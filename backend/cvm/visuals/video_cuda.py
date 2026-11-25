"""Image-to-video on NVIDIA cards, one engine per hardware tier (cvm/setup/requirements.py, feature "animation"):

- "ltxv-2b": LTX-Video 2B (0.9.x, diffusers layout, LTXImageToVideoPipeline) — 8 GB cards, offloaded to RAM;
- "wan-5b": Wan 2.2 TI2V-5B (diffusers, WanImageToVideoPipeline) — 12–16 GB, offloaded to RAM;
- "ltx2-cuda": LTX-2 on CUDA for clips — not wired yet (part 1B): raises NotReady.

480p-class clips like the Mac engines (the composer scales them). Written against the public diffusers pipelines
(diffusers ≥ 0.35 for Wan 2.2); UNVERIFIED on CUDA until the owner's RTX 5060 run — the wizard's engine test
(cvm/setup/engine_test.py) falls to the next tier on an out-of-memory error.
"""

from pathlib import Path

from cvm.models_registry import cuda_loader

ENGINES = ("ltx2-cuda", "wan-5b", "ltxv-2b")
LABELS = {"ltxv-2b": "ltx-video-2b", "wan-5b": "wan2.2-ti2v-5b", "ltx2-cuda": "ltx-2"}
MODELS = {"ltxv-2b": "ltxv-2b", "wan-5b": "wan-ti2v-5b"}
FPS = 24
NEGATIVE = "worst quality, inconsistent motion, blurry, jittery, distorted, deformed, watermark, text"


class NotReady(RuntimeError):
    """The engine exists as a tier but has no backend on this machine yet."""


def ready(engine: str | None) -> bool:
    from cvm.models_registry import is_ready

    return engine in MODELS and is_ready(MODELS[engine])


def size(portrait: bool) -> tuple[int, int]:
    return (480, 832) if portrait else (832, 480)  # both divisible by 32 (LTX) and 16 (Wan)


def ltx_frames(seconds: float) -> int:
    return max(25, min(121, (int(seconds * FPS) + 7) // 8 * 8 + 1))  # LTX: 8k+1


@cuda_loader("video")
def _ltxv():
    import torch
    from diffusers import LTXImageToVideoPipeline

    from cvm.models_registry import repo

    pipe = LTXImageToVideoPipeline.from_pretrained(repo("ltxv-2b"), torch_dtype=torch.bfloat16, local_files_only=True)
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()
    return pipe


@cuda_loader("video")
def _wan():
    import torch
    from diffusers import AutoencoderKLWan, WanImageToVideoPipeline

    from cvm.models_registry import repo

    r = repo("wan-ti2v-5b")
    vae = AutoencoderKLWan.from_pretrained(r, subfolder="vae", torch_dtype=torch.float32, local_files_only=True)
    pipe = WanImageToVideoPipeline.from_pretrained(r, vae=vae, torch_dtype=torch.bfloat16, local_files_only=True)
    pipe.enable_model_cpu_offload()
    return pipe


def clip(engine: str | None, image: Path, prompt: str, out: Path, seconds: float, portrait: bool = True,
         seed: int = 42, steps: int | None = None) -> Path:
    """Animate a still into `out` (mp4, no sound) with this machine's engine, under the heavy-model lock."""
    import torch
    from diffusers.utils import export_to_video
    from PIL import Image

    from cvm import gpu

    if engine == "ltx2-cuda":
        raise NotReady("LTX-2 para clipes no Windows ainda não foi ligado (parte 1B)")
    if engine not in MODELS:
        raise NotReady(f"motor de animação desconhecido: {engine!r}")
    if not ready(engine):
        raise NotReady(f"{LABELS[engine]} ainda não foi baixado nesta máquina")
    w, h = size(portrait)
    still = Image.open(image).convert("RGB").resize((w, h))
    gen = torch.Generator("cpu").manual_seed(seed)
    with gpu.section():
        if engine == "ltxv-2b":
            frames = _ltxv()(image=still, prompt=prompt, negative_prompt=NEGATIVE, width=w, height=h,
                             num_frames=ltx_frames(seconds), num_inference_steps=steps or 30, generator=gen).frames[0]
        else:
            from cvm.visuals.sources import wan_frames

            frames = _wan()(image=still, prompt=prompt, negative_prompt=NEGATIVE, width=w, height=h,
                            num_frames=wan_frames(seconds), num_inference_steps=steps or 30, guidance_scale=5.0,
                            generator=gen).frames[0]
    export_to_video(frames, str(out), fps=FPS)
    if not out.exists():
        raise RuntimeError(f"{LABELS[engine]} não gerou o arquivo")
    return out
