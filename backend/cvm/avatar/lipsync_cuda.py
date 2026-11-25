"""Lip-sync presenters for small NVIDIA cards (hardware tiers, feature "avatar"): LatentSync 1.5 and MuseTalk 1.5
redraw only the mouth over a base video of the avatar. The owner removed MuseTalk/LatentSync on 2026-11-21 for the
Mac (LTX is better there); they come back only as tiers of machines that cannot run LTX-2.

Each engine runs from its own folder and venv under models/ (cvm/setup/tools.py: LatentSync, MuseTalk) through our
bridge scripts (cvm/setup/vendor/cvm_latentsync.py, cvm_musetalk.py), under the heavy-model lock. UNVERIFIED on CUDA
until the owner's RTX 5060 run.

Base video: the avatar's own base.mp4 when it has one (Lucas has); otherwise one is made once per avatar from its photo
(data/avatars/<id>/lipsync_base.mp4): a short loop by this machine's animation engine, else a slow zoom on the still.
"""

import logging
import os
import subprocess
from pathlib import Path

from cvm.config import settings
from cvm.media import duration

log = logging.getLogger(__name__)

ENGINES = ("latentsync", "musetalk")
FOLDERS = {"latentsync": "LatentSync", "musetalk": "MuseTalk"}
SCRIPTS = {"latentsync": "cvm_latentsync.py", "musetalk": "cvm_musetalk.py"}
BASE_NAME = "lipsync_base.mp4"
BASE_S = 8.0
# the mouth must stay closed in the base: lip-sync draws the speech, a moving mouth leaks at the jaw
BASE_PROMPT = ("The person stands still facing the camera with the mouth closed, natural blinking, slight breathing "
               "movement, static tripod camera, realistic")


def ready(engine: str | None) -> bool:
    from cvm.setup import tools

    return engine in ENGINES and tools.TOOLS[FOLDERS[engine]].ready()


def _folder(engine: str) -> Path:
    return settings.models_dir / FOLDERS[engine]


def base_video(video: Path) -> Path:
    """A video of the avatar to lip-sync over: `video` itself when it is a real clip, else a cached one made from
    the avatar's photo."""
    if video.suffix.lower() in (".mp4", ".mov") and video.exists():
        return video
    from cvm.avatar.talking import still_for

    avatar_dir = video.parent.parent if video.parent.name in ("base", "photo") else video.parent
    out = avatar_dir / BASE_NAME
    if out.exists():
        return out
    still = still_for(video) or (video if video.suffix.lower() in (".png", ".jpg", ".jpeg") and video.exists() else None)
    if still is None:
        raise FileNotFoundError(f"o avatar não tem vídeo-base nem foto ({video})")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from cvm.visuals.sources import animate_clip

        animate_clip(still, BASE_PROMPT, out, BASE_S, portrait=False, loop=True)
        if out.exists():
            return out
    except Exception as e:  # noqa: BLE001 — no animation engine here: the still with a slow zoom
        log.info("lip-sync base from the animation engine failed (%s); using a zoom on the photo", e)
    still_motion(still, out)
    return out


def still_motion(still: Path, out: Path, seconds: float = BASE_S) -> Path:
    """A 25 fps 1920x1080 clip of the photo with a very slow zoom in and out (enough for the face detector to see a
    video, and no frozen look)."""
    frames = int(seconds * 25)
    half = frames // 2
    zoom = f"if(lte(on,{half}),1+0.0004*on,1+0.0004*({frames}-on))"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-loop", "1", "-i", str(still), "-vf",
                    f"scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
                    f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=25",
                    "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   check=True, capture_output=True)
    return out


def run(engine: str, video: Path, audio: Path, out: Path) -> Path:
    """Lip-sync `audio` over the avatar's base video into `out` (mp4 with the audio)."""
    from cvm import gpu
    from cvm.setup.tools import venv_python

    if engine not in ENGINES:
        raise ValueError(f"motor de sincronia labial desconhecido: {engine!r}")
    if not ready(engine):
        raise RuntimeError(f"{FOLDERS[engine]} ainda não está instalado nesta máquina")
    base = base_video(video)
    folder = _folder(engine)
    ff = Path(settings.ffmpeg_bin)
    env = {**os.environ, "CVM_FFMPEG": str(ff), "HF_HUB_OFFLINE": "1",
           "CVM_LATENTSYNC_CONFIG": "configs/unet/stage2.yaml"}  # LatentSync 1.5 weights (256 px, 8 GB)
    if ff.parent != Path("."):
        env["CVM_FFMPEG_DIR"] = str(ff.parent)
    timeout = max(1800, int(duration(audio) * 90))
    with gpu.section():
        proc = subprocess.run([str(venv_python(folder)), SCRIPTS[engine], "--video", str(base), "--audio", str(audio),
                               "--out", str(out)], cwd=folder, env=env, capture_output=True, text=True, timeout=timeout)
    if proc.returncode or not out.exists():
        raise RuntimeError(f"{FOLDERS[engine]} falhou: {(proc.stderr or proc.stdout or '')[-800:]}")
    return out
