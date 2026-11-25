"""Sharper AI visuals: SeedVR2 3B (mflux, MLX) upscales FLUX stills to the render size (select._sharpen).

FLUX draws 768x1344 (portrait) and LTX/Wan clips come out at 480x832, so a 1080x1920 render stretches them and
they look soft and "generated". SeedVR2 is a one-step super-resolution model that stays faithful to the input
(no prompt, no new details invented). Clips are upscaled frame by frame — it is a still model in mflux, so a
clip is only upscaled when it is short (MAX_CLIP_FRAMES), and frames share one seed to keep the grain steady.

Run as `python -m cvm.visuals.upscale --in jobs.json` (one model load for a whole video); `upscale()` does that
under the heavy-model lock. Results are cached next to the source (`<name>.sr<short side>.<ext>`).
"""

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from cvm.config import settings

log = logging.getLogger(__name__)
SEED = 42
MAX_CLIP_FRAMES = 150  # ~6 s at 24 fps
REPO = "numz/SeedVR2_comfyUI"
FILES = ("seedvr2_ema_3b_fp16.safetensors", "ema_vae_fp16.safetensors")


def ready() -> bool:
    from cvm.models_registry import MLX
    from cvm.models_registry import HF_CACHE

    snap = HF_CACHE / ("models--" + REPO.replace("/", "--")) / "snapshots"
    if not (MLX and settings.upscale_ai and snap.exists()):
        return False
    return any(all((d / f).exists() for f in FILES) for d in snap.iterdir())


def target(src: Path, short: int) -> Path:
    return src.with_name(f"{src.stem}.sr{short}{src.suffix}")


def upscale(paths: list[Path], short: int) -> dict[str, str]:
    """{source path: upscaled path} for the ones that worked (cached ones included)."""
    from cvm import gpu

    jobs = [{"src": str(p), "out": str(target(p, short))} for p in paths]
    todo = [j for j in jobs if not Path(j["out"]).exists()]
    if todo:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"short": short, "jobs": todo}, f)
        with gpu.section():
            proc = subprocess.run([sys.executable, "-m", "cvm.visuals.upscale", "--in", f.name],
                                  capture_output=True, text=True, timeout=60 * 60)
        Path(f.name).unlink(missing_ok=True)
        if proc.returncode != 0:
            log.warning("upscale failed: %s", proc.stderr[-800:])
    return {j["src"]: j["out"] for j in jobs if Path(j["out"]).exists()}


def _frames(video: Path) -> tuple[int, str]:
    out = subprocess.run([str(settings.ffprobe_bin), "-v", "error", "-select_streams", "v:0", "-count_packets",
                          "-show_entries", "stream=nb_read_packets,r_frame_rate", "-of", "json", str(video)],
                         capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s.get("nb_read_packets") or 0), s.get("r_frame_rate") or "24/1"


def _run(short: int, jobs: list[dict]) -> None:
    from mflux.models.common.config import ModelConfig
    from mflux.models.seedvr2 import SeedVR2

    model = SeedVR2(model_config=ModelConfig.seedvr2_3b())
    for j in jobs:
        src, out = Path(j["src"]), Path(j["out"])
        try:
            if src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                model.generate_image(seed=SEED, image_path=src, resolution=short).save(str(out))
            else:
                n, fps = _frames(src)
                if not n or n > MAX_CLIP_FRAMES:
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    t = Path(tmp)
                    subprocess.run([str(settings.ffmpeg_bin), "-v", "error", "-i", str(src), str(t / "f%04d.png")], check=True)
                    for fr in sorted(t.glob("f*.png")):
                        model.generate_image(seed=SEED, image_path=fr, resolution=short).save(str(t / f"u{fr.name[1:]}"))
                    subprocess.run([str(settings.ffmpeg_bin), "-v", "error", "-y", "-framerate", fps, "-i", str(t / "u%04d.png"),
                                    "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out)], check=True)
            print(json.dumps({"done": str(out)}), flush=True)
        except Exception as e:  # noqa: BLE001 — one file must not sink the rest
            print(json.dumps({"failed": str(src), "error": str(e)[:300]}), file=sys.stderr, flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    spec = json.load(open(ap.parse_args().inp))
    _run(int(spec["short"]), spec["jobs"])
