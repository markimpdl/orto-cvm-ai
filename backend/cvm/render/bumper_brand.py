"""Branded channel bumper, drawn frame by frame from the channel's own art (data/channels/<id>/brand/):
banner.png (the YouTube banner, 16:9) and logo.png (square channel picture).

  0.0–1.6 s  white flash → the banner pulls back into place, a light sweep crosses the logo, low impact + whoosh
  1.6–2.1 s  the banner blurs and darkens; the round channel logo pops in (gold ring, cyan glow)
  2.1–2.6 s  YouTube-style "Subscribe" (bell) and "Like" (thumb) buttons pop in
  ~2.7 s     a cursor clicks Subscribe → "Subscribed ✓" (grey), then clicks Like → it turns blue
  end        everything eases out to black; the presenter's voice asks to subscribe and like over it

Labels are rendered by libass into transparent sprites (Arabic shaping); shapes and motion by Pillow; frames are
piped to ffmpeg.
"""

import math
import re
import sys
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from cvm.config import settings
from cvm.media import H264, duration, filter_path

LABEL_FONT = "Avenir Next Heavy" if sys.platform == "darwin" else "Arial Black"  # upright heavy face on each OS

GOLD, CYAN, RED, BLUE = (245, 184, 0), (39, 225, 255), (255, 0, 0), (62, 166, 255)
SHARE = {"pt-BR": "COMPARTILHE", "en-US": "SHARE", "ar-AE": "شارك"}
# words in the spoken line that trigger each click (lower case, start of the word)
KEYWORDS = {"subscribe": ["inscrev", "segui", "siga", "subscri", "follow", "اشترك", "تابع"], "bell": ["sininho", "notifica", "bell", "الجرس"],
            "like": ["like", "curt", "لايك"], "share": ["compartilh", "share", "شارك"]}
SUBSCRIBED = {"pt-BR": "INSCRITO", "en-US": "SUBSCRIBED", "ar-AE": "تم الاشتراك"}


def brand_files(channel_id: int) -> tuple[Path, Path] | None:
    d = settings.data_dir / "channels" / str(channel_id) / "brand"
    banner, logo = d / "banner.png", d / "logo.png"
    return (banner, logo) if banner.exists() and logo.exists() else None


# ---------- easing ----------

def _clamp(x):
    return max(0.0, min(1.0, x))


def _ease_out(x):
    x = _clamp(x)
    return 1 - (1 - x) ** 3


