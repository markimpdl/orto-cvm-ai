"""Proof screenshots: open a source page, find a phrase, screenshot the area around it (2x) and return the phrase's
boxes relative to the image, so the highlighter can be painted exactly over it."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Never a proof source: piracy/download sites (owner's rule: never link the gta6recomp blog).
PIRACY_HOSTS = ("gta6recomp",)  # substring of the host only
PIRACY_WORDS = frozenset({"torrent", "torrents", "crack", "cracked", "warez", "download", "downloads"})  # whole path/host tokens

WIDTH = 1280
PAD = (260, 150)
SCALE = 2

FIND = """(phrase) => {
  const fold = c => { if (/[\\s\\u00a0\\u2000-\\u200b\\u202f\\u3000]/.test(c)) return ' ';
    if ('\\u2018\\u2019\\u201a\\u2032\\u02bc'.includes(c)) return "'"; if ('\\u201c\\u201d\\u201e\\u2033'.includes(c)) return '"';
    if ('\\u2010\\u2011\\u2012\\u2013\\u2014\\u2015\\u2212'.includes(c)) return '-'; return c.toLowerCase(); };
  const blockOf = el => { while (el && el !== document.body) { if (!getComputedStyle(el).display.startsWith('inline')) return el; el = el.parentElement; } return document.body; };
  const chars = [], pos = []; let lastBlock = null;
  const push = (c, p) => { if (c === ' ' && (!chars.length || chars[chars.length-1] === ' ')) return; chars.push(c); pos.push(p); };
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) { const n = w.currentNode; const el = n.parentElement; if (!el) continue;
    if (['SCRIPT','STYLE','NOSCRIPT'].includes(el.tagName)) continue;
    const st = getComputedStyle(el); if (st.display === 'none' || st.visibility === 'hidden') continue;
    const bl = blockOf(el); if (lastBlock && bl !== lastBlock) push(' ', null); lastBlock = bl;
    const t = n.textContent; for (let o = 0; o < t.length; o++) push(fold(t[o]), [n, o]); }
  const want = [...phrase].map(fold).join('').replace(/ +/g, ' ').trim();
  if (!want) return null;
  const flat = chars.join(''); let i = -1;
  while ((i = flat.indexOf(want, i + 1)) >= 0) {
    const a = pos[i], b = pos[i + want.length - 1]; if (!a || !b) continue;
    const r = document.createRange(); r.setStart(a[0], a[1]); r.setEnd(b[0], b[1] + 1);
    const rects = [...r.getClientRects()].filter(x => x.width > 2);
    if (!rects.length) continue;
    a[0].parentElement.scrollIntoView({block: 'center'});
    return [...r.getClientRects()].filter(x => x.width > 2).map(x => [x.left, x.top + window.scrollY, x.width, x.height]);
  }
  return null;
}"""

_HIDE_FIXED = """() => { for (const el of document.querySelectorAll('body *')) { const st = getComputedStyle(el);
  if ((st.position === 'fixed' || st.position === 'sticky') && el.getBoundingClientRect().height < 400)
    el.style.setProperty('display','none','important'); } }"""


def allowed(url: str) -> bool:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host = u.hostname.lower()
    if any(h in host for h in PIRACY_HOSTS):
        return False
    # host: any word of it; path: a whole segment ("/download/", "/torrents.html"), so news slugs like
    # "app-downloads-surge" or "eu-crackdown" pass
    words = set(re.split(r"[^a-z0-9]+", host))
    words |= {seg.rsplit(".", 1)[0] for seg in u.path.lower().split("/")}
    return not (words & PIRACY_WORDS) and "free-download" not in u.path.lower()


def capture(url: str, phrase: str, out: Path, timeout_s: int = 45) -> dict | None:
    """Screenshot around `phrase` on `url`. None when the host is refused, the page is a bot wall/error, the phrase
    is not on the page, or anything fails."""
    if not allowed(url) or not phrase.strip():
        return None
    from playwright.sync_api import sync_playwright

    from .sources import BOT_WALL, dismiss_consent

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            try:
                pg = b.new_page(viewport={"width": WIDTH, "height": 900}, device_scale_factor=SCALE, locale="en-US")
                pg.set_default_timeout(timeout_s * 1000)
                resp = pg.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                pg.wait_for_timeout(1500)
                dismiss_consent(pg)
                pg.evaluate(_HIDE_FIXED)
                if not allowed(pg.url):  # redirected to a banned host
                    return None
                if resp is not None and resp.status >= 400:
                    return None
                if any(m in pg.inner_text("body")[:3000].lower() for m in BOT_WALL):
                    return None
                rects = pg.evaluate(FIND, phrase)
                if not rects:
                    return None
                x0 = max(0, min(r[0] for r in rects) - PAD[0])
                y0 = max(0, min(r[1] for r in rects) - PAD[1])
                x1 = min(WIDTH, max(r[0] + r[2] for r in rects) + PAD[0])
                y1 = min(max(r[1] + r[3] for r in rects) + PAD[1], pg.evaluate("document.documentElement.scrollHeight"))
                if x1 - x0 < 900:  # keep a ~16:10 card at least 900 px wide
                    c = (x0 + x1) / 2
                    x0, x1 = max(0, c - 450), min(WIDTH, c + 450)
                pg.evaluate("window.scrollTo(0,0)")
                pg.screenshot(path=str(out), full_page=True,
                              clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0})
            finally:
                b.close()
    except Exception as e:  # noqa: BLE001
        log.warning("proof capture failed for %s: %s", url, e)
        return None
    from PIL import Image

    with Image.open(out) as im:
        size = [im.width, im.height]
    return {"img": str(out), "size": size,
            "boxes": [{"rects": [[(r[0] - x0) * SCALE, (r[1] - y0) * SCALE, r[2] * SCALE, r[3] * SCALE] for r in rects]}]}
