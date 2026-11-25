"""Install wizard: the person's first channel (language, publish defaults), its brand art and a bumper preview.

The bumper preview runs in a detached process (`python -m cvm.setup.channel bumper <id>`): ffmpeg and, with a prompt,
one light AI call that turns the prompt into render params. Its state lives in Setting `bumper_preview:<id>`
{"state": running | ready | failed, "params", "error", "prompt", "pid", "pid_created", "run", "started"}; the file is
data/channels/<id>/vinheta/preview.mp4, served by GET /setup/channel/{id}/bumper.mp4.
"""
import io
import json
import logging
import re
import subprocess
import sys
import time
import uuid

import psutil
from PIL import Image, UnidentifiedImageError
from sqlmodel import Session, select

from cvm.config import REPO_ROOT, settings
from cvm.db import engine
from cvm.models import Avatar, Channel, Platform, Setting

log = logging.getLogger(__name__)

LANGUAGES = ("pt-BR", "en-US")  # the first channel; Arabic stays the owner's (voices, RTL captions)
PUBLISH_DEFAULTS = {"notify_subscribers": False, "paid_promotion": False, "auto_chapters": False,
                    "auto_places": False, "synthetic": "auto"}
SYNTHETIC = ("auto", "yes", "no")
DEFAULT_PLATFORMS = ["youtube", "youtube_shorts"]
MAX_UPLOAD = 15 * 1024 * 1024
LOGO_MIN = 400
MAX_PIXELS = 40_000_000  # refused before decoding (a tiny PNG may declare a huge picture)
BANNER_SIZE = (2048, 1152)  # 16:9, YouTube's banner width

VARIANTS = ("pop", "slide", "glitch", "zoom")
SOUNDS = ("mix", "whoosh", "impact", "shimmer", "none")
COLOUR_KEYS = ("ring", "glow")
DEFAULT_PARAMS = {"variant": "pop", "colours": {}, "sound": "mix"}
PREVIEW_SIZE = (1280, 720, 30)
# the prompt → params call needs no tool at all (the person's words are data, never instructions to act on)
NO_TOOLS = ["mcp__cvm__*", "Agent", "Bash", "Write", "Edit", "NotebookEdit", "Skill", "WebFetch", "WebSearch",
            "Read", "Glob", "Grep"]
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class BrandError(ValueError):
    """An uploaded image the wizard cannot use (message in pt-BR)."""


# ---------- channel ----------

def create(session: Session, data: dict) -> Channel:
    """data: name, language (pt-BR | en-US), brief, avatar_id (absent = Lucas when installed; None = no avatar),
    platforms, made_for_kids (False), bumper (True), publish {notify_subscribers, paid_promotion, auto_chapters,
    auto_places, synthetic: auto | yes | no}."""
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Dê um nome ao canal.")
    language = data.get("language") or "pt-BR"
    if language not in LANGUAGES:
        raise ValueError("O primeiro canal pode ser em pt-BR ou en-US.")
    publish = dict(PUBLISH_DEFAULTS)
    for k, v in (data.get("publish") or {}).items():
        if k == "synthetic":
            if v not in SYNTHETIC:
                raise ValueError("Conteúdo sintético: auto, yes ou no.")
            publish[k] = v
        elif k in PUBLISH_DEFAULTS:
            publish[k] = bool(v)
    if "avatar_id" in data:
        avatar_id = data["avatar_id"]
        if avatar_id is not None and not session.get(Avatar, avatar_id):
            raise ValueError("Esse avatar não existe.")
    else:  # the ready-made Lucas (cvm/setup/lucas.py) when the person kept it
        lucas = session.exec(select(Avatar).where(Avatar.name == "Lucas").order_by(Avatar.id)).first()
        avatar_id = lucas.id if lucas else None
    allowed = {p.value for p in Platform}
    platforms = [p for p in (data.get("platforms") or DEFAULT_PLATFORMS) if p in allowed] or DEFAULT_PLATFORMS
    channel = Channel(
        name=name, platform=Platform.youtube, language=language, brief=(data.get("brief") or None),
        avatar_id=avatar_id, made_for_kids=bool(data.get("made_for_kids", False)), status="active",
        prefs={"platforms": platforms, "avatar_id": avatar_id, "no_avatar": avatar_id is None,
               "bumper_mode": "short" if data.get("bumper", True) else "none",
               "publish_defaults": publish},
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


# ---------- brand ----------

def _open(raw: bytes, what: str) -> Image.Image:
    if len(raw) > MAX_UPLOAD:
        raise BrandError(f"O {what} passa de 15 MB.")
    too_big = BrandError(f"O {what} é grande demais (mais de 40 megapixels). Envie uma versão menor.")
    try:
        img = Image.open(io.BytesIO(raw))
        if img.width * img.height > MAX_PIXELS:
            raise too_big
        img.load()
    except Image.DecompressionBombError as e:
        raise too_big from e
    except (UnidentifiedImageError, OSError) as e:
        raise BrandError(f"O {what} não é uma imagem que dê para abrir (use PNG ou JPG).") from e
    return img.convert("RGBA")


def _crop(img: Image.Image, ratio: float) -> Image.Image:
    w, h = img.size
    if w / h > ratio:
        nw = round(h * ratio)
        return img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    nh = round(w / ratio)
    return img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))


