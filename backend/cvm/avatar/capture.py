"""Learning an avatar's image and voice from uploaded recordings."""

from pathlib import Path

from cvm.media import duration, probe, run_ffmpeg, to_wav
from cvm.paths import avatar_dir


def validate_base_video(path: Path) -> list[str]:
    """Basic quality checks; returns a list of problems (empty = ok)."""
    problems = []
    info = probe(path)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if not video:
        return ["arquivo sem vídeo"]
    w, h = int(video["width"]), int(video["height"])
    if min(w, h) < 720:
        problems.append(f"resolução baixa ({w}x{h}); grave em 1080p ou mais")
    d = duration(path)
    if d < 20:
        problems.append(f"vídeo curto ({d:.0f}s); grave pelo menos 30–60 s")
    return problems


def picture_start(path: Path) -> float:
    """Timestamp to seek to (-ss) to skip black frames at the very start of a video (0 if there are none).
    Absolute, because browser recordings often start at a non-zero timestamp."""
    import re
    import subprocess

    from cvm.config import settings
    from cvm.media import _bin

    log = subprocess.run([_bin(settings.ffmpeg_bin, "ffmpeg"), "-hide_banner", "-t", "3", "-i", str(path),
                          "-vf", "blackdetect=d=0.01:pix_th=0.10", "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.search(r"black_start:([\d.]+) black_end:([\d.]+)", log)
    video = next(st for st in probe(path)["streams"] if st["codec_type"] == "video")
    start = float(video.get("start_time", 0) or 0)  # blackdetect reports stream timestamps
    return float(m.group(2)) + 0.04 if m and float(m.group(1)) - start < 0.1 else 0.0


def save_base_video(avatar_id: int, upload: Path) -> tuple[Path, list[str]]:
    """Normalize to 25 fps H.264 (what lipsync models expect) and extract a reference still."""
    d = avatar_dir(avatar_id) / "base"
    d.mkdir(exist_ok=True)
    out = d / "base.mp4"
    skip = picture_start(upload)  # browser recordings often start with a few black frames (breaks face detection)
    run_ffmpeg("-ss", f"{skip:.3f}", "-i", str(upload), "-vf", "fps=25", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out))
    run_ffmpeg("-ss", "1", "-i", str(out), "-frames:v", "1", str(d / "still.jpg"))
    return out, validate_base_video(out)


REF_TARGET, REF_MIN, REF_MAX = 12.0, 8.0, 15.0  # Chatterbox conditions on ~10 s; longer only slows it down


def silences(path: Path, noise_db: int = -35, min_len: float = 0.18) -> list[tuple[float, float]]:
    """(start, end) of pauses, via ffmpeg silencedetect."""
    import re
    import subprocess

    from cvm.config import settings
    from cvm.media import _bin

    proc = subprocess.run([_bin(settings.ffmpeg_bin, "ffmpeg"), "-hide_banner", "-i", str(path), "-af",
                           f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    return list(zip(starts, ends + [duration(path)] * (len(starts) - len(ends))))


def pick_reference_window(pauses: list[tuple[float, float]], total: float) -> tuple[float, float]:
    """A ~12 s stretch of speech that starts and ends on natural pauses (no cut words)."""
    start = pauses[0][1] if pauses and pauses[0][0] <= 0.05 else 0.0
    start = max(0.0, start - 0.15)
    ends = [s for s, _ in pauses if REF_MIN <= s - start <= REF_MAX]
    end = min(ends, key=lambda s: abs(s - start - REF_TARGET)) + 0.1 if ends else start + REF_TARGET
    return start, min(end, total)


def save_voice_sample(avatar_id: int, language: str, upload: Path) -> tuple[Path, str | None]:
    """Keeps the full reading (raw_<lang>.wav, for future fine-tuning) and cuts the cloning reference from it.

    Returns (reference wav, transcript of the reference or None when whisper isn't available).
    """
    d = avatar_dir(avatar_id) / "voice"
    d.mkdir(exist_ok=True)
    raw, out = d / f"raw_{language}.wav", d / f"ref_{language}.wav"
    tmp = d / f"_tmp_{language}.wav"
    to_wav(upload, tmp)
    run_ffmpeg("-i", str(tmp), "-af", "loudnorm=I=-20:TP=-2", "-ar", "24000", str(raw))
    tmp.unlink(missing_ok=True)
    start, end = pick_reference_window(silences(raw), duration(raw))
    run_ffmpeg("-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(raw), "-af", "afade=t=in:d=0.05,areverse,afade=t=in:d=0.08,areverse", str(out))
    return out, transcribe(out, language)


def transcribe(path: Path, language: str) -> str | None:
    from cvm.models_registry import is_ready

    if not is_ready("whisper"):
        return None
    try:
        from cvm.render.captions import words_from_whisper

        return " ".join(w["w"] for w in words_from_whisper(path, language)).strip() or None
    except Exception:  # noqa: BLE001
        return None
