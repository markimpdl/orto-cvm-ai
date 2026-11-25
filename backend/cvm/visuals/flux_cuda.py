"""FLUX.1 schnell on NVIDIA cards, one variant per hardware tier (cvm/setup/requirements.py, feature "ai_images"):

- "flux-nf4": the pre-quantized 4-bit package (transformer NF4, T5 8-bit) — 8 GB cards;
- "flux-8bit": the bf16 weights with the transformer and T5 quantized to 8 bits on load (bitsandbytes) — 12–16 GB;
- "flux-bf16": the bf16 weights as they are — 24 GB and up.

All three take turns on the card with enable_model_cpu_offload (T5, transformer and VAE are moved in only while they
run). Written against the diffusers/transformers bitsandbytes API (docs: quantization/bitsandbytes); UNVERIFIED on
CUDA until the owner's RTX 5060 run — the wizard's engine test (cvm/setup/engine_test.py) falls to the next tier
when a variant runs out of memory.
"""

from pathlib import Path

from cvm.models_registry import cuda_loader

VARIANTS = {"flux-nf4": "flux-schnell", "flux-8bit": "flux-schnell-bf16", "flux-bf16": "flux-schnell-bf16"}
LABELS = {"flux-nf4": "flux-schnell (local)", "flux-8bit": "flux-schnell 8-bit (local)",
          "flux-bf16": "flux-schnell bf16 (local)"}


def ready(variant: str | None) -> bool:
    from cvm.models_registry import is_ready

    return variant in VARIANTS and is_ready(VARIANTS[variant])


@cuda_loader("image")
def _nf4():
    import torch
    from diffusers import FluxPipeline

    from cvm.models_registry import repo

    pipe = FluxPipeline.from_pretrained(repo("flux-schnell"), torch_dtype=torch.bfloat16, local_files_only=True)
    pipe.enable_model_cpu_offload()  # T5 and the transformer take turns on the 8 GB card
    return pipe


@cuda_loader("image")
def _int8():
    import torch
    from diffusers import BitsAndBytesConfig, FluxPipeline, FluxTransformer2DModel
    from transformers import BitsAndBytesConfig as TransformersBitsAndBytesConfig
    from transformers import T5EncoderModel

    from cvm.models_registry import repo

    r = repo("flux-schnell-bf16")
    transformer = FluxTransformer2DModel.from_pretrained(
        r, subfolder="transformer", quantization_config=BitsAndBytesConfig(load_in_8bit=True),
        torch_dtype=torch.bfloat16, local_files_only=True)
    t5 = T5EncoderModel.from_pretrained(
        r, subfolder="text_encoder_2", quantization_config=TransformersBitsAndBytesConfig(load_in_8bit=True),
        torch_dtype=torch.bfloat16, local_files_only=True)
    pipe = FluxPipeline.from_pretrained(r, transformer=transformer, text_encoder_2=t5, torch_dtype=torch.bfloat16,
                                        local_files_only=True)
    pipe.enable_model_cpu_offload()
    return pipe


@cuda_loader("image")
def _bf16():
    import torch
    from diffusers import FluxPipeline

    from cvm.models_registry import repo

    pipe = FluxPipeline.from_pretrained(repo("flux-schnell-bf16"), torch_dtype=torch.bfloat16, local_files_only=True)
    pipe.enable_model_cpu_offload()
    return pipe


LOADERS = {"flux-nf4": _nf4, "flux-8bit": _int8, "flux-bf16": _bf16}


def draw(variant: str, prompt: str, out: Path, width: int, height: int, steps: int = 4, seed: int | None = None) -> Path:
    """One still; the caller holds gpu.section()."""
    import torch

    if variant not in LOADERS:
        raise ValueError(f"FLUX: variante desconhecida {variant!r}")
    gen = torch.Generator("cpu").manual_seed(seed % 2**32) if seed is not None else None
    LOADERS[variant]()(prompt, width=width, height=height, num_inference_steps=steps, guidance_scale=0.0,
                       max_sequence_length=256, generator=gen).images[0].save(out)
    return out