def brand_dir(channel_id: int):
    return settings.data_dir / "channels" / str(channel_id) / "brand"


def save_brand(channel_id: int, logo: bytes | None = None, banner: bytes | None = None) -> dict:
    """Logo: centre square, at least 400 px (never upscaled). Banner: centre 16:9. Both saved as PNG.
    Everything is checked before anything is written."""
    out = {}
    if logo is not None:
        img = _crop(_open(logo, "logo"), 1.0)
        if img.width < LOGO_MIN:
            raise BrandError(f"O logo precisa ter pelo menos {LOGO_MIN} × {LOGO_MIN} px (o seu tem "
                             f"{img.width} px no lado menor). Envie uma versão maior.")
        out["logo"] = img
    if banner is not None:
        img = _crop(_open(banner, "banner"), 16 / 9)
        out["banner"] = img.resize(BANNER_SIZE, Image.LANCZOS) if img.width > BANNER_SIZE[0] else img
    folder = brand_dir(channel_id)
    folder.mkdir(parents=True, exist_ok=True)
    saved = {}
    for name, img in out.items():
        path = folder / f"{name}.png"
        tmp = path.with_suffix(".tmp.png")
        img.save(tmp, "PNG")
        tmp.replace(path)
        saved[name] = {"width": img.width, "height": img.height}
    return saved


# ---------- bumper preview ----------

def _key(channel_id: int) -> str:
    return f"bumper_preview:{channel_id}"


def preview_path(channel_id: int):
    return settings.data_dir / "channels" / str(channel_id) / "vinheta" / "preview.mp4"


def parse_params(text: str) -> dict:
    """The AI's answer → render params. Unknown keys dropped, values outside the sets → defaults, colours #RRGGBB."""
    data = None
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return {**DEFAULT_PARAMS, "colours": {}}
    colours = data.get("colours") if isinstance(data.get("colours"), dict) else {}
    return {"variant": data.get("variant") if data.get("variant") in VARIANTS else DEFAULT_PARAMS["variant"],
            "colours": {k: v for k, v in colours.items() if k in COLOUR_KEYS and isinstance(v, str) and _HEX.match(v)},
            "sound": data.get("sound") if data.get("sound") in SOUNDS else DEFAULT_PARAMS["sound"]}


PROMPT = """Turn this description of a short channel intro (logo animation, under 3 seconds) into JSON. Answer with
the JSON object only, no other text.
Keys:
- "variant": how the logo enters: "pop" (bounces in), "slide" (glides in from the side), "glitch" (digital jitter),
  "zoom" (falls from very big).
- "colours": {"ring": "#RRGGBB" (ring around the logo), "glow": "#RRGGBB" (light behind it)}; leave out what the
  description does not suggest.
- "sound": "mix" (whoosh + impact + shimmer), "whoosh", "impact", "shimmer" or "none".
Description: """


RUN_MAX_S = 600  # a preview still "running" after this is stuck (hung ffmpeg or AI call): it is stopped
NO_PID_MAX_S = 30  # "running" without a pid this long: the API died before it could record the process


def _created(pid: int) -> float | None:
    """When the process started (psutil), recorded beside its pid: a reused pid has another start time."""
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, ValueError):
        return None


def _process(pid: int | None, created: float | None) -> psutil.Process | None:
    """Our bumper child, alive: same pid AND same start time. Anything else counts as dead and is never touched
    (os.kill(pid, 0) is not used as a probe: on Windows it sends CTRL_C_EVENT)."""
    if not pid or created is None:
        return None
    try:
        proc = psutil.Process(pid)
        return proc if abs(proc.create_time() - float(created)) < 0.01 else None
    except (psutil.Error, ValueError, TypeError):
        return None


def _kill(proc: psutil.Process) -> None:
    try:
        for child in proc.children(recursive=True):  # ffmpeg, claude -p
            child.kill()
        proc.kill()
    except psutil.Error:
        pass


def _read(session: Session, channel_id: int) -> dict:
    """A fresh read (another process may have written since this session last looked)."""
    session.expire_all()
    row = session.get(Setting, _key(channel_id))
    return dict(row.value) if row else {}


def _write(session: Session, channel_id: int, value: dict) -> dict:
    row = session.get(Setting, _key(channel_id))
    if row:
        row.value = value
    else:
        row = Setting(key=_key(channel_id), value=value)
    session.add(row)
    session.commit()
    return value


