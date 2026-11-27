"""Local model weights: the pipeline only uses a model once it is fully downloaded, so a job never blocks
on a multi-GB download. Download explicitly with:  uv run python -m cvm.models_registry download [name...]

On the Mac the engines run on MLX; elsewhere (Windows/Linux + NVIDIA) a model's "cuda" entry replaces its repo
with the PyTorch weights, and models without one aren't available.
"""

import sys
from pathlib import Path

from cvm.config import settings

HF_CACHE = settings.hf_cache
MLX = sys.platform == "darwin"

CUDA = {
    "whisper": {"repo": "openai/whisper-large-v3-turbo", "size": "~1.6 GB",
                "allow": ["*.json", "*.txt", "model.safetensors"]},
    "clip": {},  # same PyTorch repo on both
    # diffusers package with the transformer in 4-bit NF4 and T5 in 8-bit (bitsandbytes), ungated
    "voxcpm2": {"repo": "openbmb/VoxCPM2", "size": "~5 GB"},  # PyTorch weights for the voxcpm package
    "flux-schnell": {"repo": "aniketppanchal/flux.1-schnell-nf4-pkg", "size": "~13 GB (4-bit)", "ignore": ["*.png"]},
    # hardware tiers (cvm/setup/requirements.py): engines only the CUDA machines run, picked by the card's memory
    "flux-schnell-bf16": {},
    "ltxv-2b": {},
    "ltx2.3-dev": {},
    "gemma3-qat": {},
    # Wan 2.2 TI2V-5B in diffusers layout (WanImageToVideoPipeline, cvm/visuals/video_cuda.py)
    "wan-ti2v-5b": {"repo": "Wan-AI/Wan2.2-TI2V-5B-Diffusers", "size": "~32 GB",
                    "ignore": ["*.png", "*.jpg", "*.mp4", "assets/*", "examples/*"]},
}
_DIFFUSERS = ["model_index.json", "scheduler/*", "text_encoder/*", "text_encoder_2/*", "tokenizer/*", "tokenizer_2/*",
              "transformer/*", "vae/*"]

