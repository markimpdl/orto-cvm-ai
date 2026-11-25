"""Editorial cards and segments: one scene of the plan (cvm/visuals/editorial.py `source`) → one 1920×1080 30 fps mp4.

Ported from the bake-off renderer (bakeoff/editorial/render.py `page_spec`, `render_frames`, `video_segment`,
`card_segment`). Cards are drawn by render-templates/editorial/cards.html in headless Chromium, one screenshot per
frame; video/photo scenes get the same page as a transparent overlay (header, footer credit, channel bug) over the
picture, which moves with a camera move picked by the scene's `mov`. The channel's name, logo and accent colour come
from `brand` (see `brand_for`), never from the page.
"""

import re
import subprocess
from pathlib import Path

from cvm.config import REPO_ROOT, settings
from cvm.render.editorial.timeline import EditorialError

TEMPLATE = REPO_ROOT / "render-templates" / "editorial" / "cards.html"
FPS = 30
W, H = 1920, 1080
MAX_SLOW = 1.7  # footage never plays slower than this; a shorter window needs another window
GRADE = "eq=saturation=0.92:contrast=1.04"
ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(FPS)]
# camera moves: (zoom at the start, zoom at the end); "punch" reaches its end zoom in 0.35 s
ZOOM = {"in": (1.04, 1.12), "out": (1.13, 1.04), "punch": (1.05, 1.16), "parado": (1.03, 1.05)}
PAN = {"pan-e": "(in_w-out_w)*(0.75-0.5*t/{D})", "pan-d": "(in_w-out_w)*(0.25+0.5*t/{D})"}
LABELS = {"pt": {"fonte": "FONTE", "traducao": "TRADUÇÃO LIVRE"},
          "en": {"fonte": "SOURCE", "traducao": "FREE TRANSLATION"}}
TEXT_KEYS = ("escuro", "valor", "rotulo", "selo", "texto", "sub", "pequeno", "estilo", "titulo_tela", "impacto_t")