def status(session: Session, channel_id: int) -> dict:
    st = _read(session, channel_id) or {"state": "idle"}
    if st.get("state") == "running":
        age = time.time() - float(st.get("started") or 0)
        error = None
        proc = _process(st.get("pid"), st.get("pid_created")) if st.get("pid") else None
        if st.get("pid") and proc is None:  # gone, or its pid now belongs to another process
            error = "a vinheta parou no meio; tente de novo"
        elif not st.get("pid") and age > NO_PID_MAX_S:
            error = "a vinheta não chegou a começar; tente de novo"
        elif age > RUN_MAX_S:
            if proc is not None:
                _kill(proc)
            error = "a vinheta demorou demais e foi interrompida; tente de novo"
        if error:
            st = _write(session, channel_id, {**st, "state": "failed", "error": error})
    if st.get("state") == "ready" and preview_path(channel_id).exists():
        st["url"] = f"/setup/channel/{channel_id}/bumper.mp4?v={int(preview_path(channel_id).stat().st_mtime)}"
    return {k: v for k, v in st.items() if k not in ("pid", "pid_created", "run", "started")}


def start_bumper(session: Session, channel: Channel, prompt: str | None) -> dict:
    """Starts the preview in its own process; a second call while one runs just returns its state."""
    current = status(session, channel.id)
    if current.get("state") == "running":
        return current
    if not (brand_dir(channel.id) / "logo.png").exists():
        raise BrandError("Envie o logo do canal antes de gerar a vinheta.")
    prompt = (prompt or "").strip()[:500] or None
    run = uuid.uuid4().hex
    _write(session, channel.id, {"state": "running", "prompt": prompt, "params": None, "error": None,
                                 "started": time.time(), "run": run})
    logf = settings.data_dir / "logs" / f"bumper-preview-{channel.id}.log"
    logf.parent.mkdir(parents=True, exist_ok=True)
    try:
        with logf.open("w") as fh:  # the child keeps its own copy of the handle
            proc = subprocess.Popen([sys.executable, "-m", "cvm.setup.channel", "bumper", str(channel.id)],
                                    cwd=REPO_ROOT / "backend", stdout=fh, stderr=subprocess.STDOUT,
                                    start_new_session=True)
    except OSError as e:
        _write(session, channel.id, {**_read(session, channel.id), "state": "failed",
                                     "error": f"não deu para iniciar a vinheta: {e}"})
        return status(session, channel.id)
    created = _created(proc.pid)  # None when the child already exited: status() then sees it as gone
    st = _read(session, channel.id)  # a fast child may already have written ready/failed: keep that
    if st.get("run") == run and st.get("state") == "running" and not st.get("pid"):
        _write(session, channel.id, {**st, "pid": proc.pid, "pid_created": created})
    return status(session, channel.id)


def make_bumper(channel_id: int) -> None:
    """The detached process: prompt → params (one light AI call, no MCP, no tools), then the short bumper."""
    from cvm.render import bumper_brand

    with Session(engine) as session:
        st = _read(session, channel_id)
        run = st.get("run")
        try:
            params = {**DEFAULT_PARAMS, "colours": {}}
            if st.get("prompt"):
                from cvm.scripting import agents

                try:
                    res = agents.run_light(PROMPT + st["prompt"], "bumper", timeout=300, mcp="empty",
                                           disallowed_tools=NO_TOOLS)
                    params = parse_params(str(res.get("result") or ""))
                except Exception as e:  # noqa: BLE001 — no AI: the default bumper is still a preview
                    log.warning("bumper prompt for channel %s: %s", channel_id, e)
            logo = brand_dir(channel_id) / "logo.png"
            banner = brand_dir(channel_id) / "banner.png"
            out = preview_path(channel_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            part = out.with_name("preview.partial.mp4")
            W, H, fps = PREVIEW_SIZE
            from cvm.render.bumper import SHORT_S

            bumper_brand.render_short(logo, banner if banner.exists() else None, W, H, fps, part, SHORT_S,
                                      colours=params["colours"], variant=params["variant"], sound=params["sound"])
            part.replace(out)
            result = {"state": "ready", "params": params, "error": None}
        except Exception as e:
            log.exception("bumper preview for channel %s", channel_id)
            result = {"state": "failed", "error": str(e)[:300]}
        fresh = _read(session, channel_id)  # the parent may have added the pid meanwhile
        if fresh.get("run") != run:  # a newer preview took over (this one was stopped as stuck): leave it
            return
        _write(session, channel_id, {**fresh, **result})


def approve_bumper(session: Session, channel: Channel) -> dict:
    """The previewed params become the channel's bumper (prefs.bumper_params, read by bumper.short_bumper)."""
    st = status(session, channel.id)
    if st.get("state") != "ready" or not st.get("params"):
        raise LookupError("Não há vinheta pronta para aprovar.")
    channel.prefs = {**(channel.prefs or {}), "bumper_params": st["params"]}
    session.add(channel)
    session.commit()
    return st["params"]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) == 3 and sys.argv[1] == "bumper":
        make_bumper(int(sys.argv[2]))
