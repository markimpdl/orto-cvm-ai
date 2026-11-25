"""Image ↔ text similarity with a local CLIP, to check that a stock photo actually shows what the scene needs
(titles and tags lie: "street bartender under neon at night" is not "neon street racing").

Used only when the model is cached (`models_registry download clip`); otherwise ranking stays text-only.
"""

import io
import logging

import httpx

from cvm.models_registry import cuda_loader

log = logging.getLogger(__name__)
MODEL = "openai/clip-vit-base-patch32"


@cuda_loader("clip")
def _model():
    import torch
    from transformers import CLIPModel, CLIPProcessor

    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    return CLIPModel.from_pretrained(MODEL).to(device).eval(), CLIPProcessor.from_pretrained(MODEL), device


def available() -> bool:
    from cvm.models_registry import is_ready

    return is_ready("clip")


def _image(url: str, client: httpx.Client):
    from PIL import Image

    if not url.startswith("http"):
        return Image.open(url).convert("RGB")  # local file (e.g. a frame of a downloaded clip)
    from cvm.visuals.sources import UA

    r = client.get(url, headers=UA)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def similarities(query: str, image_urls: list[str]) -> list[float | None]:
    """Cosine similarity (CLIP B/32: ~0.30+ is a clear match, <0.22 unrelated) per image; None if it can't load."""
    import torch

    model, proc, device = _model()
    images, idx = [], []
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for i, u in enumerate(image_urls):
            try:
                images.append(_image(u, client))
                idx.append(i)
            except Exception as e:  # noqa: BLE001
                log.debug("preview %s failed: %s", u, e)
    out: list[float | None] = [None] * len(image_urls)
    if not images:
        return out
    with torch.no_grad():
        inp = proc(text=[f"a photo of {query}"], images=images, return_tensors="pt", padding=True).to(device)
        res = model(**inp)
        img, txt = res.image_embeds, res.text_embeds  # projected embeddings
        img = img / img.norm(dim=-1, keepdim=True)
        txt = txt / txt.norm(dim=-1, keepdim=True)
        sims = (img @ txt.T).squeeze(-1).tolist()
    for i, s in zip(idx, sims):
        out[i] = round(float(s), 4)
    return out


STEADY = 0.70  # calibrated 2026-11-16 on 225 LTX clips (2.3 and 2.5) looked at one by one: the clips under it had
# lost their subject (a character jumping out of the frame, a car driving off, the drawing turning into another
# scene); pans, push-ins, a plane taking off, a page being turned stay above
FLASH = 0.55  # any single frame this far from the first one: a blink to a blank page


def steadiness(video) -> float | None:
    """How well an animated clip holds its starting picture, from the CLIP similarity of each frame (12 per
    second) to the first one: the lowest value that lasts (rolling median of 5 frames, ~0.4 s, so the blur of a
    quick gesture does not count), or the lowest single frame when it is a FLASH. Compare with STEADY.
    The visual judge reads a strip of three frames and does not see any of this (it gave 10/10 to a character
    leaping off screen with a third leg). None when it can't be measured."""
    import statistics
    import subprocess
    import tempfile
    from pathlib import Path

    import torch
    from PIL import Image

    from cvm.config import settings

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([str(settings.ffmpeg_bin), "-v", "error", "-y", "-i", str(video), "-vf", "fps=12,scale=336:-2",
                        str(Path(tmp) / "%03d.jpg")], capture_output=True)
        frames = [Image.open(f).convert("RGB") for f in sorted(Path(tmp).glob("*.jpg"))]
    if len(frames) < 5:
        return None
    model, proc, device = _model()
    with torch.no_grad():
        e = model.get_image_features(**proc(images=frames, return_tensors="pt").to(device))
        e = getattr(e, "pooler_output", e)
        e = e / e.norm(dim=-1, keepdim=True)
    sims = (e @ e[0]).tolist()
    held = min(statistics.median(sims[i:i + 5]) for i in range(len(sims) - 4))
    return round(float(min(sims) if min(sims) < FLASH else held), 4)
