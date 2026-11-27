"""Talking presenter on NVIDIA cards with the official LTX-2 (tier "ltx2-q", feature "avatar"): the same model family
and look as the Mac's LTX presenter (cvm/avatar/talking.py), run by Lightricks' own PyTorch pipeline.

`python -m ltx_pipelines.a2vid_two_stage` (github.com/Lightricks/LTX-2, A2VidPipelineTwoStage) keeps the given audio
frozen and generates only the video, conditioned on the avatar's photo. To fit an 8–16 GB card the transformer is
cast to FP8 on load (`--quantization fp8-cast`) and its weights stay in RAM, streamed to the card layer by layer
(`--offload cpu`). Weights: models_registry "ltx2.3-dev" (dev checkpoint + distilled LoRA + x2 upscaler) and
"gemma3-qat" (Gemma 3 text encoder; Lightricks' ungated copy of Google's gated repo, same files).

Long narrations are cut at pauses like the Mac presenter (talking.cut_points) and each chunk starts on the previous
chunk's last frame. UNVERIFIED on CUDA until the owner's RTX 5060 run (VRAM/RAM peak, time per chunk); the wizard's
engine test falls to LatentSync when it fails.
"""

import logging
import os
import subprocess
from pathlib import Path

from cvm.config import settings

log = logging.getLogger(__name__)

FPS, W, H = 24, 1024, 576  # divisible by 32
CHECKPOINT = "ltx-2.3-22b-dev.safetensors"
LORA = "ltx-2.3-22b-distilled-lora-384-1.1.safetensors"
UPSAMPLER = "ltx-2.3-spatial-upscaler-x2-1.1.safetensors"
LORA_STRENGTH = "0.8"


def _snapshot(model: str, need: str | None = None) -> Path | None:
    """The HF cache snapshot of a registry model (holding `need`, when given)."""
    from cvm.models_registry import HF_CACHE, spec

    s = spec(model)
    if not s:
        return None
    snaps = HF_CACHE / ("models--" + s["repo"].replace("/", "--")) / "snapshots"
    for snap in sorted(snaps.glob("*")) if snaps.exists() else []:
        if need is None or (snap / need).exists():
            return snap
    return None


def ready() -> bool:
    from cvm.models_registry import is_ready
    from cvm.setup import tools

    return tools.TOOLS["LTX-2"].ready() and is_ready("ltx2.3-dev") and is_ready("gemma3-qat")


def command(seg: Path, anchor: Path, prompt: str, seconds: float, out: Path, seed: int = 42) -> list[str]:
    from cvm.setup.tools import venv_python

    ckpt = _snapshot("ltx2.3-dev", CHECKPOINT)
    gemma = _snapshot("gemma3-qat")
    if ckpt is None or gemma is None:
        raise RuntimeError("LTX-2 ou Gemma 3 ainda não foram baixados nesta máquina")
    return [str(venv_python(settings.models_dir / "LTX-2")), "-m", "ltx_pipelines.a2vid_two_stage",
            "--checkpoint-path", str(ckpt / CHECKPOINT), "--distilled-lora", str(ckpt / LORA), LORA_STRENGTH,
            "--spatial-upsampler-path", str(ckpt / UPSAMPLER), "--gemma-root", str(gemma),
            "--audio-path", str(seg), "--audio-max-duration", f"{seconds:.3f}",
            "--image", str(anchor), "0", "1.0", "--prompt", prompt,
            "--height", str(H), "--width", str(W), "--frame-rate", str(FPS), "--seed", str(seed),
            "--quantization", "fp8-cast", "--offload", "cpu", "--output-path", str(out)]


def talk(still: Path, audio: Path, out: Path, prompt: str | None = None, timeout: int = 3 * 3600) -> Path:
    from cvm import gpu, progress
    from cvm.avatar import talking

    prompt = prompt or talking.PROMPT
    ff = settings.ffmpeg_bin
    work = out.parent / f"{out.stem}_ltx2"
    # chunks are cached by index: only for the same audio and identity still
    import hashlib
    import shutil

    stamp = hashlib.sha1(audio.read_bytes() + str(still).encode()).hexdigest()
    if work.exists() and (work / "stamp").exists() and (work / "stamp").read_text() != stamp:
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    (work / "stamp").write_text(stamp)
    bounds = talking.cut_points(audio)
    anchor, parts = still, []
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"}
    chunks = len(bounds) - 1
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        progress.report(i, chunks, f"apresentador falando (LTX-2, placa pequena): trecho {i + 1}/{chunks}")
        seg, raw, part = work / f"seg_{i:02d}.wav", work / f"raw_{i:02d}.mp4", work / f"part_{i:02d}.mp4"
        subprocess.run([ff, "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(audio),
                        "-af", "apad=pad_dur=1", str(seg)], check=True, capture_output=True)
        if not raw.exists():
            with gpu.section():
                proc = subprocess.run(command(seg, anchor, prompt, b - a, raw), cwd=settings.models_dir / "LTX-2",
                                      env=env, capture_output=True, text=True, timeout=timeout)
            if proc.returncode or not raw.exists():
                raise RuntimeError(f"LTX-2 falhou no trecho {i + 1}: {(proc.stderr or proc.stdout or '')[-800:]}")
        talking._encode_part(raw, b - a, part)
        nxt = work / f"anchor_{i:02d}.png"
        subprocess.run([ff, "-v", "error", "-y", "-sseof", "-0.1", "-i", str(raw), "-frames:v", "1", "-update", "1",
                        str(nxt)], check=True, capture_output=True)
        anchor = nxt if nxt.exists() else still
        parts.append(part)
    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
    subprocess.run([ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", str(out)],
                   check=True, capture_output=True)
    return out