MODELS = {
    "whisper": {"repo": "mlx-community/whisper-large-v3-turbo", "use": "legendas palavra a palavra", "size": "~1.6 GB"},
    "clip": {"repo": "openai/clip-vit-base-patch32", "use": "conferir se a imagem combina com a cena", "size": "~600 MB",
             "ignore": ["*.msgpack", "*.h5", "tf_model*", "flax_model*"]},
    # official black-forest-labs repo is gated (needs HF login); this is the same Apache-2.0 model pre-quantized for mflux
    "flux-schnell": {"repo": "dhairyashil/FLUX.1-schnell-mflux-4bit", "use": "imagens geradas por IA", "size": "~10 GB (4-bit)",
                     "ignore": ["*.png", "*.jpg"]},
    # Z-Image-Turbo (Tongyi, 6B, 4-bit for mflux): more photographic than FLUX schnell, fewer fake logos; the default
    # for AI stills on the Mac when downloaded (bake-off 2026-11-11). Kids series stay on FLUX (series look).
    "z-image-turbo": {"repo": "filipstrand/Z-Image-Turbo-mflux-4bit", "use": "imagens geradas por IA (fotográficas)",
                      "size": "~5.5 GB (4-bit)"},
    # FLUX.2 Klein 4B (Apache-2.0, mflux): draws a scene from reference images, so a series' characters stay the same
    # in every scene (bake-off 2026-11-15/16: bakeoff/klein_episode). New kids series only; Mac only.
    "flux2-klein": {"repo": "black-forest-labs/FLUX.2-klein-4B", "use": "séries infantis novas: personagens iguais em toda cena",
                    "size": "~16 GB", "allow": ["transformer/*", "text_encoder/*", "vae/*", "tokenizer/*", "*.json", "LICENSE.md"]},
    "voxcpm2": {"repo": "mlx-community/VoxCPM2-8bit", "use": "voz clonada com estilo (opcional, 48 kHz)", "size": "~2.5 GB"},
    # Wan 2.2 TI2V-5B (Apache-2.0) prepared for MLX by mlx-gen; runs through models/MLXGen/.venv (see bakeoff/README.md)
    "wan-ti2v-5b": {"repo": "AbstractFramework/wan2.2-ti2v-5b-diffusers-8bit", "use": "anima imagens FLUX em clipes curtos",
                    "size": "~17 GB (8-bit)"},
    # ---- CUDA only (spec() is None on the Mac) ----
    # FLUX.1 schnell in bf16, diffusers layout: the official black-forest-labs repo is gated, this Apache-2.0 copy
    # is not. The 24 GB tier runs it as is, the 12 GB tier quantizes the transformer to 8 bits on load.
    "flux-schnell-bf16": {"repo": "unsloth/FLUX.1-schnell", "use": "imagens geradas por IA (placas de 12 GB ou mais)",
                          "size": "~34 GB", "cuda_only": True, "allow": _DIFFUSERS},
    # LTX-Video 2B (0.9.x) in diffusers layout: image-to-video on 8 GB cards with offload (LTXImageToVideoPipeline)
    "ltxv-2b": {"repo": "Lightricks/LTX-Video", "use": "anima imagens em clipes curtos (placas de 8 GB)",
                "size": "~27 GB", "cuda_only": True, "allow": _DIFFUSERS},
    # LTX-2.3 official PyTorch checkpoint for the presenter's audio-to-video on CUDA (Lightricks/LTX-2 a2v pipeline):
    # the dev transformer (cast to FP8 on load), the distilled LoRA and the x2 spatial upscaler the two stages need
    "ltx2.3-dev": {"repo": "Lightricks/LTX-2.3", "use": "apresentador falando (LTX-2 no Windows)", "size": "~48 GB",
                   "cuda_only": True, "allow": ["ltx-2.3-22b-dev.safetensors", "ltx-2.3-22b-distilled-lora-384-1.1.safetensors",
                                                "ltx-2.3-spatial-upscaler-x2-1.1.safetensors"]},
    # LTX-2.3's text encoder. Gated by Google: the person accepts the licence on Hugging Face and logs in once
    "gemma3-qat": {"repo": "Lightricks/gemma-3-12b-it-qat-q4_0-unquantized", "use": "texto do LTX-2 no Windows",
                   "size": "~24 GB", "cuda_only": True},
}


_LOADERS: dict = {}  # group -> cached CUDA loaders
_LAST_USE: dict = {}  # group -> time.monotonic() of the last get()
IDLE_S = 120  # an unused group is dropped after this, so an idle worker doesn't sit on ~14 GB of RAM
RAM_NEED_GB = {"image": 6.0, "voice": 4.0}  # RAM a load can't go without (FLUX's weights then page in as needed)
RAM_WAIT_S = 60  # then the load goes ahead anyway: a 15 min wait made every FLUX reload take ~17 min
SHARED = {"clip"}  # small models (~0.6 GB) that stay beside any group instead of evicting it


def free_ram_gb() -> float:
    import psutil

    return psutil.virtual_memory().available / 2**30


def _wait_for_ram(group: str) -> None:
    """Hold a model load until there is room for it plus settings.ram_reserve_gb, instead of running Windows out
    of memory (which kills the worker mid-step). Waits for other programs to give memory back."""
    import logging
    import time

    need = RAM_NEED_GB.get(group, 6.0) + settings.ram_reserve_gb
    deadline, warned = time.monotonic() + RAM_WAIT_S, False
    while (free := free_ram_gb()) < need and time.monotonic() < deadline:
        if not warned:
            logging.getLogger(__name__).warning("model %s waits for RAM: %.1f GB free, needs %.1f", group, free, need)
            warned = True
        time.sleep(5)


def _drop(groups) -> None:
    import gc

    import torch

    for g in groups:
        for f in _LOADERS[g]:
            f.cache_clear()
        _LAST_USE.pop(g, None)
    gc.collect()
    torch.cuda.empty_cache()


