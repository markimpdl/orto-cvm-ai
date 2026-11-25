import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
# Hugging Face weights live inside the project (models/hf) when that folder exists, so moving the folder to another
# machine takes the models along; otherwise the usual per-user cache.
_LOCAL_HF = REPO_ROOT / "models" / "hf"
_HF_DEFAULT = _LOCAL_HF if _LOCAL_HF.exists() else Path.home() / ".cache" / "huggingface" / "hub"
DEFAULT_VIDEO_LANGUAGES = ("pt-BR", "en-US", "ar-AE")


def _valid_tz(name: str | None) -> str | None:
    if not name:
        return None
    try:
        ZoneInfo(name)
        return name
    except (ValueError, OSError, KeyError):  # ZoneInfoNotFoundError is a KeyError
        return None


def system_tz() -> str:
    """This machine's IANA timezone, without extra dependencies: $TZ, the /etc/localtime link (macOS, Linux),
    /etc/timezone, else a whole-hour Etc/GMT±N from the current UTC offset (Windows), else UTC."""
    if name := _valid_tz(os.environ.get("TZ", "").lstrip(":")):
        return name
    try:
        real = os.path.realpath("/etc/localtime")
        if "/zoneinfo/" in real and (name := _valid_tz(real.split("/zoneinfo/", 1)[1])):
            return name
    except OSError:
        pass
    try:
        if name := _valid_tz(Path("/etc/timezone").read_text(encoding="utf-8").strip()):
            return name
    except OSError:
        pass
    offset = datetime.now().astimezone().utcoffset()
    hours = offset.total_seconds() / 3600 if offset is not None else 0
    if hours == 0:
        return "UTC"
    if hours == int(hours) and (name := _valid_tz(f"Etc/GMT{-int(hours):+d}")):  # Etc/GMT signs are inverted
        return name
    return "UTC"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CVM_", env_file=REPO_ROOT / ".env", extra="ignore")

    @field_validator("video_languages", mode="before")
    @classmethod
    def _split_languages(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json

                v = json.loads(v)
                return v or list(DEFAULT_VIDEO_LANGUAGES)
            v = [x.strip() for x in v.split(",") if x.strip()]
        return v or list(DEFAULT_VIDEO_LANGUAGES)  # empty (CVM_VIDEO_LANGUAGES="") = the default, never "no language"

    @field_validator("default_tz", mode="before")
    @classmethod
    def _check_tz(cls, v):
        return _valid_tz(str(v).strip()) if v else None

    data_dir: Path = REPO_ROOT / "data"
    export_dir: Path = Path.home() / "Movies" / "CVM"  # readable copy of each finished video (cvm/export_folder.py)
    models_dir: Path = REPO_ROOT / "models"
    hf_cache: Path = _HF_DEFAULT
    api_host: str = "127.0.0.1"
    api_port: int = 8790
    regions: list[str] = ["BR", "AE", "US"]
    languages: list[str] = ["pt-BR", "ar-AE", "en-US"]
    # Languages OFFERED for new channels/projects/avatars (CVM_VIDEO_LANGUAGES="pt-BR,en-US" in the public copy).
    # Existing ar-AE data keeps working; only creating/changing to a language outside the list is refused.
    video_languages: Annotated[list[str], NoDecode] = list(DEFAULT_VIDEO_LANGUAGES)
    # The studio's own timezone (CVM_DEFAULT_TZ, e.g. "America/Sao_Paulo"): daily/weekly ticks run at its local hour and
    # the calendar shows its time beside the audience's. Unset or invalid = this machine's timezone (home_tz()).
    default_tz: str | None = None

    # Free API keys (all optional; collectors skip when missing)
    youtube_api_key: str | None = None
    pexels_api_key: str | None = None
    pixabay_api_key: str | None = None
    reddit_client_id: str | None = None  # free "script" app at reddit.com/prefs/apps → votes/comments instead of RSS
    reddit_client_secret: str | None = None

    # ElevenLabs (paid by the user, 2026-11-23): voice for the channels whose prefs.voice_variant = "eleven".
    # Read from CVM_ELEVENLABS_API_KEY or ELEVENLABS_API_KEY in .env; never logged or stored in the DB.
    elevenlabs_api_key: str | None = Field(default=None, validation_alias=AliasChoices("CVM_ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY"))
    elevenlabs_reserve: int = 1500  # credits left untouched each month (tests, a retake); below it the trained voice reads

    # Twitch Helix (free app at dev.twitch.tv/console) for Em alta → Lives (cvm/trends/twitch.py); else the Keychain
    # (service "cvm-twitch"). Never stored in the DB, logged or returned.
    twitch_client_id: str | None = None
    twitch_client_secret: str | None = None
    # Login for yt-dlp when a site refuses without one (YouTube age-restricted videos, login walls): a browser name
    # ("chrome", "safari", "firefox", "chrome:Profile 2") read with --cookies-from-browser, or a cookies.txt path.
    # Used only on the retry after such a refusal (cvm/lives/platforms.login_retry); public videos never send it.
    ytdlp_cookies: str | None = None

    # Codex CLI (ChatGPT subscription, never an API key): writer while Claude is at its usage limit
    # (cvm/scripting/agents.py). codex_model: None = the CLI's own default/config.
    codex_fallback: bool = True
    codex_bin: str = "codex"
    codex_model: str | None = None

    # ffmpeg with libass/drawtext (brew install ffmpeg-full; keg-only, coexists with plain ffmpeg)
    ffmpeg_bin: str = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    ffprobe_bin: str = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
    use_whisper_captions: bool = True
    # scenes per video animated with Wan 2.2 (~15–25 min each, est.). Off until the bake-off passes on this Mac:
    # the first run was stopped for low system memory. Enable with CVM_AI_VIDEO_MAX_CLIPS=2.
    # CUDA PCs: RAM left to Windows and other programs when a model loads; a load waits for it (models_registry)
    ram_reserve_gb: float = 4.0
    # Mac: a heavy model (LTX, FLUX, voice, Gemma) starts only with this much RAM free. MLX memory is wired (never
    # swapped): an LTX-2.5 run beside other big programs filled the 48 GB and panicked the Mac (2026-11-20). 0 = off.
    gpu_min_free_gb: float = 16.0
    upscale_ai: bool = True  # SeedVR2 3B sharpens FLUX stills / short AI clips to the render size (cvm/visuals/upscale.py)
    image_engine: str = "z-image"  # AI stills: z-image (Z-Image-Turbo, Mac) | flux; falls back to flux when missing
    visual_judge: bool = True  # Gemma 3 vision anti-slop pass (cvm/visuals/judge.py)
    ai_video_max_clips: int = 4  # regular videos: hero ai_image scenes animated (LTX ~2 min each; Wan ~13 min)
    kids_max_clips: int = 100  # kids style: every scene animated (LTX-2.3 ~2 min per 5 s clip on M5 Pro; Wan ~13 min)
    presenter_engine: str = "ltx"  # talking avatar: "ltx" (LTX audio-to-video, whole body); anything else = static base
    video_engine: str = "ltx"  # image-to-video: "ltx" (LTX distilled, MLX) when installed, else "wan"
    ltx_pack: str = "2.5"  # LTX weights for clips and the presenter: "2.5" when downloaded, else (or "2.3") LTX-2.3
    # presenter on LTX-2.5: each chunk is refined by the official Refine-Details IC-LoRA (user's choice 2026-11-16;
    # measured on the M5 Pro: ~24 min more per ~6 s chunk). Off = the plain 2.5 take.
    presenter_refine: bool = True
    ai_video_steps: int = 20
    allow_static_avatar: bool = False  # show a non-talking avatar when no lipsync engine is installed
    music_dir: Path = REPO_ROOT / "data" / "music"
    # background music after the user's own folders, on channels with prefs.music on: "acestep" (own tracks made
    # locally; chosen by the user 2026-11-15 after approving the 7 style samples), "openverse" (free CC search), "none"
    music_source: str = "acestep"
    auto_music: bool = True  # search free CC music (Openverse) when the music folders are empty

    # Publishing
    youtube_privacy: str = "private"  # unaudited Google projects can only upload private videos
    public_base_url: str | None = None  # public tunnel to this API, required by Instagram's API
    # YouTube Analytics (cvm/publish/analytics.py): free OAuth "Desktop app" client; else data/secrets/youtube_client_secret.json
    google_client_id: str | None = None
    google_client_secret: str | None = None

    # Claude Code (subscription) — invoked as a CLI, never via API key
    claude_bin: str = "claude"
    claude_isolated: bool = True  # pipeline `claude -p` runs load only the project's settings (claude_runner.isolation_args)
    # pipeline models per step (claude_runner.isolation_args): scripts need the strongest writer (3 languages, Gulf
    # Arabic, QA pass); SEO and ideas are lighter work. None = the account default.
    claude_model: str | None = "opus"
    claude_model_light: str | None = "sonnet"
    claude_auto_run: bool = True  # True = the worker runs script/SEO steps with `claude -p`; False = "Rodar no Claude" button

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cvm.sqlite"

    @property
    def queue_path(self) -> Path:
        return self.data_dir / "queue.sqlite"


settings = Settings()


def home_tz() -> ZoneInfo:
    """The studio's timezone: settings.default_tz (CVM_DEFAULT_TZ), else the system's."""
    return ZoneInfo(_valid_tz(settings.default_tz) or system_tz())
# huggingface_hub/transformers (and the model subprocesses, which inherit it) read the cache from here
os.environ.setdefault("HF_HUB_CACHE", str(settings.hf_cache))


def key(name: str) -> str | None:
    """A free API key: .env first, then the keystore (Keychain / keyring) filled by the install wizard."""
    from cvm import keystore

    return getattr(settings, name, None) or keystore.get(name)
