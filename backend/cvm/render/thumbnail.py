"""Thumbnails built to be clicked: one bold FLUX background (from the script's `packaging.thumbnail_prompt`), the
presenter cut out big on one side with a white outline and a coloured glow — ONE focus filling 40–60% of the frame —
and at most 3 huge words on the other side (the last line in yellow) that add to the title instead of repeating it
(scripts written before the rule are fitted: words already in the title dropped, then cut to 3).

Kids series: no presenter; the background is a scene's own image with the words in the top band, and the visual
judge's "kids_thumbnail" rule (no distorted faces, exaggerated mouths or scares; calm, friendly characters) picks
another scene's image when the chosen one fails.

Long YouTube videos get up to 3 variants (main + packaging.alternatives) for Studio's "Test & Compare", which
picks the winner by watch time share — so every variant must stay honest to the video.

Presenter image: data/avatars/<id>/thumb.png when the user drops an expressive photo there (recommended: big
emotion reads at phone size), else the avatar's framed photo. The cut-out is cached next to it.
Arabic text is burnt by libass (Pillow here has no Arabic shaping), everything else by Pillow.
"""

import hashlib
import logging
import sys
from pathlib import Path

from cvm.media import filter_path, run_ffmpeg
from cvm.paths import avatar_dir, project_dir
from cvm.render.captions import clean_text, has_arabic

log = logging.getLogger(__name__)
FONT = ("/System/Library/Fonts/Supplemental/Arial Black.ttf" if sys.platform == "darwin"
        else "C:/Windows/Fonts/ariblk.ttf")  # Arial Black, both systems
YELLOW, WHITE = (255, 230, 0), (255, 255, 255)
GLOW = (0, 200, 255)
STYLE = (", YouTube thumbnail background, one bold clear subject, dramatic cinematic lighting, high contrast, vivid "
         "saturated colours, shallow depth of field, simple uncluttered composition, no signs or billboards, empty dark space on the {side} "
         "side, no text, no letters, no logo, no watermark")


# ---------- pieces ----------

def background(project_id: int, prompt: str | None, landscape: bool, seed: int, side: str, fallback: Path,
               kids: bool = False, prefer: str | None = None) -> Path:
    """AI background (Z-Image, FLUX for kids) cached per project/prompt/seed; the video's first frame when no image
    model is ready or it fails. A prompt about a flag is never drawn by AI: the official flag when the country is
    known, else the video frame."""
    from cvm.visuals import flags
    from cvm.visuals.sources import ai_image, image_engine

    if prompt and flags.mentions_flag(prompt):
        code = flags.country_of(prompt)
        out = project_dir(project_id) / "thumb" / f"flag_{code}_{'landscape' if landscape else 'portrait'}.png"
        if code and (got := flags.official_flag(code, out, portrait=not landscape)):
            return got
        log.warning("thumbnail prompt asks for a flag (%s): no AI background, using a video frame", code)
        prompt = None
    if prompt and (engine := image_engine(kids, prefer)):
        key = hashlib.sha1(f"{prompt}|{seed}|{landscape}|{side}|{engine}".encode()).hexdigest()[:10]
        out = project_dir(project_id) / "thumb" / f"bg_{key}.png"
        if out.exists():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            ai_image(prompt + STYLE.format(side=side), out, portrait=not landscape, seed=seed, kids=kids, prefer=prefer)
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("thumbnail background failed, using a video frame: %s", e)
    if fallback.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):  # a scene's still: use it as it is
        return fallback
    frame = fallback.with_suffix(".frame.jpg")
    run_ffmpeg("-ss", "0.5", "-i", str(fallback), "-frames:v", "1", "-pix_fmt", "yuvj420p", str(frame))
    if not frame.exists():  # a clip shorter than 0.5 s
        run_ffmpeg("-i", str(fallback), "-frames:v", "1", "-pix_fmt", "yuvj420p", str(frame))
    return frame


def presenter_cutout(avatar_id: int | None) -> Path | None:
    if not avatar_id:
        return None
    d = avatar_dir(avatar_id)
    src = next((p for p in (d / "thumb.png", d / "thumb.jpg", d / "photo" / "framed.png", d / "base" / "still.jpg")
                if p.exists()), None)
    if not src:
        return None
    out = d / f"thumb_cutout_{int(src.stat().st_mtime)}.png"
    if out.exists():
        return out
    from cvm.avatar.matting import matting_ready

    if not matting_ready():
        return None
    try:
        from cvm.avatar.podcast import cut_out

        tmp = d / "thumb_work"
        tmp.mkdir(exist_ok=True)
        return _trim_alpha(cut_out(src, tmp / "cutout.png"), out)
    except Exception as e:  # noqa: BLE001 — no presenter is better than no thumbnail
        log.warning("presenter cut-out for thumbnail failed: %s", e)
        return None


