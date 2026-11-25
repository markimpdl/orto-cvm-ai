"""Make the avatar's base video speak the narration.

Engines:
- "ltx-a2v": the whole presenter generated from the speech by LTX audio-to-video (cvm/avatar/talking.py), on the Mac.
- On NVIDIA cards, the avatar tier of this machine (cvm/setup/requirements.py, picked by the wizard's engine test):
  "ltx2-a2v" (official LTX-2 in FP8 with RAM offload, cvm/avatar/ltx2_cuda.py), "latentsync" or "musetalk"
  (mouth-only lip-sync over a base video, cvm/avatar/lipsync_cuda.py — back only for small cards, 2026-11-25).
- "static": no lip movement — loops the base video (or the still) under the audio, only when no engine is installed
  or the avatar has no still; the composer prefers B-roll in that case.
A failed take fails the step (visible on "Sua vez", retry from there) instead of silently falling back to a worse
presenter.
"""

import logging
from pathlib import Path

from cvm.config import settings
from cvm.media import duration, run_ffmpeg

log = logging.getLogger(__name__)
CUDA_ENGINES = ("ltx2-a2v", "latentsync", "musetalk")


def static(video: Path, audio: Path, out: Path) -> Path:
    src = ["-stream_loop", "-1", "-i", str(video)] if video.suffix == ".mp4" else ["-loop", "1", "-i", str(video)]
    run_ffmpeg(*src, "-i", str(audio), "-map", "0:v", "-map", "1:a", "-t", f"{duration(audio):.3f}",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out))
    return out


def cuda_engine(video: Path) -> str | None:
    """NVIDIA cards: the presenter engine of this machine's avatar tier, when it is installed; None otherwise."""
    from cvm import engines
    from cvm.avatar import lipsync_cuda, ltx2_cuda
    from cvm.avatar.talking import still_for

    tier = engines.feature_engine("avatar")
    if tier == "ltx2-q" and still_for(video) and ltx2_cuda.ready():
        return "ltx2-a2v"
    if tier in lipsync_cuda.ENGINES and lipsync_cuda.ready(tier):
        return tier
    return None


def wanted_engine(video: Path) -> str:
    """The engine lipsync() would use now for this avatar (cached results made by another engine are stale)."""
    from cvm.models_registry import MLX

    if not MLX:
        return cuda_engine(video) or "static"
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


def lipsync(video: Path, audio: Path, out: Path, engine: str | None = None) -> tuple[Path, str]:
    """`engine` forces one of CUDA_ENGINES (the wizard's engine test); else this machine's own."""
    from cvm.models_registry import MLX

    if not MLX or engine in CUDA_ENGINES:
        from cvm.avatar import lipsync_cuda, ltx2_cuda
        from cvm.avatar.talking import still_for

        eng = engine or cuda_engine(video)
        try:
            if eng == "ltx2-a2v":
                still = still_for(video)
                if still is None:
                    raise FileNotFoundError("o avatar não tem foto para o LTX-2")
                return ltx2_cuda.talk(still, audio, out), eng
            if eng in lipsync_cuda.ENGINES:
                return lipsync_cuda.run(eng, video, audio, out), eng
        except Exception as e:  # noqa: BLE001
            log.exception("%s presenter failed", eng)
            raise RuntimeError(f"apresentador {eng} falhou ({e}); tente de novo") from e
        return static(video, audio, out), "static"
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
