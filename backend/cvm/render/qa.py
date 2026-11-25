"""Checks of a finished render.

check_render: blocking problems (wrong size/codec, no audio, too long, black screen).
analyse: one ffmpeg pass over picture and sound — black or frozen stretches, long silences and integrated
loudness — plus a 4-frame contact sheet (10/35/65/90 %) shown next to the render for the human review.
"""

import re
import subprocess
from pathlib import Path

from cvm.config import settings
from cvm.media import probe, run_ffmpeg
from cvm.models import Render
from cvm.render.compose import PROFILES


def check_render(render: Render) -> list[str]:
    if not render.path or not Path(render.path).exists():
        return ["arquivo ausente"]
    prof = PROFILES[render.profile]
    info = probe(render.path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    issues = []
    if not v:
        return ["sem vídeo"]
    if (int(v["width"]), int(v["height"])) != (prof["width"], prof["height"]):
        issues.append(f"resolução {v['width']}x{v['height']} != {prof['width']}x{prof['height']}")
    if not a:
        issues.append("sem áudio")
    dur = float(info["format"]["duration"])
    if prof.get("max_seconds") and dur > prof["max_seconds"] + 0.5:
        issues.append(f"duração {dur:.0f}s acima do limite {prof['max_seconds']}s")
    if v.get("codec_name") != "h264":
        issues.append(f"codec {v.get('codec_name')}")
    return issues


def blocking_from(a: dict) -> list[str]:
    return [f"tela preta por {a['black_s']:.1f}s"] if a["black_s"] > 2 else []


TARGET_LUFS = -14.0


def analyse(path: Path) -> dict:
    proc = subprocess.run(
        [settings.ffmpeg_bin, "-hide_banner", "-nostats", "-i", str(path),
         "-vf", "blackdetect=d=1.0:pix_th=0.10,freezedetect=n=0.003:d=4", "-af", "ebur128=framelog=quiet,silencedetect=n=-45dB:d=2",
         "-f", "null", "-"], capture_output=True, text=True)
    log = proc.stderr
    black = [float(d) for d in re.findall(r"black_duration:([\d.]+)", log)]
    frozen = [float(d) for d in re.findall(r"freeze_duration: ([\d.]+)", log)]
    silent = [float(d) for d in re.findall(r"silence_duration: ([\d.]+)", log)]
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
    return {"black_s": round(sum(black), 2), "frozen": frozen, "silences": silent,
            "lufs": float(lufs[-1]) if lufs else None}


def warnings_from(a: dict) -> list[str]:
    out = []
    if a["frozen"]:
        out.append(f"imagem parada por {max(a['frozen']):.0f}s")
    if a["silences"]:
        out.append(f"silêncio de {max(a['silences']):.0f}s")
    if a["lufs"] is not None and abs(a["lufs"] - TARGET_LUFS) > 2:
        out.append(f"volume {a['lufs']:.0f} LUFS (alvo {TARGET_LUFS:.0f})")
    return out


def contact_sheet(path: Path, out: Path) -> Path:
    dur = float(probe(path)["format"]["duration"])
    frames = []
    for i, frac in enumerate((0.10, 0.35, 0.65, 0.90)):
        f = out.with_name(f"{out.stem}_{i}.jpg")
        run_ffmpeg("-ss", f"{dur * frac:.2f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=-2:480", str(f))
        frames.append(f)
    run_ffmpeg(*sum((["-i", str(f)] for f in frames), []), "-filter_complex", "hstack=inputs=4", "-q:v", "4", str(out))
    for f in frames:
        f.unlink(missing_ok=True)
    return out
