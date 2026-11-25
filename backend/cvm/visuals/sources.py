"""Free visual sources. Each search returns candidates; `fetch` downloads one to disk.

Candidate: {"kind": "video"|"image", "url", "preview", "source", "license", "credit", "width", "height", "text"}
`text` (title/tags/slug) feeds `relevance`, so a loose keyword hit (e.g. "GTA 6" → a car named "147 GTA (6)") ranks low.
"""

import hashlib
import logging
import os
import re
import subprocess
import unicodedata
from pathlib import Path

import httpx

from cvm.config import key, settings

log = logging.getLogger(__name__)
_VENV_BIN = Path(__file__).resolve().parents[2] / ".venv" / ("Scripts" if os.name == "nt" else "bin")
YTDLP = _VENV_BIN / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
# Wikimedia rejects agents without contact info (https://w.wiki/4wJS)
UA = {"User-Agent": "orto-cvm/0.1 (local content tool) httpx"}
BOT_WALL = ("security verification", "verify you are human", "verifying you are human", "just a moment", "captcha",
            "access denied", "are you a robot", "verificação de segurança", "confirme que você é humano", "enable javascript and cookies",
            "the request could not be satisfied", "request blocked", "403 forbidden", "error 403", "page not found", "404 not found")
FREE_LICENSES = ("public domain", "cc0", "cc by", "cc-by", "pdm")


def pexels_videos(query: str, portrait: bool, client: httpx.Client, n: int = 6) -> list[dict]:
    if not key("pexels_api_key"):
        return []
    r = client.get(
        "https://api.pexels.com/videos/search",
        params={"query": query, "per_page": n, "orientation": "portrait" if portrait else "landscape"},
        headers={"Authorization": key("pexels_api_key")},
    )
    r.raise_for_status()
    out = []
    for v in r.json().get("videos", []):
        files = sorted((f for f in v["video_files"] if f.get("width")), key=lambda f: f["width"])
        best = next((f for f in files if f["width"] >= 1080), files[-1] if files else None)
        if best:
            out.append({"kind": "video", "url": best["link"], "preview": v["image"], "source": "pexels", "license": "Pexels License",
                        "credit": v["user"]["name"], "width": best["width"], "height": best["height"], "duration": v.get("duration"),
                        "text": v.get("url", "").rstrip("/").rsplit("/", 1)[-1].replace("-", " ")})
    return out


def pixabay_videos(query: str, portrait: bool, client: httpx.Client, n: int = 6) -> list[dict]:
    if not key("pixabay_api_key"):
        return []
    r = client.get("https://pixabay.com/api/videos/", params={"key": key("pixabay_api_key"), "q": query, "per_page": max(n, 3)})
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        f = h["videos"].get("large") or h["videos"].get("medium")
        if f and f.get("url"):
            out.append({"kind": "video", "url": f["url"], "preview": f.get("thumbnail"), "source": "pixabay", "license": "Pixabay Content License",
                        "credit": h.get("user"), "width": f["width"], "height": f["height"], "duration": h.get("duration"),
                        "text": h.get("tags", "")})
    return out


def wikimedia_images(query: str, portrait: bool, client: httpx.Client, n: int = 6) -> list[dict]:
    r = client.get(
        "https://commons.wikimedia.org/w/api.php",
        params={"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": n * 2, "prop": "imageinfo",
                "iiprop": "url|extmetadata|size", "iiurlwidth": 1920},
        headers=UA,
    )
    r.raise_for_status()
    out = []
    for p in (r.json().get("query", {}).get("pages", {}) or {}).values():
        ii = p["imageinfo"][0]
        lic = (ii["extmetadata"].get("LicenseShortName", {}).get("value") or "").lower()
        if not lic.startswith(FREE_LICENSES) or ii.get("width", 0) < 800:
            continue
        artist = ii["extmetadata"].get("Artist", {}).get("value", "")
        out.append({"kind": "image", "url": ii.get("thumburl") or ii["url"], "preview": ii.get("thumburl"), "source": "wikimedia",
                    "license": lic, "credit": _strip_html(artist)[:120], "width": ii["width"], "height": ii["height"], "page": ii.get("descriptionurl"),
                    "text": p.get("title", "").removeprefix("File:").rsplit(".", 1)[0],
                    "tags": _strip_html(ii["extmetadata"].get("ImageDescription", {}).get("value", ""))[:300]})
    return out[:n]


