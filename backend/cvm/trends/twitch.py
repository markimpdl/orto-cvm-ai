"""Twitch lives through the official, free Helix API (Em alta → Lives, and followed Twitch channels).

Credentials: a free app at dev.twitch.tv/console (client id + secret), read from CVM_TWITCH_CLIENT_ID /
CVM_TWITCH_CLIENT_SECRET in .env, else from the macOS Keychain (service "cvm-twitch", accounts "client_id" and
"client_secret"). They are never stored in the DB, logged or returned by the API. Without them every call raises
NotConfigured, which the UI shows as "configure a Twitch".

The app access token (client credentials, ~60 days) is kept only in memory.
"""

import logging
import subprocess
import sys
import time

import httpx

from cvm.config import settings

log = logging.getLogger(__name__)

HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
KEYCHAIN_SERVICE = "cvm-twitch"
NOT_CONFIGURED = ("Configure a Twitch para ver as lives de lá: crie um app grátis em dev.twitch.tv/console e coloque "
                  "CVM_TWITCH_CLIENT_ID e CVM_TWITCH_CLIENT_SECRET no .env (ou no Keychain, serviço cvm-twitch).")
LANG = {"BR": "pt", "AE": "ar", "US": "en"}

_token: dict = {}  # {"value", "until", "client_id"}: in memory only


class NotConfigured(RuntimeError):
    pass


def _keychain(account: str) -> str | None:
    if sys.platform != "darwin":
        return None
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
                       capture_output=True, text=True)
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def credentials() -> tuple[str, str] | None:
    from cvm import keystore

    cid = getattr(settings, "twitch_client_id", None) or keystore.get("twitch_client_id") or _keychain("client_id")
    secret = (getattr(settings, "twitch_client_secret", None) or keystore.get("twitch_client_secret")
              or _keychain("client_secret"))
    return (cid, secret) if cid and secret else None


def configured() -> bool:
    return credentials() is not None


def _headers(client: httpx.Client) -> dict:
    creds = credentials()
    if not creds:
        raise NotConfigured(NOT_CONFIGURED)
    cid, secret = creds
    if not (_token.get("client_id") == cid and _token.get("until", 0) > time.time() + 60):
        r = client.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"})
        if r.status_code in (400, 401, 403):
            raise NotConfigured("A Twitch recusou o client id/secret. Confira o app em dev.twitch.tv/console.")
        r.raise_for_status()
        d = r.json()
        _token.update(value=d["access_token"], until=time.time() + int(d.get("expires_in", 3600)), client_id=cid)
    return {"Client-Id": cid, "Authorization": f"Bearer {_token['value']}"}


def _get(client: httpx.Client, path: str, params: list | dict) -> dict:
    r = client.get(f"{HELIX}{path}", params=params, headers=_headers(client))
    if r.status_code == 401:  # token revoked/expired early: once more with a new one
        _token.clear()
        r = client.get(f"{HELIX}{path}", params=params, headers=_headers(client))
    r.raise_for_status()
    return r.json()


def parse_streams(payload: dict) -> list[dict]:
    """Helix /streams → the common item shape of Em alta (same keys as cvm/grow/discover._item)."""
    out = []
    for s in (payload or {}).get("data") or []:
        login = s.get("user_login") or ""
        if not login or s.get("type", "live") != "live":  # "" = stream in an error state
            continue
        thumb = (s.get("thumbnail_url") or "").replace("{width}", "480").replace("{height}", "270")
        out.append({"id": f"twitch:{s.get('id') or login}", "platform": "twitch", "kind": "live",
                    "url": f"https://www.twitch.tv/{login}", "title": s.get("title") or "",
                    "channel": s.get("user_name") or login, "channel_url": f"https://www.twitch.tv/{login}",
                    "handle": login, "thumb": thumb or None, "viewers": s.get("viewer_count"), "views": None,
                    "duration": None, "game": s.get("game_name") or "", "language": s.get("language") or "",
                    "started_at": s.get("started_at")})
    return out


def _game_id(client: httpx.Client, name: str) -> str | None:
    d = _get(client, "/games", {"name": name})
    if (d.get("data") or []):
        return d["data"][0].get("id")
    d = _get(client, "/search/categories", {"query": name, "first": 1})
    return ((d.get("data") or [{}])[0] or {}).get("id")


def top_streams(topic: str = "", region: str = "", n: int = 20, client: httpx.Client | None = None) -> list[dict]:
    """Most watched streams now, optionally in the region's language and for a game/category named like `topic`.
    A topic that is not a category filters the top streams by title instead."""
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        params: list = [("first", str(min(100, max(n, 20) if not topic else 100)))]
        if region in LANG:
            params.append(("language", LANG[region]))
        gid = _game_id(client, topic) if topic.strip() else None
        if gid:
            params.append(("game_id", gid))
        items = parse_streams(_get(client, "/streams", params))
        if topic.strip() and not gid:
            words = topic.lower().split()
            items = [i for i in items if all(w in f"{i['title']} {i['game']}".lower() for w in words)]
        return sorted(items, key=lambda x: -(x.get("viewers") or 0))[:n]
    finally:
        if own:
            client.close()


def live_of(logins: list[str], client: httpx.Client | None = None) -> list[dict]:
    """Followed Twitch channels that are live now."""
    logins = [x.strip().lstrip("@").lower() for x in logins if x.strip()][:100]
    if not logins:
        return []
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        return parse_streams(_get(client, "/streams", [("user_login", x) for x in logins]))
    finally:
        if own:
            client.close()


def safe_top(topic: str = "", region: str = "", n: int = 20) -> dict:
    """{items, error, configured}: never raises (the Lives tab shows the message)."""
    try:
        return {"items": top_streams(topic, region, n), "error": None, "configured": True}
    except NotConfigured as e:
        return {"items": [], "error": str(e), "configured": False}
    except Exception as e:  # noqa: BLE001
        log.warning("twitch: %s", type(e).__name__)
        return {"items": [], "error": f"A Twitch não respondeu agora ({type(e).__name__}).", "configured": True}
