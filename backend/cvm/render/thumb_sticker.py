"""Sticker thumbnail (TEST, opt-in per channel with `Channel.prefs.thumb_style = "sticker"`; user's pick 2026-11-17):
the picture stays clean, the words go on a solid tilted label in the channel colour placed over the emptiest part of
the image (never over the subject), a thin frame in the channel colour and the channel logo in the bottom-left
corner. The bottom-right corner stays free: YouTube puts the video length there.

Colours: `prefs.thumb` {label, accent, text, font: "rounded"|"condensed"} else the channel's `prefs.brand`
(glow = label, ring = frame/shadow).
"""

import sys
from pathlib import Path

from cvm.config import settings

_SUP = "/System/Library/Fonts/Supplemental/" if sys.platform == "darwin" else "C:/Windows/Fonts/"
FONTS = {
    "rounded": _SUP + ("Arial Rounded Bold.ttf" if sys.platform == "darwin" else "ARLRDBD.TTF"),
    "condensed": _SUP + ("Impact.ttf" if sys.platform == "darwin" else "impact.ttf"),
}
TILT = -4  # degrees


def _rgb(hexs: str | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    try:
        h = (hexs or "").lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else default  # type: ignore[return-value]
    except ValueError:
        return default


def style_of(channel) -> dict | None:
    """The channel's sticker style, or None when it keeps the classic thumbnail."""
    prefs = (channel.prefs or {}) if channel else {}
    if prefs.get("thumb_style") != "sticker":
        return None
    brand, own = prefs.get("brand") or {}, prefs.get("thumb") or {}
    logo = settings.data_dir / "channels" / str(channel.id) / "brand" / "logo.png"
    return {
        "label": _rgb(own.get("label") or brand.get("glow"), (255, 214, 0)),
        "accent": _rgb(own.get("accent") or brand.get("ring"), (20, 20, 20)),
        "text": _rgb(own.get("text"), (24, 24, 24)),
        "font": FONTS.get(own.get("font") or ("rounded" if channel.default_style == "kids" else "condensed"),
                          FONTS["rounded"]),
        "logo": logo if logo.exists() else None,
    }


def busy_map(img):
    """0..1 per pixel: how much is going on (edges + local contrast), blurred so a label avoids whole objects."""
    import numpy as np
    from PIL import Image, ImageFilter

    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    # any line counts as "something there" (a pale object on pale paper is only its outline), grown by a safety
    # margin so a label never touches what it sits beside, then softened
    a = (np.asarray(edges, dtype="float32") > 28).astype("float32") * 255
    grown = Image.fromarray(a.astype("uint8")).filter(ImageFilter.MaxFilter(_odd(img.width // 45)))
    a = np.asarray(grown.filter(ImageFilter.GaussianBlur(img.width / 80)), dtype="float32")
    return a / max(1.0, float(a.max()))


def _odd(n: int) -> int:
    return n + 1 - n % 2


def _label(text: str, st: dict, max_w: int, h_ref: int):
    """The label as an RGBA image (rounded plate + offset shadow in the accent colour), tilted. Text in ≤ 2 lines,
    as big as fits max_w."""
    from PIL import Image, ImageDraw, ImageFont

    words = text.upper().split()
    pad_x, size = 0, int(h_ref * 0.16)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    while size > 18:
        font = ImageFont.truetype(st["font"], size)
        pad_x = int(size * 0.32)
        best = None
        for cut in range(1, len(words) + 1):  # one or two lines, the most balanced split that fits
            lines = [" ".join(words[:cut]), " ".join(words[cut:])] if cut < len(words) else [" ".join(words)]
            wmax = max(probe.textlength(ln, font=font) for ln in lines)
            if wmax + 2 * pad_x <= max_w and (best is None or wmax < best[0]):
                best = (wmax, lines)
        if best:
            break
        size = int(size * 0.92)
    wmax, lines = best if best else (max_w - 2 * pad_x, words[:3])
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * 0.95)
    pad_y = int(size * 0.22)
    w, h = int(wmax + 2 * pad_x), lh * len(lines) + 2 * pad_y
    sh = max(4, int(size * 0.12))  # shadow offset
    plate = Image.new("RGBA", (w + sh, h + sh), (0, 0, 0, 0))
    d = ImageDraw.Draw(plate)
    r = int(size * 0.22)
    d.rounded_rectangle((sh, sh, w + sh - 1, h + sh - 1), r, fill=(*st["accent"], 255))
    d.rounded_rectangle((0, 0, w - 1, h - 1), r, fill=(*st["label"], 255))
    y = pad_y - int(size * 0.04)
    for ln in lines:
        d.text(((w - d.textlength(ln, font=font)) / 2, y), ln, font=font, fill=(*st["text"], 255))
        y += lh
    return plate.rotate(-TILT, resample=Image.BICUBIC, expand=True)


def _place(busy, lw: int, lh: int, w: int, h: int, avoid: list[tuple[float, float, float, float]]):
    """Top-left corner of the emptiest spot for an lw x lh label, and its mean busyness."""
    import numpy as np

    ii = np.pad(busy, ((1, 0), (1, 0))).cumsum(0).cumsum(1)  # integral image
    m = int(w * 0.03)
    best = (None, 9.0)
    for y in range(m, max(m + 1, h - lh - m), max(4, h // 60)):
        for x in range(m, max(m + 1, w - lw - m), max(4, w // 80)):
            if any(x < ax1 * w and x + lw > ax0 * w and y < ay1 * h and y + lh > ay0 * h for ax0, ay0, ax1, ay1 in avoid):
                continue
            s = (ii[y + lh, x + lw] - ii[y, x + lw] - ii[y + lh, x] + ii[y, x]) / (lw * lh)
            s += 0.04 * (y / h)  # ties: higher up reads first
            if s < best[1]:
                best = ((x, y), s)
    return best


def _logo(base, logo: Path, st: dict):
    from PIL import Image, ImageDraw, ImageOps

    w, h = base.size
    d = int(min(w, h) * 0.17)
    im = ImageOps.fit(Image.open(logo).convert("RGBA"), (d, d), Image.LANCZOS)
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
    ring = int(d * 0.07)
    disc = Image.new("RGBA", (d + 2 * ring, d + 2 * ring), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((0, 0, d + 2 * ring - 1, d + 2 * ring - 1), fill=(255, 255, 255, 255))
    disc.paste(im, (ring, ring), mask)
    m = int(min(w, h) * 0.05)
    base.alpha_composite(disc, (m, h - disc.height - m))


def build(img, text: str, st: dict, avoid_extra: list | None = None):
    """img: the graded background (RGB, final size). Returns the finished RGB thumbnail."""
    from PIL import ImageDraw

    w, h = img.size
    busy = busy_map(img)
    # bottom-right = YouTube's length badge; bottom-left = the logo
    avoid = [(0.76, 0.78, 1.0, 1.0)] + ([(0.0, 0.70, 0.26, 1.0)] if st.get("logo") else []) + (avoid_extra or [])
    best = None
    for share in (0.46, 0.40, 0.34, 0.28):  # the biggest label that finds a calm spot
        lab = _label(text, st, int(w * share), h)
        pos, score = _place(busy, lab.width, lab.height, w, h, avoid)
        if pos and (best is None or score < best[2] - 0.02):
            best = (lab, pos, score)
        if pos and score < 0.12:
            best = (lab, pos, score)
            break
    base = img.convert("RGBA")
    if best:
        base.alpha_composite(best[0], best[1])
    if st.get("logo"):
        _logo(base, st["logo"], st)
    fw = max(6, int(w * 0.012))
    ImageDraw.Draw(base).rectangle((0, 0, w - 1, h - 1), outline=(*st["accent"], 255), width=fw)
    return base.convert("RGB")
