"""Em alta → Seguindo: keywords and channels the user follows (Setting "follow").

    {"keywords": ["gta 6", ...], "channels": [{"platform": "youtube|twitch|kick", "handle": "gaules"}], "region": "BR"}

`tick()` (worker, every 30 min) brings what they published or have on air into the trend list as Trend rows with
source="follow" (meta.followed says which keyword/channel matched); a title or link already in the trend list is
skipped, and one already brought in is only refreshed. Every source fails on its own.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, col, select

from cvm.models import Setting, Trend
from cvm.trends.sources import normalize_key

log = logging.getLogger(__name__)

KEY = "follow"
SOURCE = "follow"
PLATFORMS = ("youtube", "twitch", "kick")
MAX_KEYWORDS = 20
MAX_CHANNELS = 30
PER_KEYWORD = 3
FRESH = timedelta(hours=2)  # how long a followed match stays on the list without being seen again
HANDLE = re.compile(r"^[\w.\-]{2,60}$")


def _handle(platform: str, raw: str) -> str:
    """'@Gaules', 'https://www.twitch.tv/gaules', 'kick.com/gaules' → 'gaules' (YouTube keeps its case)."""
    h = str(raw or "").strip().rstrip("/")
    h = re.sub(r"^https?://(www\.|m\.)?", "", h)
    h = re.sub(r"^(youtube\.com/|twitch\.tv/|kick\.com/)", "", h)
    h = h.split("?")[0].split("/")[0].lstrip("@")
    return h if platform == "youtube" else h.lower()


def clean(value: dict) -> dict:
    kws: list[str] = []
    for k in value.get("keywords") or []:
        k = " ".join(str(k).split())[:80]
        if len(k) >= 2 and k.lower() not in {x.lower() for x in kws}:
            kws.append(k)
    chans: list[dict] = []
    for c in value.get("channels") or []:
        if not isinstance(c, dict) or c.get("platform") not in PLATFORMS:
            continue
        h = _handle(c["platform"], c.get("handle", ""))
        if HANDLE.match(h) and not any(x["platform"] == c["platform"] and x["handle"].lower() == h.lower() for x in chans):
            chans.append({"platform": c["platform"], "handle": h})
    r = str(value.get("region") or "").upper()
    region = r if r == "GLOBAL" or (len(r) == 2 and r.isalpha()) else "BR"
    return {"keywords": kws[:MAX_KEYWORDS], "channels": chans[:MAX_CHANNELS], "region": region}


def get(session: Session) -> dict:
    row = session.get(Setting, KEY)
    v = clean(row.value if row else {})
    return {**v, "last_run": (row.value or {}).get("last_run") if row else None,
            "last_error": (row.value or {}).get("last_error") if row else None}


def put(session: Session, value: dict) -> dict:
    row = session.get(Setting, KEY) or Setting(key=KEY, value={})
    row.value = {**(row.value or {}), **clean(value)}
    flag_modified(row, "value")
    session.add(row)
    session.commit()
    return get(session)


def _mark(session: Session, **fields) -> None:
    row = session.get(Setting, KEY) or Setting(key=KEY, value={})
    row.value = {**(row.value or {}), **fields}
    flag_modified(row, "value")
    session.add(row)
    session.commit()


# ---------- fetching (each returns Em alta items: {url, title, channel, views, viewers, platform, ...}) ----------

def _youtube_keyword(keyword: str, region: str) -> list[dict]:
    from cvm.grow import discover

    items = [i for i in (discover._item(e) for e in discover._search(keyword, discover.SP["week"], "" if region == "GLOBAL" else region)) if i]
    return sorted(items, key=lambda x: -(x.get("views") or 0))[:PER_KEYWORD]


def _youtube_channel(handle: str) -> list[dict]:
    """Newest video and newest live of a YouTube channel (@handle or UC… id)."""
    from cvm.grow import discover

    base = f"https://www.youtube.com/{'channel/' + handle if handle.startswith('UC') and len(handle) == 24 else '@' + handle}"
    out = []
    for tab in ("videos", "streams"):
        try:
            entries = discover._search_url(f"{base}/{tab}", 1)
        except Exception as e:  # noqa: BLE001 — a channel without lives has no streams tab
            log.info("follow youtube %s/%s: %s", handle, tab, str(e)[:120])
            continue
        out += [i for i in (discover._item(e) for e in entries) if i]
    return out


def fetch(cfg: dict) -> list[tuple[str, dict]]:
    """[(followed label, item)] for everything followed; a failing source is logged and skipped."""
    from cvm.trends import kick, twitch

    found: list[tuple[str, dict]] = []
    errors: list[str] = []
    for kw in cfg["keywords"]:
        try:
            found += [(f"palavra: {kw}", i) for i in _youtube_keyword(kw, cfg["region"])]
        except Exception as e:  # noqa: BLE001
            errors.append(f"{kw}: {str(e)[:80]}")
    by = {p: [c["handle"] for c in cfg["channels"] if c["platform"] == p] for p in PLATFORMS}
    for h in by["youtube"]:
        found += [(f"canal YouTube: {h}", i) for i in _youtube_channel(h)]
    if by["twitch"]:
        try:
            found += [(f"canal Twitch: {i['handle']}", i) for i in twitch.live_of(by["twitch"])]
        except twitch.NotConfigured as e:
            errors.append(str(e))
        except Exception as e:  # noqa: BLE001
            errors.append(f"Twitch: {type(e).__name__}")
    for h in by["kick"]:
        try:
            if it := kick.channel_live(h):
                found.append((f"canal Kick: {h}", it))
        except Exception as e:  # noqa: BLE001
            errors.append(f"Kick {h}: {type(e).__name__}")
    if errors:
        log.info("follow: %s", "; ".join(errors)[:500])
    return found


def store(session: Session, found: list[tuple[str, dict]], region: str) -> int:
    """Matches → Trend(source="follow"). Skips what the trend list already has (same title key or same link from any
    source); refreshes a match brought in before. Returns how many new trends were added."""
    ts = datetime.now(UTC)
    added = 0
    for label, it in found:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        key = normalize_key(title)
        mine = session.exec(select(Trend).where(Trend.source == SOURCE, Trend.key == key)).first()
        if not mine:
            same = (col(Trend.key) == key) | (col(Trend.url) == it["url"]) if it.get("url") else col(Trend.key) == key
            others = session.exec(select(Trend).where(Trend.source != SOURCE).where(same)).first()
            if others:
                continue
            mine = Trend(source=SOURCE, region=region, key=key, title=title[:300], url=it.get("url"))
            added += 1
        metric = it.get("viewers") or it.get("views")
        mine.last_seen = ts
        mine.meta = {**(mine.meta or {}), "followed": label, "platform": it.get("platform") or "youtube",
                     "channel": it.get("channel"), "kind": it.get("kind"), "thumb": it.get("thumb"),
                     "views": it.get("views"), "viewers": it.get("viewers"), "metric": metric}
        session.add(mine)
        session.flush()
    session.commit()
    return added


def tick(session: Session, fetcher=fetch) -> int:
    cfg = get(session)
    if not cfg["keywords"] and not cfg["channels"]:
        return 0
    try:
        n = store(session, fetcher(cfg), cfg["region"])
        _mark(session, last_run=datetime.now(UTC).isoformat(), last_error=None)
        return n
    except Exception as e:  # noqa: BLE001
        log.warning("follow tick failed: %s", e)
        _mark(session, last_run=datetime.now(UTC).isoformat(), last_error=str(e)[:200])
        return 0
