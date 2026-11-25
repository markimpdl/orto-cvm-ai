"""Real footage of the subject for `footage` scenes: official gameplay/trailers for games, real video for events.

Only two kinds of YouTube clips are accepted, because everything else is someone else's work (or a leak):
- "cc": the video's own license is Creative Commons Attribution (checked on the video page, not just the search
  filter) — reusable with credit. Typical: public broadcasters, governments, NASA, agencies' CC uploads.
- "official": the uploader is one of the scene's `official_channels` (named by the scriptwriter, e.g. "Rockstar
  Games") — trailers and gameplay published by the owner. Can still get a Content ID claim (monetization, not a
  strike), so it is opt-in per channel (`Channel.allow_official_clips`).
Leaks, reactions and re-uploads are always refused. Wikimedia Commons videos (free licenses) are tried too.
Clips are short (≤ 8 s), muted under the narration, and credited on screen by the composer.
"""

import contextvars
import hashlib
import json
import logging
import re
import subprocess
from pathlib import Path
from urllib.parse import quote_plus

import httpx

from cvm.visuals import sources

log = logging.getLogger(__name__)
YTDLP = sources.YTDLP
MAX_CLIP_S = 8.0
CLIP_MIN_FOOTAGE = 0.235  # video frames score lower than curated photos
CC_FILTER = "EgIwAQ%253D%253D"  # YouTube search filter "Creative Commons"
BLOCK = re.compile(r"\b(leak\w*|vazou|vazad\w*|vazament\w*|filtrad\w*|react|reacts|reaction\w*|reagindo|reage|rumor\w*|fake)\b", re.I)
# TV shows re-uploaded by third parties under a wrong CC license
TV_BRANDS = ("globo", "fantástico", "fantastico", "jornal nacional", "record", "sbt", "band", "cnn", "bbc", "fox", "espn", "netflix")


# a folder where YouTube searches and metadata are kept (the editorial step sets it per project/language, so a
# curation send-back does not search again); per thread/context, so parallel jobs never share it
CACHE_DIR: contextvars.ContextVar[Path | None] = contextvars.ContextVar("footage_cache_dir", default=None)
_MISS = object()


def _cache_file(kind: str, key: str) -> Path | None:
    d = CACHE_DIR.get()
    return d / f"{kind}_{hashlib.sha1(key.encode()).hexdigest()[:20]}.json" if d else None


def _cached(kind: str, key: str):
    f = _cache_file(kind, key)
    if f and f.exists():
        try:
            return json.loads(f.read_text())
        except (OSError, ValueError):
            return _MISS
    return _MISS


def _store(kind: str, key: str, value) -> None:
    if f := _cache_file(kind, key):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(value, ensure_ascii=False))


def _ytdlp_json(args: list[str], timeout: int = 60) -> dict:
    out = subprocess.run([str(YTDLP), *args, "-J", "--no-warnings"], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-500:])
    return json.loads(out.stdout)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _acceptable_title(title: str) -> bool:
    return not BLOCK.search(title or "")


def youtube_candidates(query: str, official_channels: list[str], allow_official: bool, n: int = 10) -> list[dict]:
    key = json.dumps([query, sorted(official_channels), allow_official, n])
    if (hit := _cached("search", key)) is not _MISS:
        return hit
    found = _youtube_candidates(query, official_channels, allow_official, n)
    _store("search", key, found)
    return found


def _youtube_candidates(query: str, official_channels: list[str], allow_official: bool, n: int) -> list[dict]:
    found: list[dict] = []
    officials = {_norm(c) for c in official_channels}
    if allow_official and officials:
        d = _ytdlp_json(["--flat-playlist", "--playlist-end", str(n), f"ytsearch{n}:{query}"])
        for e in d.get("entries") or []:
            if _norm(e.get("channel")) in officials and _acceptable_title(e.get("title")) and (e.get("duration") or 0) >= 10:
                found.append({**e, "license_kind": "official"})
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={CC_FILTER}"
    d = _ytdlp_json(["--flat-playlist", "--playlist-end", str(n), url])
    for e in d.get("entries") or []:
        title, channel = e.get("title") or "", (e.get("channel") or "").lower()
        if not _acceptable_title(title) or not (10 <= (e.get("duration") or 0) <= 3600):
            continue
        if any(b in title.lower() and b not in channel for b in TV_BRANDS):
            continue  # someone else's TV show
        if sources.relevance(query, {"text": title}) < 0.5:
            continue  # CC search is loose; the title must be about the subject
        found.append({**e, "license_kind": "cc"})
    return found


def _confirm(entry: dict) -> dict | None:
    """Full metadata: the CC license must be on the video itself; official ones must still be from that channel."""
    key = f"{entry['license_kind']}|{entry['url']}"
    if (hit := _cached("confirm", key)) is not _MISS:
        return hit
    try:
        info = _ytdlp_json(["--skip-download", entry["url"]], timeout=60)
    except Exception as e:  # noqa: BLE001 — a network error is not remembered
        log.debug("metadata failed %s: %s", entry.get("url"), e)
        return None
    if entry["license_kind"] == "cc" and "creative commons" not in (info.get("license") or "").lower():
        _store("confirm", key, None)
        return None
    out = {**entry, "duration": info.get("duration") or entry.get("duration"), "thumbnail": info.get("thumbnail"),
            "upload_date": info.get("upload_date"),
            "channel": info.get("channel") or entry.get("channel"), "license": info.get("license") or "official upload"}
    _store("confirm", key, out)
    return out