def _trim_alpha(src: Path, out: Path) -> Path:
    from PIL import Image

    img = Image.open(src).convert("RGBA")
    a = img.getchannel("A").point(lambda v: 0 if v < 24 else v)  # drop matting haze
    img.putalpha(a)
    img.crop(a.getbbox() or (0, 0, *img.size)).save(out)
    return out


def _grade(img):
    from PIL import ImageEnhance, ImageFilter

    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Color(img).enhance(1.25)
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))


def _shade(img, side: str | None, vertical: bool):
    """Darken where the text goes (plus a soft vignette) so the words pop on any background."""
    import numpy as np
    from PIL import Image

    w, h = img.size
    xs, ys = np.linspace(0, 1, w)[None, :], np.linspace(0, 1, h)[:, None]
    if vertical:
        v = np.clip(1 - ys / 0.42, 0, 1) * 200 + 0 * xs
    elif side == "left":
        v = np.clip(1 - xs / 0.62, 0, 1) * 190 + 0 * ys
    elif side == "right":
        v = np.clip((xs - 0.38) / 0.62, 0, 1) * 190 + 0 * ys
    else:
        v = np.full((h, w), 70.0)
    vignette = np.clip(((xs - 0.5) ** 2 + (ys - 0.5) ** 2) * 2.2 - 0.25, 0, 1) * 120
    mask = Image.fromarray(np.clip(np.maximum(v, vignette), 0, 255).astype("uint8"), "L")
    return Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), img, mask)


FOCUS = (0.40, 0.60)  # the one focus (the presenter) covers this share of the thumbnail


def focus_size(pw: int, ph: int, w: int, h: int, vertical: bool) -> tuple[int, int]:
    """Size of the presenter cut-out so its visible box covers ~half the frame (40–60%). Landscape: it may run
    off the bottom edge (head and shoulders), never wider than 56% (the words need the other side); vertical:
    under the words' top band (also running off the bottom when needed)."""
    ar = pw / ph
    max_w = w * (0.9 if vertical else 0.56)
    max_h = h * (1.0 if vertical else 2.0)
    vis_h = h * (0.64 if vertical else 1.0)
    th = h * 0.3
    while th < max_h and th * ar < max_w:
        if th * ar * min(th, vis_h) / (w * h) >= (FOCUS[0] + FOCUS[1]) / 2:
            break
        th += h * 0.01
    th = min(th, max_h, max_w / ar)
    return round(th * ar), round(th)


def focus_share(size: tuple[int, int], w: int, h: int) -> float:
    return min(size[0], w) * min(size[1], h) / (w * h)


def _place_presenter(img, cut: Path, side: str, vertical: bool):
    from PIL import Image, ImageFilter

    w, h = img.size
    p = Image.open(cut).convert("RGBA")
    p = p.resize(focus_size(p.width, p.height, w, h, vertical), Image.LANCZOS)
    if vertical:
        x = (w - p.width) // 2
    else:
        x = w - p.width + int(w * 0.02) if side == "right" else -int(w * 0.02)
    room = h * (0.64 if vertical else 1.0)  # vertical: the words own the top 36%
    y = h - p.height if p.height <= room else int(h - room + h * 0.04)  # too tall: keep the head, crop the bottom
    # crop what falls outside the frame (alpha_composite takes no negative offsets)
    cx, cy = max(0, -x), max(0, -y)
    p = p.crop((cx, cy, min(p.width, w - x), min(p.height, h - y)))
    x, y = x + cx, y + cy
    a = p.getchannel("A")
    base = img.convert("RGBA")
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    glow.paste((*GLOW, 255), (x, y), a.filter(ImageFilter.MaxFilter(31)))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(28)))
    rim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rim.paste((255, 255, 255, 255), (x, y), a.filter(ImageFilter.MaxFilter(13)))
    base.alpha_composite(rim)
    base.alpha_composite(p, (x, y))
    return base.convert("RGB")