def openverse_images(query: str, portrait: bool, client: httpx.Client, n: int = 8) -> list[dict]:
    """Openverse (WordPress): CC-licensed images from Flickr, Wikimedia, museums… No key needed."""
    r = client.get("https://api.openverse.org/v1/images/",
                   params={"q": query, "page_size": n * 2, "license_type": "commercial,modification", "mature": "false"}, headers=UA)
    r.raise_for_status()
    out = []
    for x in r.json().get("results", []):
        if (x.get("width") or 0) < 800:
            continue
        lic = f"cc {x['license']}" if x["license"] not in ("cc0", "pdm") else x["license"]
        out.append({"kind": "image", "url": x["url"], "preview": x.get("thumbnail"), "source": f"openverse/{x.get('source')}", "license": lic,
                    "credit": (x.get("creator") or "")[:120], "width": x["width"], "height": x["height"], "page": x.get("foreign_landing_url"),
                    "text": x.get("title") or "", "tags": " ".join(t["name"] for t in x.get("tags") or [])})
    return out[:n]


def wikimedia_videos(query: str, portrait: bool, client: httpx.Client, n: int = 4) -> list[dict]:
    """Wikimedia Commons video (news events, nature, archives, NASA uploads): free licences only, ≥ 720p,
    3 s to 3 min, under 80 MB. The poster frame is the preview, so CLIP checks it like a photo."""
    r = client.get(
        "https://commons.wikimedia.org/w/api.php",
        params={"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                "gsrsearch": f"{query} filetype:video", "gsrlimit": n * 3, "prop": "imageinfo",
                "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 640},
        headers=UA,
    )
    r.raise_for_status()
    out = []
    for p in (r.json().get("query", {}).get("pages", {}) or {}).values():
        ii = p["imageinfo"][0]
        lic = (ii["extmetadata"].get("LicenseShortName", {}).get("value") or "").lower()
        if not lic.startswith(FREE_LICENSES) or min(ii.get("width", 0), ii.get("height", 0)) < 720:
            continue
        if not 3 <= (ii.get("duration") or 0) <= 180 or (ii.get("size") or 0) > 80_000_000:
            continue
        artist = ii["extmetadata"].get("Artist", {}).get("value", "")
        out.append({"kind": "video", "url": ii["url"], "preview": ii.get("thumburl"), "source": "wikimedia", "license": lic,
                    "credit": _strip_html(artist)[:120] or "Wikimedia Commons", "width": ii["width"], "height": ii["height"],
                    "duration": ii.get("duration"), "page": ii.get("descriptionurl"),
                    "text": p.get("title", "").removeprefix("File:").rsplit(".", 1)[0],
                    "tags": _strip_html(ii["extmetadata"].get("ImageDescription", {}).get("value", ""))[:300]})
    return out[:n]


STOPWORDS = set("a o as os de da do das dos e em no na um uma para com por the of and in on for to with at from an".split())


def _words(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()
    s = re.sub(r"\(\d+\)", " ", s)  # file numbering like "(6)" is not content
    return [w for w in re.findall(r"[a-z0-9]+", s) if w not in STOPWORDS]


def relevance(query: str, cand: dict) -> float:
    """0–1: share of the query's words found in the candidate's title (`text`), +bonus when they appear as a phrase.
    Words found only in `tags`/description count half: user tags on Flickr/Openverse are a noisy word soup."""
    q = _words(query)
    if not q:
        return 0.0
    text = _words(cand.get("text") or "")
    have, tags = set(text), set(_words(cand.get("tags") or ""))
    cover = sum(1.0 if w in have else 0.5 if w in tags else 0 for w in q) / len(q)
    phrase = len(q) > 1 and f" {' '.join(q)} " in f" {' '.join(text)} "
    if not phrase and any(w.isdigit() for w in q):
        cover = min(cover, 0.4)  # "GTA 6": a stray "6" somewhere in the tags means nothing
    return min(1.0, cover + (0.25 if phrase else 0))


def _strip_html(s: str) -> str:
    import re

    from html import unescape

    return unescape(re.sub(r"<[^>]+>", "", s)).strip()


def title_cover(query: str, cand: dict) -> float:
    q, have = _words(query), set(_words(cand.get("text") or ""))
    return sum(w in have for w in q) / len(q) if q else 0.0


CLIP_MIN = 0.26  # CLIP B/32 cosine: unrelated photos sit around 0.20–0.23, clear matches 0.28+


def search_broll(terms: list[str], portrait: bool, min_relevance: float = 0.6) -> list[dict]:
    """Candidates for the scene's search terms, best first. Weak matches are dropped: an AI image or a text
    card (the caller's fallbacks) is better than an unrelated photo.

    With the local CLIP model, the image itself is checked against the term (titles/tags only pre-filter);
    without it, a stricter text-only rule applies."""
    from cvm.visuals import clip_rank

    use_clip = clip_rank.available()
    ranked: dict[str, dict] = {}
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        for i, term in enumerate([t for t in terms or [] if t.strip()][:3]):
            found: list[dict] = []
            for fn in (pexels_videos, pixabay_videos, wikimedia_videos, openverse_images, wikimedia_images):
                try:
                    found += fn(term, portrait, client)
                except Exception as e:  # noqa: BLE001
                    log.warning("%s(%s) failed: %s", fn.__name__, term, e)
            for c in found:
                c["relevance"] = round(relevance(term, c), 2)
            if use_clip:
                pool = sorted([c for c in found if c["relevance"] >= 0.25 or c["source"] in ("pexels", "pixabay")
                               or (c["kind"] == "video" and c.get("preview"))],
                              key=lambda c: -c["relevance"])[:16]
                try:
                    sims = clip_rank.similarities(term, [c.get("preview") or c["url"] for c in pool])
                except Exception as e:  # noqa: BLE001
                    log.warning("clip failed, text-only ranking: %s", e)
                    use_clip, sims = False, []
                keep = [(c, s) for c, s in zip(pool, sims) if s is not None and s >= CLIP_MIN]
                scored = [(c, 3 * s + 0.3 * c["relevance"], s) for c, s in keep]
            if not use_clip:
                def ok(c):
                    need = 0.5 if len(_words(term)) <= 2 else 0.67  # half the query in the title, not only in the tag soup
                    return c["relevance"] >= min_relevance and (c["kind"] != "image" or title_cover(term, c) >= need)
                scored = [(c, c["relevance"], None) for c in found if ok(c)]
            for rank, (c, base, sim) in enumerate(scored):
                # earlier terms are the writer's first choice; video beats stills
                score = base - 0.1 * i + (0.15 if c["kind"] == "video" else 0) - 0.001 * rank
                if c["url"] not in ranked or ranked[c["url"]]["score"] < score:
                    ranked[c["url"]] = {**c, "clip": sim, "score": round(score, 3), "query": term}
            if len(ranked) >= 6:
                break  # enough good options, skip the fallback terms
    out, seen = [], set()
    for c in sorted(ranked.values(), key=lambda c: -c["score"]):
        key = (c["text"][:40].lower(), c.get("credit"))  # same shoot uploaded many times → keep one
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def download(url: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(url.split("?")[0]).suffix[:5] or ".bin"
    out = dest_dir / (hashlib.sha1(url.encode()).hexdigest()[:16] + ext)
    if out.exists():
        return out
    with httpx.stream("GET", url, headers=UA, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with out.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return out


def article_image(url: str) -> dict | None:
    """The article's lead image (og:image) — usually the subject itself (official art, event photo)."""
    r = httpx.get(url, headers={**UA, "User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/130 Safari/537.36"},
                  timeout=20, follow_redirects=True)
    r.raise_for_status()
    m = re.search(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)', r.text) \
        or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)', r.text)
    if not m:
        return None
    outlet = url.split("/")[2].removeprefix("www.")
    return {"kind": "image", "url": m.group(1).replace("&amp;", "&"), "source": url, "license": "news image (credit outlet)",
            "credit": outlet, "text": ""}


CONSENT_BUTTONS = ("Reject all", "Reject", "Rejeitar", "Recusar", "Decline", "Accept", "Aceitar", "I agree", "Concordo",
                   "OK", "Got it", "Accept all", "Aceitar todos")
_HIDE_OVERLAYS = """() => {
  const re = /cookie|privac|consent|gdpr|lgpd|newsletter|assine|subscribe|notifica/i;
  for (const el of document.querySelectorAll('body *')) {
    const st = getComputedStyle(el);
    if ((st.position === 'fixed' || st.position === 'sticky') && re.test(el.innerText || '') && el.innerText.length < 2000)
      el.style.setProperty('display', 'none', 'important');
  }
  document.documentElement.style.overflow = 'auto'; document.body.style.overflow = 'auto';
}"""


def dismiss_consent(page) -> None:
    """Decline cookie/consent banners when offered (else acknowledge), then hide fixed/sticky boxes that talk about it."""
    for label in CONSENT_BUTTONS:  # get_by_role also reaches shadow-DOM banners
        btn = page.get_by_role("button", name=label, exact=True)
        if btn.count():
            try:
                btn.first.click(timeout=1500)
                break
            except Exception:  # noqa: BLE001
                pass
    page.evaluate(_HIDE_OVERLAYS)


def news_screenshot(url: str, out: Path, portrait: bool = True) -> Path:
    """Screenshot the top of an article (headline area), for 'headline' scenes."""
    from playwright.sync_api import sync_playwright

    w, h = (1080, 1350) if portrait else (1600, 900)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": w // 2 if portrait else w, "height": h // 2 if portrait else h}, device_scale_factor=2)
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        dismiss_consent(page)
        page.wait_for_timeout(300)
        body = page.inner_text("body")[:3000].lower()
        blocked = (resp is not None and resp.status >= 400) or any(m in body for m in BOT_WALL)  # error pages (CloudFront 403…)
        if not blocked:
            page.screenshot(path=str(out))
        browser.close()
    if blocked:
        raise RuntimeError(f"bot wall at {url}")
    if detail(out) < MIN_DETAIL:  # lazy page that never painted (grey placeholder box, splash with a logo)
        out.unlink(missing_ok=True)
        raise RuntimeError(f"empty page at {url}")
    return out


MIN_DETAIL = 0.05  # blank screenshots measure ≤ 0.03, real headlines 0.08+


def detail(image: Path) -> float:
    """Share of edge pixels (400 px thumbnail): ~0 for an empty frame, 0.1+ for a page with a headline and text."""
    from PIL import Image, ImageFilter

    im = Image.open(image).convert("L")
    im.thumbnail((400, 400))
    hist = im.filter(ImageFilter.FIND_EDGES).histogram()
    return sum(hist[41:]) / max(sum(hist), 1)


def flux_variant() -> str | None:
    """CUDA machines: this machine's FLUX tier ("flux-nf4" | "flux-8bit" | "flux-bf16", cvm/visuals/flux_cuda.py),
    when its weights are downloaded; None otherwise."""
    from cvm import engines
    from cvm.visuals import flux_cuda

    v = engines.feature_engine("ai_images")
    return v if flux_cuda.ready(v) else None


AI_STILL_SOURCES = ("flux", "z-image")  # Asset.source prefixes of locally generated stills


def is_ai_still(source: str | None) -> bool:
    return str(source or "").startswith(AI_STILL_SOURCES)


def image_engine(kids: bool = False, prefer: str | None = None) -> str | None:
    """Which model draws AI stills here: Z-Image-Turbo by default on the Mac, FLUX schnell for kids series (their
    look must not change mid-series), on CUDA, or when Z-Image isn't downloaded. None when neither is ready.
    `prefer` is a channel's own choice (Channel.prefs.image_engine): a series started on Z-Image stays on it, and a
    new kids series is on "klein" (FLUX.2 Klein draws from the characters' reference sheets, cvm/visuals/cast.py)."""
    from cvm.config import settings
    from cvm.models_registry import MLX, is_ready

    from cvm import engines

    want = prefer or ("flux" if kids else engines.image_engine())
    if MLX and want == "klein" and is_ready("flux2-klein"):
        return "klein"
    if MLX and want == "z-image" and is_ready("z-image-turbo"):
        return "z-image"
    if not MLX:  # CUDA: the FLUX variant of this machine's tier (images by AI only when one is ready)
        return "flux" if flux_variant() else None
    return "flux" if is_ready("flux-schnell") else None


SOURCE_LABEL = {"z-image": "z-image-turbo (local)", "flux": "flux-schnell (local)", "klein": "flux2-klein (local)"}


def ai_image(prompt: str, out: Path, portrait: bool = True, seed: int | None = None, kids: bool = False,
             prefer: str | None = None, refs: list[Path] | None = None) -> str:
    """Draw a still with the engine of `image_engine`; returns its Asset.source label.
    `refs`: reference images (character sheets), used by Klein only."""
    engine = image_engine(kids, prefer)
    if engine == "klein":
        klein_image(prompt, out, portrait, seed=seed, refs=refs)
    elif engine == "z-image":
        zimage_image(prompt, out, portrait, seed=seed)
    elif engine == "flux":
        flux_image(prompt, out, portrait, seed=seed)
    else:
        raise RuntimeError("no image model downloaded")
    return SOURCE_LABEL[engine]


KLEIN_MAX_REFS = 4  # reference images per drawing (three characters together held in the bake-off)


def klein_image(prompt: str, out: Path, portrait: bool = True, steps: int = 4, seed: int | None = None,
                refs: list[Path] | None = None, size: tuple[int, int] | None = None) -> Path:
    """FLUX.2 Klein 4B through mflux: plain text-to-image, or drawn from up to KLEIN_MAX_REFS reference images
    ("image 1", "image 2"… in the prompt) so each character keeps its look. ~30–70 s for 1344x768 on the M5 Pro."""
    from cvm import gpu

    w, h = size or ((768, 1344) if portrait else (1344, 768))
    refs = [str(r) for r in (refs or []) if Path(r).is_file()][:KLEIN_MAX_REFS]
    tool = "mflux-generate-flux2-edit" if refs else "mflux-generate-flux2"
    with gpu.section():
        subprocess.run(
            [str(_VENV_BIN / tool), "--model", "flux2-klein-4b", *(["--image-paths", *refs] if refs else []),
             "--steps", str(steps), "--width", str(w), "--height", str(h), "--prompt", prompt, "--output", str(out),
             *(["--seed", str(seed)] if seed is not None else [])],
            check=True, capture_output=True, text=True, env={**os.environ, "HF_HUB_OFFLINE": "1"},
        )
    return out


def zimage_image(prompt: str, out: Path, portrait: bool = True, steps: int = 9, seed: int | None = None) -> Path:
    """Z-Image-Turbo (4-bit, mflux) — ~28 s for 768x1344 on the M5 Pro."""
    from cvm import gpu
    from cvm.models_registry import MODELS

    w, h = (768, 1344) if portrait else (1344, 768)
    with gpu.section():
        subprocess.run(
            [str(_VENV_BIN / "mflux-generate-z-image-turbo"), "--model", MODELS["z-image-turbo"]["repo"],
             "--base-model", "z-image-turbo", "--steps", str(steps), "--width", str(w), "--height", str(h),
             "--prompt", prompt, "--output", str(out), *(["--seed", str(seed)] if seed is not None else [])],
            check=True, capture_output=True, text=True, env={**os.environ, "HF_HUB_OFFLINE": "1"},
        )
    return out


def flux_image(prompt: str, out: Path, portrait: bool = True, steps: int = 4, seed: int | None = None) -> Path:
    """Local FLUX.1-schnell from the pre-quantized 4-bit weights in models_registry: mflux (MLX) on the Mac,
    diffusers on CUDA elsewhere."""
    from cvm import gpu
    from cvm.models_registry import MLX, MODELS

    w, h = (768, 1344) if portrait else (1344, 768)
    if not MLX:
        from cvm.visuals import flux_cuda

        variant = flux_variant()
        if variant is None:
            raise RuntimeError("nenhum FLUX pronto nesta máquina (assistente > Modelos)")
        with gpu.section():
            flux_cuda.draw(variant, prompt, out, w, h, steps=steps, seed=seed)
        return out
    mflux = _VENV_BIN / "mflux-generate"

    with gpu.section():
        subprocess.run(
            [str(mflux), "--model", MODELS["flux-schnell"]["repo"], "--base-model", "schnell", "--steps", str(steps),
             "--width", str(w), "--height", str(h), "--prompt", prompt, "--output", str(out),
             *(["--seed", str(seed)] if seed is not None else [])],
            check=True, capture_output=True, text=True,
        )
    return out


WAN_FPS = 24


def wan_frames(seconds: float) -> int:
    """Wan needs 4k+1 frames; TI2V-5B tops out at 121 (5 s at 24 fps)."""
    n = int(seconds * WAN_FPS) + 1
    return max(49, min(121, (n - 1 + 3) // 4 * 4 + 1))


def wan_clip(image: Path, prompt: str, out: Path, seconds: float, portrait: bool = True, steps: int = 20,
             timeout: int = 3600, seed: int = 42) -> Path:
    """Animate a still with Wan 2.2 TI2V-5B (first-frame image-to-video) through mlx-gen, 480p-class."""
    from cvm.config import settings
    from cvm.models_registry import MODELS

    w, h = (480, 832) if portrait else (832, 480)
    bin_ = settings.models_dir / "MLXGen" / ".venv" / "bin" / "mlxgen"
    from cvm import gpu

    with gpu.section():
        subprocess.run(
            [str(bin_), "generate", "--model", MODELS["wan-ti2v-5b"]["repo"], "--image", str(image), "--prompt", prompt,
             "--width", str(w), "--height", str(h), "--frames", str(wan_frames(seconds)), "--steps", str(steps),
             "--guidance", "5", "--flow-shift", "3", "--fps", str(WAN_FPS), "--seed", str(seed), "--output", str(out)],
            check=True, capture_output=True, text=True, timeout=timeout,
        )
    if not out.exists():
        raise RuntimeError("mlx-gen produced no file")
    return out


# LTX-2.3 distilled (q8, MLX) through models/LTX2MLX (ltx-2-mlx, MIT; weights: LTX-2 community license).
# Only the subset of dgrauet/ltx-2.3-mlx-q8 the distilled path needs is downloaded (see the user's notes), so it
# is checked here by path instead of models_registry (whose `download` would fetch the whole 87 GB repo).
LTX_REPO = "dgrauet/ltx-2.3-mlx-q8"
# LTX-2.5 (sharper, faster clips; bake-off 2026-11-15): used when its distilled pack is downloaded and
# settings.ltx_pack is "2.5"; a machine without it stays on 2.3.
LTX25_REPO = "dgrauet/ltx-2.5-mlx-q8"
LTX_REFINE_REPO = "Lightricks/LTX-2.5-22b-IC-LoRA-Refine-Details"
LTX_PACKS = {"2.5": (LTX25_REPO, "transformer-distilled.safetensors"), "2.3": (LTX_REPO, "transformer-distilled-1.1.safetensors")}
LTX_FPS = 24


def _ltx_pack(version: str) -> Path | None:
    from cvm.models_registry import HF_CACHE

    repo, transformer = LTX_PACKS[version]
    snaps = HF_CACHE / ("models--" + repo.replace("/", "--")) / "snapshots"
    for snap in sorted(snaps.glob("*")) if snaps.exists() else []:
        if (snap / transformer).exists() and (snap / "connector.safetensors").exists():
            return snap
    return None


def ltx_version() -> str | None:
    """The LTX pack in use: settings.ltx_pack when downloaded, else the other one; None when neither is."""
    from cvm.config import settings

    want = settings.ltx_pack if settings.ltx_pack in LTX_PACKS else "2.5"
    return next((v for v in (want, *[k for k in LTX_PACKS if k != want]) if _ltx_pack(v)), None)


def _ltx_model() -> Path | None:
    return _ltx_pack(v) if (v := ltx_version()) else None


def ltx_refine_lora() -> Path | None:
    """The Refine-Details IC-LoRA file (LTX-2.5 presenter refinement), when downloaded."""
    from cvm.models_registry import HF_CACHE

    snaps = HF_CACHE / ("models--" + LTX_REFINE_REPO.replace("/", "--")) / "snapshots"
    return next((p for p in sorted(snaps.glob("*/*.safetensors")) if p.resolve().exists()), None) if snaps.exists() else None


def ltx_ready() -> bool:
    from cvm.config import settings
    from cvm.models_registry import HF_CACHE

    gemma = HF_CACHE / "models--mlx-community--gemma-3-12b-it-4bit" / "snapshots"
    return bool(_ltx_model()) and gemma.exists() and (settings.models_dir / "LTX2MLX" / ".venv" / "bin" / "ltx-2-mlx").exists()


def ltx_clip(image: Path, prompt: str, out: Path, seconds: float, portrait: bool = True, timeout: int = 1800,
             loop: bool = False, seed: int = 42) -> Path:
    """Animate a still with LTX distilled (first-frame image-to-video), 480p-class, ~2 min per 5 s clip.
    loop=True also anchors the last frame to the same still: no drift or zoom, and the clip loops seamlessly."""
    import os

    from cvm import gpu
    from cvm.config import settings

    w, h = (480, 832) if portrait else (832, 480)
    frames = max(25, min(121, (int(seconds * LTX_FPS) + 7) // 8 * 8 + 1))  # LTX needs 8k+1 frames
    raw = out.with_suffix(".ltx.mp4")
    bin_ = settings.models_dir / "LTX2MLX" / ".venv" / "bin" / "ltx-2-mlx"
    with gpu.section():
        subprocess.run(
            [str(bin_), "generate", "--distilled", "--model", str(_ltx_model()), "--image", str(image), "0", "1.0",
             *(["--image", str(image), "last", "1.0", "guide"] if loop else []),
             "-p", prompt, "-W", str(w), "-H", str(h), "-f", str(frames), "--frame-rate", str(LTX_FPS), "--seed", str(seed),
             "-o", str(raw)],
            check=True, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "HF_HUB_OFFLINE": "1"},  # never a surprise multi-GB download from a job
        )
    # LTX also generates sound; the video keeps only the picture (narration and music are mixed later)
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-i", str(raw), "-an", "-c:v", "copy", str(out)],
                   check=True, capture_output=True)
    raw.unlink(missing_ok=True)
    if not out.exists():
        raise RuntimeError("ltx-2-mlx produced no file")
    return out


def animate_clip(image: Path, prompt: str, out: Path, seconds: float, portrait: bool = True, steps: int = 20,
                 loop: bool = False, seed: int = 42, purpose: str = "realistic") -> tuple[Path, str]:
    """Image-to-video with the engine picked for `purpose` (cvm/engines.py: kids | realistic | cartoon; LTX when
    installed, else Wan). Returns (clip, engine). loop: end on the starting still (LTX only; avatars' idle loops)."""
    from cvm import engines
    from cvm.models_registry import MLX

    if not MLX:  # CUDA: this machine's animation tier (cvm/visuals/video_cuda.py)
        from cvm.visuals import video_cuda

        eng = engines.feature_engine("animation")
        return video_cuda.clip(eng, image, prompt, out, seconds, portrait, seed=seed), video_cuda.LABELS.get(eng, str(eng))
    if engines.video_engine(purpose) == "ltx" and ltx_ready():
        return ltx_clip(image, prompt, out, seconds, portrait, loop=loop, seed=seed), f"ltx-{ltx_version()}"
    return wan_clip(image, prompt, out, seconds, portrait, steps, seed=seed), "wan2.2-ti2v-5b"


def source_clip(url: str, out_dir: Path, start: float = 0, length: float = 20) -> Path:
    """Short excerpt of a video (react style) via yt-dlp; only the needed section is downloaded."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = hashlib.sha1(f"{url}{start}{length}".encode()).hexdigest()[:16]
    existing = list(out_dir.glob(f"{stem}.*"))
    if existing:
        return existing[0]
    subprocess.run(
        [str(YTDLP), "-f", "bv*[height<=1080]+ba/b[height<=1080]", "--merge-output-format", "mp4",
         "--download-sections", f"*{start}-{start + length}", "-o", str(out_dir / f"{stem}.%(ext)s"), url],
        check=True, capture_output=True, text=True,
    )
    return next(out_dir.glob(f"{stem}.*"))
