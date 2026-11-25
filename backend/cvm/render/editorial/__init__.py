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


def header_text(content: dict, topic: str = "") -> tuple[str, str]:
    pk = content.get("packaging") or {}
    h = pk.get("header") or {}
    if h.get("headline"):
        return _clean(h.get("kicker") or "")[:28].upper(), _clean(h["headline"])
    title = _clean(pk.get("title") or content.get("hook") or "")
    words = title.split()
    if len(words) > 1 and "*" not in title:
        k = next((i for i in range(len(words) - 1, -1, -1) if len(re.sub(r"\W", "", words[i])) >= 3), len(words) - 1)
        lead = re.match(r"^(\W*)(.*?)(\W*)$", words[k])
        words[k] = f"{lead.group(1)}*{lead.group(2)}*{lead.group(3)}"
    m = re.search(r"\b(GTA\s?\d|[A-Z]{3,}(?:\s\d)?)\b", topic or "")
    return (m.group(1).replace("GTA6", "GTA 6") if m else "AGORA").upper(), " ".join(words)


def header_png(kicker: str, headline: str, channel_name: str, out: Path, width: int = 1080) -> Path:
    """Transparent PNG of the header band, drawn by headless Chromium from a file page (so the local fonts load)."""
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
    page_file = out.with_suffix(".html")
    page_file.write_text(html)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": width, "height": HEADER_H})
        pg.goto(page_file.as_uri())
        pg.evaluate("document.fonts.ready.then(() => true)")
        pg.wait_for_timeout(150)
        pg.screenshot(path=str(out), omit_background=True)
        b.close()
    return out