def _reaper() -> None:
    import time

    while True:
        time.sleep(30)
        idle = [g for g, t in list(_LAST_USE.items()) if time.monotonic() - t > IDLE_S]
        if idle:
            _drop(idle)  # a call still running keeps its own reference; memory goes when it returns


def cuda_loader(group: str):
    """Caches a CUDA model loader. The 8 GB card holds one group at a time ("voice": VoxCPM2 + Whisper,
    "image": FLUX), so loading a model of one group first drops the others' models; a group unused for IDLE_S
    is dropped too. SHARED groups (CLIP) neither evict nor get evicted: stock ranking between two FLUX stills
    must not reload FLUX."""
    import threading
    import time
    from functools import lru_cache, wraps

    def wrap(fn):
        cached = lru_cache(maxsize=1)(fn)
        _LOADERS.setdefault(group, []).append(cached)

        @wraps(fn)
        def get():
            if not cached.cache_info().currsize:
                others = [g for g, fs in _LOADERS.items()
                          if g != group and g not in SHARED and any(f.cache_info().currsize for f in fs)]
                if others and group not in SHARED:
                    _drop(others)
                    _wait_for_ram(group)
                if not any(t.name == "cvm-model-reaper" for t in threading.enumerate()):
                    threading.Thread(target=_reaper, name="cvm-model-reaper", daemon=True).start()
            _LAST_USE[group] = time.monotonic()
            return cached()

        return get

    return wrap


def spec(name: str) -> dict | None:
    """The model's entry for this machine (None when it has no engine here)."""
    if MLX:
        return None if MODELS[name].get("cuda_only") else MODELS[name]
    return {**MODELS[name], **CUDA[name]} if name in CUDA else None


def repo(name: str) -> str:
    return spec(name)["repo"]


def is_ready(name: str) -> bool:
    if spec(name) is None:
        return False
    folder = HF_CACHE / ("models--" + repo(name).replace("/", "--"))
    if not (folder / "snapshots").exists():
        return False
    if _pending_parts(folder):
        return False
    weights = [p for p in (folder / "snapshots").rglob("*") if p.suffix in (".safetensors", ".npz", ".bin")]
    return bool(weights) and all(p.resolve().exists() and p.resolve().stat().st_size > 1_000_000 for p in weights)


def _pending_parts(folder: Path) -> list[Path]:
    """Partial downloads still in progress. A leftover part doesn't count when its blob already completed, nor when
    nothing has written to it for 10 min (an interrupted attempt: without symlinks, e.g. on Windows, finished files
    go straight to snapshots/ and no blob is left to compare with)."""
    import time

    parts = (folder / "blobs").glob("*.incomplete")
    return [p for p in parts if not (folder / "blobs" / p.name.split(".")[0]).exists()
            and time.time() - p.stat().st_mtime < 600]


def _clean_stale_parts(folder: Path) -> None:
    for p in (folder / "blobs").glob("*.incomplete"):
        if p not in _pending_parts(folder):
            p.unlink(missing_ok=True)


OPTIONAL_WITH_LTX = {"wan-ti2v-5b"}  # Wan only animates when LTX is missing: not "missing" while LTX is installed


def status() -> dict[str, dict]:
    try:
        from cvm.visuals.sources import ltx_ready

        skip = OPTIONAL_WITH_LTX if ltx_ready() else set()
    except Exception:  # noqa: BLE001
        skip = set()
    return {k: {**v, "ready": is_ready(k)} for k in MODELS if (v := spec(k)) and k not in skip}


def download(names: list[str]) -> None:
    from huggingface_hub import snapshot_download

    for n in names or [k for k in MODELS if spec(k)]:
        s = spec(n)
        if s is None:
            print(f"{n}: sem motor para esta máquina, pulando")
            continue
        print(f"baixando {n} ({s['size']})…")
        snapshot_download(s["repo"], ignore_patterns=s.get("ignore"), allow_patterns=s.get("allow"))
        _clean_stale_parts(HF_CACHE / ("models--" + s["repo"].replace("/", "--")))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "download":
        download(sys.argv[2:])
    for k, v in status().items():
        print(f"{'✓' if v['ready'] else '·'} {k:13s} {v['use']} ({v['size']})")