def wikimedia_videos(query: str, client: httpx.Client, n: int = 5) -> list[dict]:
    r = client.get("https://commons.wikimedia.org/w/api.php", headers=sources.UA, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrsearch": f"{query} filetype:video",
        "gsrlimit": n, "prop": "imageinfo", "iiprop": "url|extmetadata|size"})
    r.raise_for_status()
    out = []
    for p in (r.json().get("query", {}).get("pages", {}) or {}).values():
        ii = p["imageinfo"][0]
        lic = (ii["extmetadata"].get("LicenseShortName", {}).get("value") or "").lower()
        if lic.startswith(sources.FREE_LICENSES) and (ii.get("duration") or 0) >= 4:
            out.append({"url": ii["url"], "title": p.get("title", ""), "channel": sources._strip_html(ii["extmetadata"].get("Artist", {}).get("value", ""))[:80],
                        "license": lic, "license_kind": "free", "duration": ii.get("duration")})
    return out


def _clip_window(key: str, duration: float, length: float, attempt: int = 0) -> float:
    """Somewhere in the middle 20–80 % (skips logos and end cards); different scenes get different parts."""
    frac = 0.2 + 0.6 * ((int(hashlib.sha1(key.encode()).hexdigest(), 16) + attempt * 377) % 1000) / 1000
    return max(0.0, min(duration * frac, duration - length - 1))


def clip_match(path: Path, query: str) -> float | None:
    """Best CLIP similarity of 3 frames of the clip to the query (None when CLIP isn't installed)."""
    from cvm.media import duration, run_ffmpeg
    from cvm.visuals import clip_rank

    if not clip_rank.available():
        return None
    d = duration(path)
    frames = []
    for i, t in enumerate((d * 0.2, d * 0.5, d * 0.8)):
        f = path.with_name(f"{path.stem}_f{i}.jpg")
        run_ffmpeg("-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=448:-1", str(f))
        frames.append(str(f))
    sims = [x for x in clip_rank.similarities(query, frames) if x is not None]
    for f in frames:
        Path(f).unlink(missing_ok=True)
    return max(sims) if sims else None


def _recency(entry: dict) -> int:
    d = entry.get("upload_date") or "0"
    return int(d[:4]) if d[:4].isdigit() else 0


def find_footage(scene: dict, dest: Path, allow_official: bool, used: set[str]) -> dict | None:
    v = scene["visual"]
    queries = [q for q in [v.get("clip_query"), *(v.get("search_terms") or [])] if q][:2]
    length = min(scene.get("target_seconds") or MAX_CLIP_S, MAX_CLIP_S)
    for q in queries:
        try:
            cands = youtube_candidates(q, v.get("official_channels") or [], allow_official)
        except Exception as e:  # noqa: BLE001
            log.warning("youtube search failed (%s): %s", q, e)
            cands = []
        # official first (exactly the subject), then CC; most viewed first within each
        cands.sort(key=lambda e: (e["license_kind"] != "official", -(e.get("view_count") or 0)))
        confirmed = [i for i in (_confirm(e) for e in cands[:6]) if i]
        # CC footage of events: newest first (an old flood is not this flood); official: as ranked above
        confirmed.sort(key=lambda i: (i["license_kind"] != "official", -_recency(i) if i["license_kind"] == "cc" else 0))
        for info in confirmed:
            for attempt in range(3):  # a window that doesn't show the subject → another part of the video
                start = _clip_window(f"{info['url']}{scene['index']}", float(info["duration"] or 30), length, attempt)
                key = f"{info['url']}@{int(start)}"
                if key in used:
                    continue
                try:
                    path = sources.source_clip(info["url"], dest / "clips", start=start, length=length)
                except Exception as ex:  # noqa: BLE001
                    log.warning("clip download failed %s: %s", info["url"], ex)
                    break
                sim = clip_match(Path(path), q)
                if sim is not None and sim < CLIP_MIN_FOOTAGE:
                    log.info("clip %s@%.0f off-topic (clip %.3f), trying another part", info["url"], start, sim)
                    continue
                used.add(key)
                return {"kind": "video", "path": str(path), "source": info["url"], "license": info["license"],
                        "credit": info.get("channel"), "license_kind": info["license_kind"], "title": info.get("title"),
                        "clip_start": round(start, 1), "clip": sim, "upload_date": info.get("upload_date"), "query": q}
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            try:
                for w in wikimedia_videos(q, client):
                    if w["url"] in used:
                        continue
                    raw = sources.download(w["url"], dest / "clips")
                    used.add(w["url"])
                    return {"kind": "video", "path": str(raw), "source": w["url"], "license": w["license"],
                            "credit": w["channel"] or "Wikimedia Commons", "license_kind": "free", "title": w["title"], "query": q}
            except Exception as ex:  # noqa: BLE001
                log.warning("wikimedia video failed (%s): %s", q, ex)
    return None