def _ease_in_out(x):
    x = _clamp(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def _back(x):  # overshoot pop
    x = _clamp(x)
    c = 1.9
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def _seg(t, a, b):
    return _clamp((t - a) / (b - a)) if b > a else float(t >= a)


# ---------- sprites ----------

def label(text: str, px: int, colour: tuple, out: Path) -> Image.Image:
    """One line of text as an RGBA sprite, shaped by libass (works for Arabic). ffmpeg's ass filter keeps the
    alpha untouched, so the text is drawn white on black and its brightness becomes the alpha."""
    W, H = px * max(4, len(text)) + 200, int(px * 2.2)
    ass = out.with_suffix(".ass")
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: L,{LABEL_FONT},{px},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,L,,0,0,0,,{text}
""", encoding="utf-8")
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}",
                    "-vf", f"ass='{filter_path(ass)}'", "-frames:v", "1", str(out)], check=True)
    a = Image.open(out).convert("L")
    img = Image.new("RGBA", a.size, (*colour, 255))
    img.putalpha(a)
    return img.crop(a.getbbox() or (0, 0, 1, 1))


def _bell(s: int, colour=(255, 255, 255, 255)) -> Image.Image:
    k = 4
    img = Image.new("RGBA", (100 * k, 100 * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((24 * k, 18 * k, 76 * k, 76 * k), radius=26 * k, fill=colour)
    d.polygon([(16 * k, 78 * k), (84 * k, 78 * k), (76 * k, 56 * k), (24 * k, 56 * k)], fill=colour)
    d.rounded_rectangle((12 * k, 72 * k, 88 * k, 82 * k), radius=5 * k, fill=colour)
    d.ellipse((42 * k, 82 * k, 58 * k, 96 * k), fill=colour)
    d.ellipse((44 * k, 8 * k, 56 * k, 20 * k), fill=colour)
    return img.resize((s, s), Image.LANCZOS)


def _thumb(s: int, colour=(255, 255, 255, 255)) -> Image.Image:
    k = 4
    img = Image.new("RGBA", (100 * k, 100 * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6 * k, 44 * k, 24 * k, 94 * k), radius=4 * k, fill=colour)  # cuff
    d.rounded_rectangle((30 * k, 40 * k, 90 * k, 94 * k), radius=12 * k, fill=colour)  # palm + fingers
    d.polygon([(30 * k, 46 * k), (46 * k, 8 * k), (56 * k, 6 * k), (62 * k, 14 * k), (58 * k, 42 * k)], fill=colour)  # thumb
    d.ellipse((44 * k, 2 * k, 62 * k, 20 * k), fill=colour)
    for y in (56, 70, 82):  # finger gaps
        d.line((62 * k, y * k, 90 * k, y * k), fill=(0, 0, 0, 0), width=3 * k)
    return img.resize((s, s), Image.LANCZOS)


def _share(s: int, colour=(255, 255, 255, 255)) -> Image.Image:
    """Curved share arrow."""
    k = 4
    img = Image.new("RGBA", (100 * k, 100 * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(56 * k, 10 * k), (96 * k, 44 * k), (56 * k, 78 * k), (56 * k, 58 * k), (40 * k, 58 * k), (24 * k, 62 * k),
               (8 * k, 86 * k), (10 * k, 64 * k), (22 * k, 40 * k), (40 * k, 32 * k), (56 * k, 30 * k)], fill=colour)
    return img.resize((s, s), Image.LANCZOS)


def _cursor(s: int) -> Image.Image:
    k = 4
    pts = [(0, 0), (0, 70), (17, 54), (29, 82), (41, 77), (29, 50), (52, 50)]
    img = Image.new("RGBA", (60 * k, 90 * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    big = [(x * k + 4 * k, y * k + 4 * k) for x, y in pts]
    d.polygon(big, fill=(0, 0, 0, 255))
    inner = [(x * 0.82 * k + 9 * k, y * 0.82 * k + 11 * k) for x, y in pts]
    d.polygon(inner, fill=(255, 255, 255, 255))
    return img.resize((int(s * 60 / 90), s), Image.LANCZOS)


def _round_logo(logo: Path, r: int, ring=GOLD, glow_c=CYAN) -> Image.Image:
    """Circle logo with a gold ring and a cyan glow (the banner's colours), on a transparent canvas."""
    pad = int(r * 0.45)
    size = 2 * (r + pad)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((pad - 18, pad - 18, size - pad + 18, size - pad + 18), fill=(*glow_c, 230))
    out.alpha_composite(glow.filter(ImageFilter.GaussianBlur(r * 0.16)))
    ring_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(ring_img).ellipse((pad - 12, pad - 12, size - pad + 12, size - pad + 12), fill=(*ring, 255))
    out.alpha_composite(ring_img)
    face = Image.open(logo).convert("RGBA").resize((2 * r, 2 * r), Image.LANCZOS)
    mask = Image.new("L", (2 * r * 4, 2 * r * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 2 * r * 4, 2 * r * 4), fill=255)
    face.putalpha(mask.resize((2 * r, 2 * r), Image.LANCZOS))
    out.alpha_composite(face, (pad, pad))
    return out


def _button(w: int, h: int, fill, icon: Image.Image, text: Image.Image, rtl: bool) -> Image.Image:
    k = 3
    b = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    ImageDraw.Draw(b).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=h * k // 2, fill=fill)
    b = b.resize((w, h), Image.LANCZOS)
    shadow = Image.new("RGBA", (w + 60, h + 60), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 150), (30, 38), b.getchannel("A"))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    shadow.alpha_composite(b, (30, 30))
    gap = int(h * 0.22)
    total = icon.width + gap + text.width
    x = 30 + (w - total) // 2
    ix, tx = (x + text.width + gap, x) if rtl else (x, x + icon.width + gap)
    shadow.alpha_composite(icon, (ix, 30 + (h - icon.height) // 2))
    shadow.alpha_composite(text, (tx, 30 + (h - text.height) // 2))
    return shadow


def _paste_scaled(frame: Image.Image, sprite: Image.Image, cx: float, cy: float, scale: float, alpha: float = 1.0):
    if scale <= 0.02 or alpha <= 0.01:
        return
    s = sprite.resize((max(1, int(sprite.width * scale)), max(1, int(sprite.height * scale))), Image.BILINEAR)
    if alpha < 1:
        s.putalpha(s.getchannel("A").point(lambda v: int(v * alpha)))
    x, y = int(cx - s.width / 2), int(cy - s.height / 2)
    if x < 0 or y < 0:  # partly off the left/top edge (slide/zoom entrances): Pillow wants a non-negative corner
        if x + s.width <= 0 or y + s.height <= 0:
            return
        s = s.crop((max(0, -x), max(0, -y), s.width, s.height))
        x, y = max(0, x), max(0, y)
    frame.alpha_composite(s, (x, y))


# ---------- the bumper ----------

def _rgb(hex_or_tuple, default):
    if not hex_or_tuple:
        return default
    h = hex_or_tuple.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def render(banner: Path, logo: Path, voice: Path, texts: dict, language: str, W: int, H: int, fps: int, work: Path,
           out: Path, colours: dict | None = None) -> Path:
    """colours: {"ring": "#hex", "glow": "#hex"} from Channel.prefs["brand"] (default gold + cyan)."""
    u = W / 1920
    rtl = language.startswith("ar")
    # timeline follows the line: its first clause plays over the banner, the logo and buttons arrive with the
    # call to action, and every action is clicked when the voice says it (subscribe, bell, like, share)
    say = texts.get("say", "")
    low = say.lower()
    m = re.search(r"[,،?؟!]", say[:-1])
    split = (m.end() / len(say)) if m and len(say) > 20 else 0.0
    voice_at = 0.5 if split else 1.75
    dv = duration(voice)
    found = {}
    for kind, words in KEYWORDS.items():
        pos = [low.find(w) for w in words if low.find(w) >= 0]
        if pos:
            found[kind] = min(pos) / max(1, len(say))
    kinds = [k for k in ("subscribe", "bell", "like", "share") if k in found or k in ("subscribe", "like")]
    if "subscribe" not in found:  # no keyword: the old spacing (mid-CTA and near the end)
        found["subscribe"] = split + (1 - split) * 0.4
    if "like" not in found:
        found["like"] = 0.9
    kinds.sort(key=lambda k: found[k])
    first = voice_at + dv * found[kinds[0]]
    tb = max(1.2, min(voice_at + dv * split - 0.35 if split else 1.5, first - 1.35))  # banner blurs, logo comes in
    times, last = {}, tb + 1.35
    for k in kinds:  # a click lands just after the word is said, never two at once
        last = max(last, voice_at + dv * found[k] + 0.25)
        times[k] = last
        last += 0.55
    D = max(5.0, voice_at + dv + 0.6, last + 0.6)

    bg = Image.open(banner).convert("RGB").resize((int(W * 1.25), int(H * 1.25)), Image.LANCZOS)
    blurred = bg.filter(ImageFilter.GaussianBlur(22 * u))
    colours = colours or {}
    logo_s = _round_logo(logo, int(190 * u), _rgb(colours.get("ring"), GOLD), _rgb(colours.get("glow"), CYAN))
    bh = int(132 * u)
    px = int(48 * u)
    sub_txt = label(texts["subscribe"], px, (255, 255, 255), work / "l_sub.png")
    subd_txt = label(SUBSCRIBED.get(language, "SUBSCRIBED") + ("" if rtl else "  ✓"), px, (255, 255, 255), work / "l_subd.png")
    like_txt = label(texts["like"], px, (16, 16, 16), work / "l_like.png")
    like_txt_on = label(texts["like"], px, (255, 255, 255), work / "l_like_on.png")
    bw = int(max(sub_txt.width, subd_txt.width, like_txt.width) + bh * 1.6)
    bell = _bell(int(bh * 0.5))
    btn_sub = _button(bw, bh, (*RED, 255), bell, sub_txt, rtl)
    btn_subd = _button(bw, bh, (62, 62, 62, 255), _bell(int(bh * 0.5), (200, 200, 200, 255)), subd_txt, rtl)
    btn_like = _button(bw, bh, (255, 255, 255, 255), _thumb(int(bh * 0.5), (16, 16, 16, 255)), like_txt, rtl)
    btn_liked = _button(bw, bh, (*BLUE, 255), _thumb(int(bh * 0.5)), like_txt_on, rtl)
    share = "share" in kinds
    if share:
        sh_txt = label(texts.get("share") or SHARE.get(language, "SHARE"), px, (16, 16, 16), work / "l_share.png")
        sh_txt_on = label(texts.get("share") or SHARE.get(language, "SHARE"), px, (255, 255, 255), work / "l_share_on.png")
        bw = max(bw, int(sh_txt.width + bh * 1.6))
        btn_share = _button(bw, bh, (255, 255, 255, 255), _share(int(bh * 0.5), (16, 16, 16, 255)), sh_txt, rtl)
        btn_shared = _button(bw, bh, (46, 170, 90, 255), _share(int(bh * 0.5)), sh_txt_on, rtl)
        # every button at the same width
        btn_sub = _button(bw, bh, (*RED, 255), bell, sub_txt, rtl)
        btn_subd = _button(bw, bh, (62, 62, 62, 255), _bell(int(bh * 0.5), (200, 200, 200, 255)), subd_txt, rtl)
        btn_like = _button(bw, bh, (255, 255, 255, 255), _thumb(int(bh * 0.5), (16, 16, 16, 255)), like_txt, rtl)
        btn_liked = _button(bw, bh, (*BLUE, 255), _thumb(int(bh * 0.5)), like_txt_on, rtl)
    bell_on = _bell(int(bh * 0.5), (255, 214, 0, 255))
    gap = int(bh * 0.22)
    total = bell.width + gap + subd_txt.width
    bell_dx = (total / 2 - bell.width / 2) if rtl else -(total / 2 - bell.width / 2)  # bell icon, from the button centre
    cursor = _cursor(int(64 * u))

    # layout: logo on one side, the buttons on the other (mirrored for Arabic)
    lx, bx = (W * 0.70, W * 0.36) if rtl else (W * 0.30, W * 0.64)
    ly = H * 0.5
    sy, ky, shy = (H * 0.34, H * 0.50, H * 0.66) if share else (H * 0.42, H * 0.60, None)
    target = {"subscribe": (bx, sy), "bell": (bx + bell_dx, sy), "like": (bx, ky), "share": (bx, shy or ky)}

    xs = np.linspace(-1, 1, W)[None, :]
    ys = np.linspace(-1, 1, H)[:, None]

    cmd = [settings.ffmpeg_bin, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-i", str(voice),
           "-f", "lavfi", "-i", "anoisesrc=d=0.8:c=pink:a=0.6",  # whoosh in
           "-f", "lavfi", "-i", "anoisesrc=d=0.6:c=pink:a=0.5",  # whoosh to the buttons
           "-f", "lavfi", "-i", "aevalsrc='0.9*sin(2*PI*(45+60*exp(-8*t))*t)*exp(-3.2*t)':d=1.6",  # low impact
           "-f", "lavfi", "-i", "aevalsrc='0.6*sin(2*PI*2400*t)*exp(-90*t)+0.3*sin(2*PI*1200*t)*exp(-60*t)':d=0.12",  # click
           "-filter_complex",
           f"[1:a]adelay={int(voice_at * 1000)}|{int(voice_at * 1000)}[vo];"
           "[2:a]highpass=f=400,lowpass=f=5000,afade=t=in:d=0.25,afade=t=out:st=0.25:d=0.55,volume=0.3[w1];"
           f"[3:a]highpass=f=700,lowpass=f=5000,afade=t=in:d=0.2,afade=t=out:st=0.2:d=0.4,volume=0.2,adelay={int((tb + 0.05) * 1000)}|{int((tb + 0.05) * 1000)}[w2];"
           "[4:a]volume=0.9[boom];"
           f"[5:a]asplit={len(kinds)}" + "".join(f"[c{i}]" for i in range(len(kinds))) + ";"
           + "".join(f"[c{i}]adelay={int(times[k] * 1000)}|{int(times[k] * 1000)}[k{i}];" for i, k in enumerate(kinds))
           + "[vo][w1][w2][boom]" + "".join(f"[k{i}]" for i in range(len(kinds)))
           + f"amix=inputs={4 + len(kinds)}:duration=longest:normalize=0,"
           f"atrim=0:{D:.2f},afade=t=out:st={D - 0.3:.2f}:d=0.3,aformat=sample_rates=48000:channel_layouts=stereo,"
           "loudnorm=I=-14:TP=-1.5:LRA=11[a]",
           "-map", "0:v", "-map", "[a]", "-t", f"{D:.2f}", "-c:v", H264, "-b:v", "16M", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = int(D * fps)
    for i in range(n):
        t = i / fps
        # banner: pull back from 1.18 to 1.0, then a slow push; blur + darken from 1.6 s
        z = 1.18 - 0.18 * _ease_out(t / 1.6) + 0.06 * _seg(t, 1.6, D)
        b = _ease_in_out(_seg(t, tb, tb + 0.5))
        cw, ch = int(W * z), int(H * z)
        src = Image.blend(bg, blurred, b) if b > 0 else bg
        ox, oy = (src.width - cw) // 2, (src.height - ch) // 2
        frame = src.crop((ox, oy, ox + cw, oy + ch)).resize((W, H), Image.BILINEAR)
        arr = np.asarray(frame).astype(np.float32)
        # light sweep across the logo (0.45–1.35 s)
        sw = _seg(t, 0.45, 1.35)
        if 0 < sw < 1:
            pos = -1.6 + 3.2 * sw
            band = np.exp(-((xs + ys * 0.35 - pos) ** 2) / 0.012)
            arr += band[..., None] * 95
        dark = 0.5 * b
        vignette = np.clip((xs ** 2 + ys ** 2) * 0.55, 0, 1)[..., None]
        arr *= (1 - dark) * (1 - 0.35 * vignette)
        flash = 1 - _ease_out(t / 0.35)  # white flash in
        if flash > 0:
            arr = arr * (1 - flash) + 255 * flash
        end = _ease_in_out(_seg(t, D - 0.35, D))  # out to black
        arr *= 1 - end
        frame = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")

        fade_ui = 1 - end
        _paste_scaled(frame, logo_s, lx, ly, _back(_seg(t, tb + 0.25, tb + 0.7)) * (1 + 0.02 * math.sin(t * 3)), fade_ui)
        def press(k):
            if k not in times:
                return 1.0
            tc = times[k]
            return (1 - 0.08 * math.exp(-((t - tc) ** 2) / 0.004)) * (1 + 0.07 * math.exp(-((t - tc - 0.18) ** 2) / 0.01))

        sub = btn_subd if t >= times["subscribe"] else btn_sub
        _paste_scaled(frame, sub, bx, sy, _back(_seg(t, tb + 0.55, tb + 0.95)) * press("subscribe"), fade_ui)
        if "bell" in times and t >= times["bell"]:  # the bell lights up and rings
            dt = t - times["bell"]
            wig = 18 * math.sin(dt * 28) * math.exp(-dt * 3.5)
            b_img = bell_on.rotate(wig, resample=Image.BICUBIC, center=(bell_on.width / 2, bell_on.height * 0.15))
            _paste_scaled(frame, b_img, bx + bell_dx, sy, 1.0 + 0.25 * math.exp(-dt * 6), fade_ui)
        like = btn_liked if t >= times["like"] else btn_like
        _paste_scaled(frame, like, bx, ky, _back(_seg(t, tb + 0.75, tb + 1.15)) * press("like"), fade_ui)
        if share:
            sh = btn_shared if t >= times["share"] else btn_share
            _paste_scaled(frame, sh, bx, shy, _back(_seg(t, tb + 0.95, tb + 1.35)) * press("share"), fade_ui)
        # click ripples
        for k, tc in times.items():
            r = _seg(t, tc, tc + 0.4)
            if 0 < r < 1:
                cxr, cyr = target[k]
                ring = Image.new("RGBA", frame.size, (0, 0, 0, 0))
                rad = 20 * u + 90 * u * r
                ImageDraw.Draw(ring).ellipse((cxr - rad, cyr - rad, cxr + rad, cyr + rad), outline=(255, 255, 255, int(200 * (1 - r))), width=max(2, int(6 * u)))
                frame.alpha_composite(ring)
        # cursor: enters from the lower corner, visits every action in order, then leaves
        seq = [(times[k], target[k]) for k in kinds]
        t_in = max(tb + 1.0, seq[0][0] - 0.6)
        if t_in <= t <= D - 0.5:
            start = (W * (0.55 if rtl else 0.9), H * 1.05)
            pts = [start] + [(x + bw * 0.12 if k != "bell" else x + bh * 0.1, y + bh * 0.15) for k, (_, (x, y)) in zip(kinds, seq)]
            legs = [(t_in, seq[0][0] - 0.05)] + [(seq[j][0] + 0.15, seq[j + 1][0] - 0.05) for j in range(len(seq) - 1)]
            cx, cy = pts[-1]
            for j, (a0, a1) in enumerate(legs):
                if t < a1 or j == len(legs) - 1 and t <= seq[-1][0]:
                    k = _ease_in_out(_seg(t, a0, a1))
                    cx, cy = pts[j][0] + (pts[j + 1][0] - pts[j][0]) * k, pts[j][1] + (pts[j + 1][1] - pts[j][1]) * k
                    break
            else:
                k = _ease_in_out(_seg(t, seq[-1][0] + 0.3, D - 0.5))
                cx, cy = pts[-1][0] + (start[0] - pts[-1][0]) * k, pts[-1][1] + (start[1] - pts[-1][1]) * k
            frame.alpha_composite(cursor, (int(cx), int(cy)))
        proc.stdin.write(frame.convert("RGB").tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg falhou ao montar a vinheta")
    return out


# ---------- short bumper (≤ 3 s, long videos since 2026-11-13) ----------

def logo_file(channel_id: int) -> Path | None:
    p = settings.data_dir / "channels" / str(channel_id) / "brand" / "logo.png"
    return p if p.exists() else None


SHORT_VARIANTS = ("pop", "slide", "glitch", "zoom")  # how the logo arrives; "pop" = the look every channel had
SHORT_SOUNDS = ("mix", "whoosh", "impact", "shimmer", "none")  # "mix" = whoosh + impact + shimmer (the old sound)
_WHOOSH = ["-f", "lavfi", "-i", "anoisesrc=d=0.7:c=pink:a=0.6"]
_IMPACT = ["-f", "lavfi", "-i", "aevalsrc='0.9*sin(2*PI*(45+60*exp(-8*t))*t)*exp(-3.2*t)':d=1.4"]
_SHIMMER = ["-f", "lavfi", "-i", "aevalsrc='0.25*(sin(2*PI*1318*t)+sin(2*PI*1760*t)+0.6*sin(2*PI*2637*t))*exp(-3*t)':d=1.6"]
_ONE_SOUND = {  # one sound alone (install wizard's bumper preview): input + its chain on [1:a]
    "whoosh": (_WHOOSH, "[1:a]highpass=f=400,lowpass=f=5000,afade=t=in:d=0.2,afade=t=out:st=0.2:d=0.5,volume=0.4"),
    "impact": (_IMPACT, "[1:a]adelay=150|150,volume=0.9"),
    "shimmer": (_SHIMMER, "[1:a]adelay=450|450,volume=0.6"),
}


def _logo_motion(variant: str, t: float, D: float, W: int, H: int, u: float, i: int) -> tuple[float, float, float]:
    """(x, y, scale) of the logo at time t. "pop" is the original overshoot pop."""
    grow = 1 + 0.025 * _seg(t, 0.5, D)
    if variant == "slide":  # glides in from the left and settles
        k = _ease_out(_seg(t, 0.1, 0.55))
        return -0.25 * W + (0.75 * W) * k, H / 2, grow
    if variant == "zoom":  # falls from very big onto the screen
        k = _ease_out(_seg(t, 0.1, 0.5))
        return W / 2, H / 2, (3.0 - 2.0 * k) * grow if t >= 0.1 else 0.0
    x = W / 2
    if variant == "glitch" and 0.3 <= t <= 0.75:  # a few frames of digital jitter as it lands
        x += ((i * 37) % 11 - 5) * 7 * u
    return x, H / 2, _back(_seg(t, 0.1, 0.5)) * grow


def render_short(logo: Path, banner: Path | None, W: int, H: int, fps: int, out: Path, D: float,
                 colours: dict | None = None, music: Path | None = None, variant: str = "pop",
                 sound: str = "mix") -> Path:
    """The channel's logo popping in over its blurred banner (or a dark glow), a light sweep across it and a short
    sound (whoosh + low impact + shimmer), or the channel's opening music cut to D. No words, no voice: the
    spoken subscribe ask lives in the script (~40% and the end).
    variant/sound (install wizard): another entrance (slide | glitch | zoom) or one sound alone (whoosh | impact |
    shimmer | none); the defaults are the original look and sound, unchanged."""
    variant = variant if variant in SHORT_VARIANTS else "pop"
    sound = sound if sound in SHORT_SOUNDS else "mix"
    u = W / 1920
    colours = colours or {}
    ring, glow_c = _rgb(colours.get("ring"), GOLD), _rgb(colours.get("glow"), CYAN)
    if banner and banner.exists():
        bg = Image.open(banner).convert("RGB").resize((int(W * 1.15), int(H * 1.15)), Image.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(26 * u))
    else:
        bg = Image.new("RGB", (int(W * 1.15), int(H * 1.15)), (7, 9, 13))
        halo = Image.new("RGB", bg.size, (0, 0, 0))
        r = int(H * 0.45)
        ImageDraw.Draw(halo).ellipse((bg.width // 2 - r, bg.height // 2 - r, bg.width // 2 + r, bg.height // 2 + r), fill=glow_c)
        bg = Image.blend(bg, halo.filter(ImageFilter.GaussianBlur(H * 0.12)), 0.45)
    logo_s = _round_logo(logo, int(min(W, H) * 0.2), ring, glow_c)
    xs = np.linspace(-1, 1, W)[None, :]
    ys = np.linspace(-1, 1, H)[:, None]
    vignette = np.clip((xs ** 2 + ys ** 2) * 0.55, 0, 1)[..., None]
    if music:
        audio = ["-i", str(music)]
        afilter = (f"[1:a]atrim=0:{D:.2f},asetpts=PTS-STARTPTS,afade=t=in:d=0.05,afade=t=out:st={max(0.1, D - 0.5):.2f}:d=0.5,"
                   "aformat=sample_rates=48000:channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    elif sound == "none":
        audio = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        afilter = f"[1:a]atrim=0:{D:.2f}[a]"
    elif sound in _ONE_SOUND:
        audio, chain = _ONE_SOUND[sound]
        afilter = (f"{chain},apad,atrim=0:{D:.2f},afade=t=out:st={D - 0.3:.2f}:d=0.3,"
                   "aformat=sample_rates=48000:channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    else:
        audio = [*_WHOOSH, *_IMPACT, *_SHIMMER]
        afilter = ("[1:a]highpass=f=400,lowpass=f=5000,afade=t=in:d=0.2,afade=t=out:st=0.2:d=0.5,volume=0.3[w];"
                   "[2:a]adelay=150|150,volume=0.9[b];[3:a]adelay=600|600,volume=0.5[s];"
                   f"[w][b][s]amix=inputs=3:duration=longest:normalize=0,apad,atrim=0:{D:.2f},afade=t=out:st={D - 0.3:.2f}:d=0.3,"
                   "aformat=sample_rates=48000:channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    cmd = [settings.ffmpeg_bin, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", *audio, "-filter_complex", afilter, "-map", "0:v", "-map", "[a]", "-t", f"{D:.2f}",
           "-c:v", H264, "-b:v", "12M", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(int(D * fps)):
        t = i / fps
        z = 1.0 + 0.06 * (t / D)  # slow push on the background
        cw, ch = int(W * z), int(H * z)
        ox, oy = (bg.width - cw) // 2, (bg.height - ch) // 2
        frame = bg.crop((ox, oy, ox + cw, oy + ch)).resize((W, H), Image.BILINEAR)
        arr = np.asarray(frame).astype(np.float32) * 0.55 * (1 - 0.35 * vignette)
        sw = _seg(t, 0.55, 1.35)  # light sweep across the logo
        if 0 < sw < 1:
            arr += np.exp(-((xs + ys * 0.35 - (-1.6 + 3.2 * sw)) ** 2) / 0.012)[..., None] * 80
        flash = 0.35 * math.exp(-((t - 0.32) ** 2) / 0.004)  # a quick flash as the logo lands
        arr = arr * (1 - flash) + 255 * flash
        arr *= 1 - 0.6 * _ease_in_out(_seg(t, D - 0.3, D))
        frame = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")
        if variant == "glitch" and 0.3 <= t <= 0.75 and i % 2 == 0:  # colour split on alternate frames
            r, g, b = frame.split()[:3]
            shift = int(6 * u) + 1
            frame = Image.merge("RGB", (ImageChops.offset(r, shift, 0), g, ImageChops.offset(b, -shift, 0))).convert("RGBA")
        lx, ly, scale = _logo_motion(variant, t, D, W, H, u, i)
        _paste_scaled(frame, logo_s, lx, ly, scale, 1 - 0.5 * _seg(t, D - 0.3, D))
        proc.stdin.write(frame.convert("RGB").tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg falhou ao montar a vinheta curta")
    return out