def _lines(draw, words: list[str], font, max_w: float) -> list[str]:
    lines, cur = [], ""
    for wd in words:
        test = f"{cur} {wd}".strip()
        if draw.textlength(test, font=font) > max_w and cur:
            lines.append(cur)
            cur = wd
        else:
            cur = test
    return lines + [cur]


def _text_pillow(img, text: str, box: tuple[int, int, int, int]):
    """Biggest size that fits the box in ≤ 3 lines; white, the last line yellow, thick outline + drop shadow."""
    from PIL import Image, ImageDraw, ImageFont

    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    draw = ImageDraw.Draw(img)
    words = text.upper().split()
    size = int(bh * 0.42)
    while size > 20:
        font = ImageFont.truetype(FONT, size)
        lines = _lines(draw, words, font, bw)
        if len(lines) <= 3 and len(lines) * size * 1.08 <= bh and all(draw.textlength(ln, font=font) <= bw for ln in lines):
            break
        size = int(size * 0.92)
    lh = size * 1.06
    y = y0 + (bh - lh * len(lines)) / 2
    stroke = max(5, size // 9)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for i, ln in enumerate(lines):
        x = x0 + (bw - draw.textlength(ln, font=font)) / 2
        colour = YELLOW if i == len(lines) - 1 and len(lines) > 1 else WHITE
        ld.text((x + size * 0.06, y + size * 0.07), ln, font=font, fill=(0, 0, 0, 170), stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
        ld.text((x, y), ln, font=font, fill=colour, stroke_width=stroke, stroke_fill="black")
        y += lh
    base = img.convert("RGBA")
    base.alpha_composite(layer)
    return base.convert("RGB")


def _text_ass(src: Path, text: str, box: tuple[int, int, int, int], tw: int, th: int, out: Path) -> Path:
    x0, y0, x1, y1 = box
    bw = x1 - x0
    size = int((y1 - y0) * 0.42)
    while len(text) * size * 0.55 > bw * 2 and size > 24:  # at most ~2 lines
        size = int(size * 0.9)
    ass = out.with_suffix(".thumb.ass")
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {tw}
PlayResY: {th}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Thumb,Arial Black,{size},&H0000E6FF,&H0000E6FF,&H00000000,&HAA000000,1,0,0,0,100,100,0,0,1,{max(5, size // 9)},{max(3, size // 16)},5,{x0},{tw - x1},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:05.00,Thumb,,0,0,0,,{{\\pos({(x0 + x1) // 2},{(y0 + y1) // 2})}}{text}
""", encoding="utf-8")
    run_ffmpeg("-i", str(src), "-vf", f"ass='{filter_path(ass)}'", "-frames:v", "1", "-pix_fmt", "yuvj420p", "-q:v", "2", str(out))
    ass.unlink(missing_ok=True)
    return out


# ---------- one thumbnail ----------

def build(bg: Path, text: str, out: Path, landscape: bool, cutout: Path | None, side: str = "right",
          sticker: dict | None = None) -> Path:
    from PIL import Image, ImageOps

    tw, th = (1280, 720) if landscape else (1080, 1920)
    text = clean_text(text)
    rtl = has_arabic(text)
    if rtl and cutout and landscape:
        side = "left"  # Arabic reads from the right: the words lead, the face follows
    img = ImageOps.fit(Image.open(bg).convert("RGB"), (tw, th), Image.LANCZOS)
    img = _grade(img)
    if sticker and not rtl:  # channel opted in to the sticker layout (cvm/render/thumb_sticker.py)
        from cvm.render import thumb_sticker

        avoid = []
        if cutout:
            img = _place_presenter(img, cutout, side, not landscape)
            avoid = [(0.44, 0.0, 1.0, 1.0)] if side == "right" else [(0.0, 0.0, 0.56, 1.0)]
        thumb_sticker.build(img, text, sticker, avoid).save(out, quality=93)
        return out
    text_side = None if not cutout else ("left" if side == "right" else "right")
    # no presenter (kids series, a scene as background): words in the top band, so the characters stay visible
    img = _shade(img, text_side if landscape else None, not landscape or not cutout)
    if cutout:
        img = _place_presenter(img, cutout, side, not landscape)
    pad = int(tw * 0.04)
    if not landscape:
        box = (pad, int(th * 0.06), tw - pad, int(th * 0.36))
    elif not cutout:
        box = (pad, int(th * 0.04), tw - pad, int(th * 0.40))
    elif text_side == "left":
        box = (pad, int(th * 0.08), int(tw * 0.54), int(th * 0.92))
    else:
        box = (int(tw * 0.46), int(th * 0.08), tw - pad, int(th * 0.92))
    if rtl:
        tmp = out.with_suffix(".base.png")
        img.save(tmp)
        _text_ass(tmp, text, box, tw, th, out)
        tmp.unlink(missing_ok=True)
    else:
        _text_pillow(img, text, box).save(out, quality=93)
    return out


# ---------- per render ----------

_IMAGE = (".png", ".jpg", ".jpeg", ".webp")


def kids_scene(pkg: dict, scene_media: dict, title: str | None) -> Path | None:
    """The scene image for a kids thumbnail: packaging.thumbnail_scene, unless the judge's kids_thumbnail rule
    refuses it (distorted face, exaggerated mouth, a scare) — then the nearest scene images, first that passes.
    None (keep the chosen scene) without the judge or when nothing passes."""
    from cvm.visuals import judge

    first = pkg.get("thumbnail_scene")
    if first is None or not judge.ready():
        return None
    order = sorted((i for i in scene_media if i is not None), key=lambda i: (i != first, abs(i - first), i))
    cands = [Path(scene_media[i]) for i in order
             if Path(scene_media[i]).suffix.lower() in _IMAGE and Path(scene_media[i]).exists()]
    return judge.pick_kids_thumbnail(cands[:judge.KIDS_THUMB_TRIES], title or "") if cands else None


def _sticker_style(project) -> dict | None:
    if not getattr(project, "channel_id", None):
        return None
    from sqlmodel import Session

    from cvm.db import engine
    from cvm.models import Channel
    from cvm.render import thumb_sticker

    with Session(engine) as s:
        return thumb_sticker.style_of(s.get(Channel, project.channel_id))


def make_thumbnails(project, script_content: dict, language: str, fallback_clip: Path, out_dir: Path, landscape: bool,
                    variants: bool, kids: bool = False, scene_media: dict | None = None,
                    presenter: bool = True) -> list[Path]:
    """presenter False: no avatar cut-out (news-desk editorial videos show no presenter). scene_media: {scene index: image/video of that scene}; packaging.thumbnail_scene picks one as the background
    (kids series keep their own characters; checked by the kids_thumbnail rule), else the FLUX prompt, else the
    video's first frame."""
    from cvm.scripting.schema import fit_thumbnail_text

    pkg = script_content.get("packaging") or {}
    scene_bg = (scene_media or {}).get(pkg.get("thumbnail_scene"))
    if kids and scene_media:
        scene_bg = kids_scene(pkg, scene_media, pkg.get("title")) or scene_bg
    if scene_bg and Path(scene_bg).exists():
        fallback_clip, pkg = Path(scene_bg), {**pkg, "thumbnail_prompt": None}
    scenes = script_content.get("scenes") or []
    main = pkg.get("thumbnail_text") or (scenes[0].get("on_screen_text") if scenes else None) or script_content.get("hook", "")
    main = fit_thumbnail_text(main, pkg.get("title")) or main
    side = pkg.get("thumbnail_side") or "right"
    # a background that already shows him (his own selfie) needs no second, cut-out presenter
    cut = None if kids or not presenter or pkg.get("thumbnail_presenter") is False else presenter_cutout(project.avatar_id)
    sticker = _sticker_style(project)
    alts = [fit_thumbnail_text(a["thumbnail_text"], a.get("title")) or a["thumbnail_text"]
            for a in pkg.get("alternatives", []) if a.get("thumbnail_text")]
    texts = [main] + (alts[:2] if variants else [])
    outs = []
    for i, t in enumerate(texts):
        # variant C also gets a different background, so the test compares more than the words
        bg = background(project.id, pkg.get("thumbnail_prompt"), landscape, seed=101 + (i == 2) * 17,
                        side="left" if side == "right" else "right", fallback=fallback_clip, kids=kids)
        out = out_dir / ("thumbnail.jpg" if i == 0 else f"thumbnail_{'abc'[i]}.jpg")
        outs.append(build(bg, t, out, landscape, cut, side if i < 2 else ("left" if side == "right" else "right"),
                          sticker=sticker))
        if bg.name.endswith(".frame.jpg"):
            bg.unlink(missing_ok=True)
    return outs
