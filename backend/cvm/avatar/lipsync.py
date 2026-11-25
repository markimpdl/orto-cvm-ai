"""Make the avatar's base video speak the narration.

Engines:
- "ltx-a2v": the whole presenter generated from the speech by LTX audio-to-video (cvm/avatar/talking.py).
- "static": no lip movement — loops the base video (or the still) under the audio, only when LTX is not installed
  or the avatar has no still; the composer prefers B-roll in that case.
MuseTalk/LatentSync were removed (user, 2026-11-21: not used). A failed LTX take
now fails the step (visible on "Sua vez", retry from there) instead of silently falling back to a worse presenter.
"""

import logging
from pathlib import Path

from cvm.config import settings
from cvm.media import duration, run_ffmpeg

log = logging.getLogger(__name__)


def static(video: Path, audio: Path, out: Path) -> Path:
    src = ["-stream_loop", "-1", "-i", str(video)] if video.suffix == ".mp4" else ["-loop", "1", "-i", str(video)]
    run_ffmpeg(*src, "-i", str(audio), "-map", "0:v", "-map", "1:a", "-t", f"{duration(audio):.3f}",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out))
    return out


def wanted_engine(video: Path) -> str:
    """The engine lipsync() would use now for this avatar (cached results made by another engine are stale)."""
    if settings.presenter_engine == "ltx":
        from cvm.avatar.talking import still_for
        from cvm.visuals.sources import ltx_ready

        if still_for(video) and ltx_ready():
            return "ltx-a2v"
    return "static"


def cached(target: Path, marker: Path, audio: Path, video: Path) -> str | None:
    """Engine of a reusable cached result: newer than the audio and the avatar's base video, made by the engine
    that would be used now (a "static" fallback is kept only when that is all there is)."""
    if not (target.exists() and marker.exists()):
        return None
    t = target.stat().st_mtime
    if t < audio.stat().st_mtime or (video.exists() and t < video.stat().st_mtime):
        return None
    engine = marker.read_text().strip()
    return engine if engine == wanted_engine(video) else None


def lipsync(video: Path, audio: Path, out: Path) -> tuple[Path, str]:
    if settings.presenter_engine == "ltx":  # the whole presenter generated from the speech (see talking.py)
        from cvm.avatar.talking import ltx_talk, still_for
        from cvm.visuals.sources import ltx_ready

        still = still_for(video)
        if still and ltx_ready():
            try:
                return ltx_talk(still, audio, out), "ltx-a2v"
            except Exception as e:  # noqa: BLE001
                log.exception("ltx talking presenter failed")
                raise RuntimeError(f"apresentador LTX falhou ({e}); tente de novo") from e
    return static(video, audio, out), "static"
