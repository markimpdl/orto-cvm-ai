"""Kick lives (EXPERIMENTAL): Kick's official API needs an OAuth app; this reads the public JSON the kick.com site itself
uses (`/stream/livestreams/<lang>` for the most watched, `/api/v2/channels/<slug>` for one channel). Those endpoints
are undocumented and sit behind Cloudflare, so they may change or refuse us at any time: every call fails soft and the
UI marks the source "experimental".
"""

import logging

import httpx

log = logging.getLogger(__name__)

SITE = "https://kick.com"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 "
                    "Safari/537.36", "Accept": "application/json"}
# the site's list ignores its language path; the language comes back on each stream as a name
LANG = {"BR": "Portuguese", "AE": "Arabic", "US": "English"}
EXPERIMENTAL = "Kick é experimental: usa a página pública do site, que pode mudar ou bloquear sem aviso."


def _item(ls: dict, channel: dict | None = None) -> dict | None:
    ch = channel or ls.get("channel") or {}
    slug = ch.get("slug") or ""
    if not slug or ls.get("is_live") is False:
        return None
    user = ch.get("user") or {}
    thumb = ls.get("thumbnail") or {}
    cats = ls.get("categories") or []
    return {"id": f"kick:{ls.get('id') or slug}", "platform": "kick", "kind": "live", "url": f"{SITE}/{slug}",
            "title": ls.get("session_title") or "", "channel": user.get("username") or slug,
            "channel_url": f"{SITE}/{slug}", "handle": slug,
            "thumb": (thumb.get("src") or thumb.get("url")) if isinstance(thumb, dict) else None,
            "viewers": ls.get("viewer_count", ls.get("viewers")), "views": None, "duration": None,
            "game": (cats[0] or {}).get("name", "") if cats else "", "language": ls.get("language") or "",
            "started_at": ls.get("start_time")}


def parse_livestreams(payload: dict, region: str = "", topic: str = "") -> list[dict]:
    """The site's livestream list → Em alta items, filtered by the region's language and the topic words."""
    items = [i for i in (_item(x) for x in (payload or {}).get("data") or [] if isinstance(x, dict)) if i]
    if region in LANG:
        items = [i for i in items if i["language"] == LANG[region]]
    words = topic.lower().split()
    if words:
        items = [i for i in items if all(w in f"{i['title']} {i['game']}".lower() for w in words)]
    return sorted(items, key=lambda x: -(x.get("viewers") or 0))


def parse_channel(payload: dict) -> dict | None:
    """One channel's page → its live (None when offline)."""
    ls = (payload or {}).get("livestream")
    return _item(ls, payload) if isinstance(ls, dict) else None


def top_streams(topic: str = "", region: str = "", n: int = 20, client: httpx.Client | None = None) -> list[dict]:
    own = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True, headers=UA)
    try:
        items: list[dict] = []
        for page in (1, 2, 3):  # the list is global: a few pages so a language/topic filter still finds some
            r = client.get(f"{SITE}/stream/livestreams/pt", params={"page": page, "limit": 50, "sort": "desc"})
            r.raise_for_status()
            items += parse_livestreams(r.json(), region, topic)
            if len(items) >= n:
                break
        seen: set[str] = set()
        return [i for i in sorted(items, key=lambda x: -(x.get("viewers") or 0))
                if not (i["id"] in seen or seen.add(i["id"]))][:n]
    finally:
        if own:
            client.close()


def channel_live(slug: str, client: httpx.Client | None = None) -> dict | None:
    own = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True, headers=UA)
    try:
        r = client.get(f"{SITE}/api/v2/channels/{slug.strip().lstrip('@').lower()}")
        r.raise_for_status()
        return parse_channel(r.json())
    finally:
        if own:
            client.close()


def safe_top(topic: str = "", region: str = "", n: int = 20) -> dict:
    """{items, error, experimental}: never raises."""
    try:
        return {"items": top_streams(topic, region, n), "error": None, "experimental": True}
    except Exception as e:  # noqa: BLE001
        log.warning("kick: %s", e)
        return {"items": [], "error": f"A Kick não respondeu agora ({type(e).__name__}). Ela é experimental.",
                "experimental": True}
