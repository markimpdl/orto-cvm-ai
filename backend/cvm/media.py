"""Thin ffmpeg/ffprobe helpers."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from cvm.config import settings

H264 = "h264_videotoolbox" if sys.platform == "darwin" else "h264_nvenc"  # hardware encoder: Apple / NVIDIA


def _bin(path: str, fallback: str) -> str:
    return path if Path(path).exists() else (shutil.which(fallback) or fallback)


STALL_S = 300  # an ffmpeg that uses no CPU for this long is hung (seen with NVENC on Windows: 0 bytes out for hours)
FFMPEG_TRIES = 3


def run_ffmpeg(*args: str) -> None:
    cmd = [_bin(settings.ffmpeg_bin, "ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *args]
    for attempt in range(1, FFMPEG_TRIES + 1):
        code, err = _run_watched(cmd)
        if code is not None:
            if code != 0:
                raise RuntimeError(f"ffmpeg failed: {err[-3000:]}")
            return
        import logging

        logging.getLogger(__name__).warning("ffmpeg parado %d s sem usar CPU, tentativa %d/%d: %s",
                                            STALL_S, attempt, FFMPEG_TRIES, " ".join(cmd)[-300:])
    raise RuntimeError(f"ffmpeg travou {FFMPEG_TRIES} vezes (sem usar CPU por {STALL_S} s)")


def _run_watched(cmd: list[str]) -> tuple[int | None, str]:
    """(exit code, stderr); (None, stderr) when the process stopped using CPU for STALL_S and was killed."""
    import time

    import psutil

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace")
    last_cpu, last_move = -1.0, time.monotonic()
    while True:
        try:
            _, err = proc.communicate(timeout=15)
            return proc.returncode, err or ""
        except subprocess.TimeoutExpired:
            pass
        try:
            p = psutil.Process(proc.pid)
            cpu = sum(t.user + t.system for t in [p.cpu_times()] + [c.cpu_times() for c in p.children(recursive=True)])
        except psutil.Error:
            continue
        if cpu > last_cpu + 0.05:
            last_cpu, last_move = cpu, time.monotonic()
        elif time.monotonic() - last_move > STALL_S:
            proc.kill()
            _, err = proc.communicate()
            return None, err or ""


def probe(path: Path | str) -> dict:
    out = subprocess.run(
        [_bin(settings.ffprobe_bin, "ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def duration(path: Path | str) -> float:
    return float(probe(path)["format"]["duration"])


def to_wav(src: Path | str, dst: Path | str, sr: int = 24000) -> Path:
    run_ffmpeg("-i", str(src), "-ac", "1", "-ar", str(sr), str(dst))
    return Path(dst)


def filter_path(p) -> str:
    """A file path inside a quoted ffmpeg filter argument: forward slashes, the drive colon (D:) escaped (Windows)."""
    return Path(p).as_posix().replace(":", r"\:").replace("'", r"\'")
