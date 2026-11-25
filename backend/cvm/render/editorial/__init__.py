"""Editorial vertical format (approved 2026-11-23): no avatar, the 16:9 picture whole in the
middle of the 9:16 frame over a blurred copy, a fixed header on top (yellow kicker + Anton headline with one accent
word) and the word captions right under the picture. A project opts in with Project.presenter = "editorial".

The header text comes from the script's `packaging.header` ({"kicker", "headline"} with the accent word between
*asterisks*), else the packaging title (emoji removed, last word accented) under a "GTA 6"-style kicker taken from
the topic's first all-caps word.
"""

import re
from pathlib import Path

FONTS = Path(__file__).resolve().parent.parent / "fonts"
ACCENT = "#FFD21E"
HEADER_H = 560
CAPTION_TOP = 1330  # the 16:9 picture sits at y 656–1264 in a 1080x1920 frame; captions start just under it


def wanted(presenter: str | None, vertical: bool) -> bool:
    return bool(vertical and presenter == "editorial")


def _clean(s: str) -> str:
    s = re.sub(r"[\U0001F000-\U0001FFFF☀-➿️]", "", s or "")
    return re.sub(r"\s{2,}", " ", s).strip(" ·-")


def short_kicker(k: str, limit: int = 28) -> str:
    """"GTA 6 · SPOILER · VIA PAULINHO O LOKO" → drop middle parts, then whole trailing words, never cut a word."""
    parts = [x.strip() for x in k.split("·") if x.strip()]
    while len(" · ".join(parts)) > limit and len(parts) > 2:
        parts.pop(1)
    out = " · ".join(parts)
    while len(out) > limit and " " in out:
        out = out.rsplit(" ", 1)[0].rstrip(" ·")
    return out


def header_text(content: dict, topic: str = "") -> tuple[str, str]:
    pk = content.get("packaging") or {}
    h = pk.get("header") or {}
    if h.get("headline"):
        return short_kicker(_clean(h.get("kicker") or "").upper()), _clean(h["headline"])
    title = _clean(pk.get("title") or content.get("hook") or "")
    words = title.split()
    if len(words) > 1 and "*" not in title:
        k = next((i for i in range(len(words) - 1, -1, -1) if len(re.sub(r"\W", "", words[i])) >= 3), len(words) - 1)
        lead = re.match(r"^(\W*)(.*?)(\W*)$", words[k])
        words[k] = f"{lead.group(1)}*{lead.group(2)}*{lead.group(3)}"
    m = re.search(r"\b(GTA\s?\d|[A-Z]{3,}(?:\s\d)?)\b", topic or "")
    return (m.group(1).replace("GTA6", "GTA 6") if m else "AGORA").upper(), " ".join(words)


def compact_html(kicker: str, head_html: str, plain: str, name: str, width: int, height: int) -> str:
    """The 3-panel Short's thin header band (10% of the height): kicker chip + channel name on one row, the headline
    under it on one line (or two smaller ones when long)."""
    s = height / 192
    room = width - 2 * 48 * s
    one = room / (0.47 * max(len(plain), 1))
    lines, size = (1, min(96 * s, one)) if one >= 62 * s else (2, min(52 * s, 2 * one))
    return f""".k{{position:absolute;left:{48*s:.0f}px;top:{14*s:.0f}px;font:600 {22*s:.0f}px Plex;letter-spacing:3px;color:#0A0E17;
    background:{ACCENT};padding:{5*s:.0f}px {10*s:.0f}px {4*s:.0f}px}}
.h{{position:absolute;left:{48*s:.0f}px;right:{48*s:.0f}px;top:{58*s:.0f}px;bottom:{8*s:.0f}px;display:flex;align-items:center;
    font:{size:.0f}px/1.0 Anton;color:#F7F7F5;text-transform:uppercase;text-shadow:-2px 3px 0 rgba(0,0,0,.5)}}
.b{{position:absolute;right:{44*s:.0f}px;top:{16*s:.0f}px;font:600 {18*s:.0f}px Plex;color:rgba(247,247,245,.85);letter-spacing:2px}}
</style><div class="b">{name}</div>{f'<div class="k">{kicker}</div>' if kicker else ''}<div class="h"><div>{head_html}</div></div>"""


def header_png(kicker: str, headline: str, channel_name: str, out: Path, width: int = 1080,
               height: int | None = None) -> Path:
    """Transparent PNG of the header band, drawn by headless Chromium from a file page (so the local fonts load).
    height < HEADER_H: the compact band of the 3-panel Short (compact_html)."""
    from playwright.sync_api import sync_playwright

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    head = re.sub(r"\*(.+?)\*", rf'<span style="color:{ACCENT}">\1</span>', esc(headline))
    size = 118 if len(re.sub(r"\*", "", headline)) <= 22 else 96 if len(headline) <= 40 else 80
    name = "<br>".join(esc(channel_name.upper()).split(" ", 1)) if channel_name else ""
    html = f"""<!doctype html><meta charset="utf-8"><style>
@font-face{{font-family:Anton;src:url({(FONTS / 'Anton-Regular.ttf').as_uri()})}}
@font-face{{font-family:Plex;src:url({(FONTS / 'IBMPlexMono-SemiBold.ttf').as_uri()})}}
body{{margin:0;background:transparent;width:{width}px;height:{HEADER_H}px;overflow:hidden}}
.k{{position:absolute;left:64px;top:150px;font:600 34px Plex;letter-spacing:4px;color:#0A0E17;background:{ACCENT};padding:9px 16px 8px}}
.h{{position:absolute;left:64px;right:64px;top:222px;font:{size}px/1.0 Anton;color:#F7F7F5;text-transform:uppercase;
    text-shadow:-3px 5px 0 rgba(0,0,0,.5)}}
.b{{position:absolute;right:56px;top:60px;font:600 26px Plex;color:rgba(247,247,245,.85);letter-spacing:3px;text-align:right}}
</style><div class="b">{name}</div>{f'<div class="k">{esc(kicker)}</div>' if kicker else ''}<div class="h">{head}</div>"""
    H = height or HEADER_H
    if H < HEADER_H:
        plain = re.sub(r"\*", "", headline)
        html = html.split("<style>")[0] + "<style>" + html.split("<style>")[1].split(".k{")[0].replace(
            f"height:{HEADER_H}px", f"height:{H}px") + compact_html(esc(kicker), head, plain,
                                                                    esc(channel_name.upper()), width, H)
    page_file = out.with_suffix(".html")
    page_file.write_text(html)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": width, "height": H})
        pg.goto(page_file.as_uri())
        pg.evaluate("document.fonts.ready.then(() => true)")
        pg.wait_for_timeout(150)
        pg.screenshot(path=str(out), omit_background=True)
        b.close()
    return out