def _run(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffmpeg failed ({r.returncode}): {' '.join(map(str, cmd))[:300]}\n{r.stderr[-1500:]}")
    return r


def _uri(p) -> str:
    return Path(p).resolve().as_uri()


def brand_for(channel) -> dict:
    """{"logo": Path|None, "name": str, "colours": dict, "slogan": str|None} for a Channel (None → no brand)."""
    from cvm.render.bumper import channel_name

    if channel is None:
        return {"logo": None, "name": "", "colours": {}, "slogan": None}
    prefs = channel.prefs or {}
    logo = settings.data_dir / "channels" / str(channel.id) / "brand" / "logo.png"
    return {"logo": logo if logo.exists() else None, "name": channel_name(channel),
            "colours": prefs.get("brand") or {}, "slogan": (prefs.get("editorial") or {}).get("slogan")}


INK = "#0A0E17"  # the card background the accent is read against
MIN_CONTRAST = 4.5  # WCAG AA for text


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    def ch(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _readable_accent(colours: dict) -> str | None:
    """The channel's accent (`accent`, else the bumper's `ring`) when it is a hex colour that reads on the dark card
    (contrast ≥ 4.5 against INK); None → the page keeps its default yellow."""
    ink = _luminance(INK)
    for key in ("accent", "ring"):
        c = (colours or {}).get(key)
        if not isinstance(c, str) or not re.fullmatch(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})", c.strip()):
            continue
        c = c.strip()
        lum = _luminance(c)
        if (max(lum, ink) + 0.05) / (min(lum, ink) + 0.05) >= MIN_CONTRAST:
            return c
    return None


def labels(lang: str) -> dict:
    return dict(LABELS.get((lang or "pt").split("-")[0].lower(), LABELS["en"]))


def page_spec(scene: dict, brand: dict, lang: str) -> dict:
    """What cards.html needs for one scene (files as file:// URIs)."""
    s = scene
    tipo = s["tipo"]
    logo = brand.get("logo")
    colours = brand.get("colours") or {}
    media = s.get("media") or {}
    spec = {"tipo": tipo, "logo": _uri(logo) if logo else None, "name": brand.get("name") or "",
            "slogan": brand.get("slogan") or "", "accent": _readable_accent(colours),
            "labels": labels(lang), "secao": s.get("secao"), "secao_nova": bool(s.get("secao_nova")),
            "fonte": s.get("fonte"), "body": "card"}
    if s.get("bg"):
        spec["bg"] = _uri(s["bg"])
    for k in TEXT_KEYS:
        if s.get(k) is not None:
            spec[k] = s[k]
    traducao = s.get("traducao") or (s.get("prova") or {}).get("traducao")
    if traducao:
        spec["traducao"] = traducao
    if tipo in ("video", "foto"):
        spec["body"] = "overlay"
        spec["fonte"] = None if media.get("own") else (s.get("fonte") or media.get("credit"))
        if s.get("fim"):
            spec.update(fim=True, fonte=None, secao=None)
    elif tipo == "impacto":
        spec["body"] = "impacto"
    elif tipo == "vinheta":
        spec.update(body="vinheta", secao=None)
    elif tipo == "fim":
        spec.update(body="card", secao=None, fonte=None)
    if s.get("_items"):
        spec["itens"] = s["_items"]
    if tipo == "prova":
        spec["img"] = _uri(s["img"])
        spec["size"] = list(s["size"])
        hl = s.get("_hl") or [0.4]
        # one highlight time per box; a single time paints every box one after the other
        spec["boxes"] = [{"rects": b["rects"], "t": round(hl[min(k, len(hl) - 1)] + (0 if len(hl) > 1 else k * .35), 3)}
                         for k, b in enumerate(s.get("boxes") or [])]
    return spec


def open_page(browser):
    """A 1920×1080 page with cards.html loaded (a file page, so the local fonts load)."""
    page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
    page.goto(TEMPLATE.as_uri())
    page.wait_for_timeout(400)
    return page


def render_frames(page, spec: dict, dur: float, outdir: Path, transparent: bool) -> tuple[Path, int]:
    """Screenshot every frame; identical consecutive states are written once (an ffconcat list keeps timing)."""
    outdir.mkdir(parents=True, exist_ok=True)
    page.evaluate("([s, d]) => window.setup(s, d)", [spec, dur])
    page.evaluate("document.fonts.ready.then(() => true)")
    page.wait_for_timeout(150)
    n = max(1, round(dur * FPS))
    entries: list[list] = []
    last_key = None
    for f in range(n):
        key = page.evaluate("t => window.frame(t)", f / FPS)
        if transparent and key == last_key and entries:
            entries[-1][1] += 1
            continue
        p = outdir / f"{len(entries):05d}.{'png' if transparent else 'jpg'}"
        if transparent:
            page.screenshot(path=str(p), omit_background=True)
        else:
            page.screenshot(path=str(p), type="jpeg", quality=93)
        entries.append([p, 1])
        last_key = key
    lst = outdir / "list.ffconcat"
    lst.write_text("ffconcat version 1.0\n" + "".join(f"file '{p.name}'\nduration {c / FPS:.6f}\n" for p, c in entries)
                   + f"file '{entries[-1][0].name}'\n")
    return lst, len(entries)


def _move(mov: str | None, dur: float) -> tuple[str, str]:
    """(zoom expression over t, crop x expression) for a camera move."""
    z0, z1 = ZOOM.get(mov or "in", (1.08, 1.08))
    px = PAN.get(mov, "(in_w-out_w)/2").format(D=dur)
    zoom = f"{z0}+({z1}-{z0})*min(t/0.35,1)" if mov == "punch" else f"{z0}+({z1}-{z0})*min(t/{dur},1)"
    return zoom, px


def _overlay(dur: float) -> str:
    return f"[1:v]fps={FPS},format=rgba[o];[v][o]overlay=0:0:format=auto,trim=duration={dur:.4f}[out]"


def video_segment(scene: dict, dur: float, overlay_list: Path, out: Path, fps: int = FPS) -> float:
    """The scene's footage window (`media.start`..`media.window_end` in `media.file`), muted, slowed when the window is
    shorter than the scene (never more than 1.7×), with the camera move and the overlay on top. Returns the slow factor."""
    m = scene["media"]
    start = float(m.get("start") or 0.0)
    end = m.get("window_end")
    avail = (float(end) - start) if end is not None else dur
    slow = max(1.0, dur / avail) if avail > 0 else float("inf")
    if slow > MAX_SLOW:
        at = float(scene.get("start") or 0.0)
        err = EditorialError(f"A cena em {at:.1f}s precisa de {dur:.2f}s, mas o trecho do vídeo tem "
                             f"{avail:.2f}s (mais lento que {MAX_SLOW}×). Escolha outro trecho.")
        err.fix_state = "sourcing_visuals"
        raise err
    crop = f"{m['crop']}," if m.get("crop") else ""
    zoom, px = _move(scene.get("mov"), dur)
    vf = (f"[0:v]{crop}setpts={round(slow, 4)}*(PTS-STARTPTS),fps={fps},"
          f"scale={W}:{H}:force_original_aspect_ratio=increase,"
          f"scale=w='trunc(iw*({zoom})/2)*2':h='trunc(ih*({zoom})/2)*2':eval=frame,"
          f"crop={W}:{H}:x='{px}':y='(in_h-out_h)/2',{GRADE},setsar=1,"
          f"tpad=stop_mode=clone:stop_duration=0.5[v];" + _overlay(dur))
    _run([settings.ffmpeg_bin, "-v", "error", "-y", "-ss", start, "-i", m["file"], "-f", "concat", "-safe", "0",
          "-i", overlay_list, "-filter_complex", vf, "-map", "[out]", "-an", *ENC, "-t", f"{dur:.4f}", out])
    return round(slow, 4)


def photo_segment(scene: dict, dur: float, overlay_list: Path, out: Path) -> None:
    """Ken Burns over the scene's photo (`media.file`): the same moves and grade as footage. The move runs on a 2×
    canvas and is scaled down at the end, which halves the one-pixel steps of the zoom."""
    m = scene["media"]
    zoom, px = _move(scene.get("mov"), dur)
    vf = (f"[0:v]fps={FPS},scale={2 * W}:{2 * H}:force_original_aspect_ratio=increase,"
          f"scale=w='trunc(iw*({zoom})/2)*2':h='trunc(ih*({zoom})/2)*2':eval=frame,"
          f"crop={2 * W}:{2 * H}:x='{px}':y='(in_h-out_h)/2',scale={W}:{H},{GRADE},setsar=1[v];" + _overlay(dur))
    _run([settings.ffmpeg_bin, "-v", "error", "-y", "-loop", "1", "-framerate", FPS, "-t", f"{dur + 0.5:.4f}",
          "-i", m["file"], "-f", "concat", "-safe", "0", "-i", overlay_list, "-filter_complex", vf, "-map", "[out]",
          "-an", *ENC, "-t", f"{dur:.4f}", out])


def card_segment(lst: Path, dur: float, out: Path) -> None:
    _run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
          "-vf", f"fps={FPS},format=yuv420p,setsar=1", "-an", *ENC, "-t", f"{dur:.4f}", out])
