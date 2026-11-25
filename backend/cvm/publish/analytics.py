"""YouTube Analytics (free API, OAuth per channel) — docs/plano-canais-e-voz.md, Phase 8.

One Google sign-in per channel, started from the channel panel (POST /channels/{id}/stats/connect): the consent page
redirects to this API (GET /oauth/google/callback), which keeps only the refresh token, in the macOS Keychain (the
`security` CLI) or the system keyring elsewhere, under the channel's `account_ref`. Tokens are never logged or returned.

The OAuth client is the same free "Desktop app" client used for uploads: CVM_GOOGLE_CLIENT_ID / CVM_GOOGLE_CLIENT_SECRET
in .env, or data/secrets/youtube_client_secret.json.

Snapshots (channel totals, per-video 28-day numbers, top videos) live in Setting "channel_stats:<id>"; per-video
numbers are also copied to Publication.metrics["analytics"].
"""

import base64
import hashlib
import json
import logging
import secrets
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

import httpx
from sqlmodel import Session, select

from cvm.config import settings
from cvm.models import Channel, Publication, Render, Setting, now
from cvm.publish.metrics import YT_ID

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly", "https://www.googleapis.com/auth/youtube.readonly"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
DATA_URL = "https://www.googleapis.com/youtube/v3"
CALLBACK_PATH = "/oauth/google/callback"
KEYCHAIN_SERVICE = "cvm-youtube-analytics"
STALE_S = 6 * 3600  # GET /channels/{id}/stats refreshes older snapshots
MIN_VIEWS_FOR_RETENTION = 20  # a 3-view video with 90% retention is noise
HOURS_TARGET, SUBS_TARGET, DEADLINE = 4000, 1000, "2027-01-31"  # YPP; after the deadline the goal is 8,000 h


class NotConnected(RuntimeError):
    pass


# ---------------- token store (Keychain on the Mac, keyring elsewhere, else a 0600 file) ----------------

def account_ref(channel: Channel) -> str:
    return channel.account_ref or f"cvm-channel-{channel.uid or channel.id}"


def _file(ref: str):
    return settings.data_dir / "secrets" / f"analytics_{ref.replace('/', '_')}.json"


def _keyring():
    try:
        import keyring  # optional; not a dependency

        return keyring
    except Exception:  # noqa: BLE001
        return None


def save_token(ref: str, token: str) -> None:
    if sys.platform == "darwin":
        # through stdin (security -i), so the token never shows up in the process list
        cmd = f'add-generic-password -U -s {KEYCHAIN_SERVICE} -a "{ref}" -w "{token}"\n'
        r = subprocess.run(["security", "-i"], input=cmd, text=True, capture_output=True)
        if r.returncode != 0:
            raise RuntimeError("não consegui gravar no Keychain")
        return
    kr = _keyring()
    if kr:
        kr.set_password(KEYCHAIN_SERVICE, ref, token)
        return
    f = _file(ref)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"refresh_token": token}))
    f.chmod(0o600)


def load_token(ref: str) -> str | None:
    if sys.platform == "darwin":
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", ref, "-w"],
                           capture_output=True, text=True)
        return r.stdout.strip() or None if r.returncode == 0 else None
    kr = _keyring()
    if kr:
        return kr.get_password(KEYCHAIN_SERVICE, ref)
    f = _file(ref)
    return json.loads(f.read_text()).get("refresh_token") if f.exists() else None


def delete_token(ref: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", ref], capture_output=True)
        return
    kr = _keyring()
    if kr:
        try:
            kr.delete_password(KEYCHAIN_SERVICE, ref)
        except Exception:  # noqa: BLE001
            pass
    _file(ref).unlink(missing_ok=True)


def connected(channel: Channel) -> bool:
    try:
        return bool(load_token(account_ref(channel)))
    except Exception:  # noqa: BLE001
        return False


# ---------------- OAuth (installed-app flow with a loopback redirect to this API) ----------------

def client() -> tuple[str, str]:
    cid, secret = settings.google_client_id, settings.google_client_secret
    if cid and secret:
        return cid, secret
    f = settings.data_dir / "secrets" / "youtube_client_secret.json"
    if f.exists():
        d = json.loads(f.read_text())
        d = d.get("installed") or d.get("web") or {}
        if d.get("client_id") and d.get("client_secret"):
            return d["client_id"], d["client_secret"]
    raise NotConnected("configure CVM_GOOGLE_CLIENT_ID e CVM_GOOGLE_CLIENT_SECRET no .env (cliente OAuth \"App para "
                       "computador\" do Google Cloud) ou data/secrets/youtube_client_secret.json")


def _pending(session: Session) -> Setting:
    return session.get(Setting, "oauth_pending") or Setting(key="oauth_pending", value={})


def auth_url(session: Session, channel: Channel, redirect_uri: str) -> str:
    cid, _ = client()
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    row = _pending(session)
    keep = {k: v for k, v in (row.value or {}).items() if time.time() - v.get("at", 0) < 3600}  # drop old attempts
    row.value = {**keep, state: {"channel_id": channel.id, "verifier": verifier, "redirect_uri": redirect_uri, "at": time.time()}}
    session.add(row)
    session.commit()
    return AUTH_URL + "?" + urlencode({
        "client_id": cid, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent select_account", "include_granted_scopes": "true",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })


def finish(session: Session, state: str, code: str, http=httpx) -> Channel:
    """Exchange the code, check the Google account picked is this channel's YouTube channel, keep the refresh token."""
    row = _pending(session)
    pend = dict(row.value or {})
    p = pend.pop(state, None)
    row.value = pend
    session.add(row)
    session.commit()
    if not p:
        raise NotConnected("pedido de login expirado ou desconhecido; clique em Conectar de novo")
    channel = session.get(Channel, p["channel_id"])
    cid, secret = client()
    r = http.post(TOKEN_URL, data={"code": code, "client_id": cid, "client_secret": secret, "grant_type": "authorization_code",
                                   "redirect_uri": p["redirect_uri"], "code_verifier": p["verifier"]}, timeout=30)
    if r.status_code != 200:
        raise NotConnected(f"o Google recusou o código ({r.status_code})")
    tok = r.json()
    if not tok.get("refresh_token"):
        raise NotConnected("o Google não devolveu refresh token; remova o acesso do app na conta Google e conecte de novo")
    me = http.get(f"{DATA_URL}/channels", params={"part": "id,snippet", "mine": "true"},
                  headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=30)
    items = me.json().get("items", []) if me.status_code == 200 else []
    yt_id = items[0]["id"] if items else None
    if channel.youtube_channel_id and yt_id and yt_id != channel.youtube_channel_id:
        name = items[0].get("snippet", {}).get("title", yt_id)
        raise NotConnected(f"você entrou com o canal “{name}”, mas este é {channel.name}: escolha o canal certo na tela do Google")
    if not channel.account_ref:
        channel.account_ref = account_ref(channel)
    if yt_id and not channel.youtube_channel_id:
        channel.youtube_channel_id = yt_id
    session.add(channel)
    session.commit()
    save_token(account_ref(channel), tok["refresh_token"])
    _ACCESS.pop(account_ref(channel), None)
    return channel


_ACCESS: dict[str, tuple[str, float]] = {}  # account_ref -> (access token, expiry)


def access_token(channel: Channel, http=httpx) -> str:
    ref = account_ref(channel)
    hit = _ACCESS.get(ref)
    if hit and hit[1] > time.time() + 60:
        return hit[0]
    refresh = load_token(ref)
    if not refresh:
        raise NotConnected(f"{channel.name} não está conectado ao YouTube Analytics")
    cid, secret = client()
    r = http.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret, "refresh_token": refresh,
                                   "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        raise NotConnected(f"o token de {channel.name} não vale mais ({r.status_code}); conecte de novo")
    d = r.json()
    _ACCESS[ref] = (d["access_token"], time.time() + int(d.get("expires_in", 3600)))
    return d["access_token"]


# ---------------- reports ----------------

def _report(token: str, start: date, end: date, metrics: str, http=httpx, **extra) -> list[dict]:
    r = http.get(REPORTS_URL, params={"ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat(),
                                      "metrics": metrics, **extra},
                 headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    d = r.json()
    cols = [h["name"] for h in d.get("columnHeaders", [])]
    return [dict(zip(cols, row, strict=False)) for row in d.get("rows") or []]


def _titles(token: str, ids: list[str], http=httpx) -> dict[str, str]:
    out = {}
    for i in range(0, len(ids), 50):
        r = http.get(f"{DATA_URL}/videos", params={"part": "snippet", "id": ",".join(ids[i:i + 50])},
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if r.status_code == 200:
            out.update({it["id"]: it["snippet"]["title"] for it in r.json().get("items", [])})
    return out


def _watch_hours_365(token: str, start: date, end: date, http=httpx) -> float:
    """Public watch hours of the last 365 days as YPP counts them: without Shorts when the API can split them."""
    try:
        rows = _report(token, start, end, "estimatedMinutesWatched", http, dimensions="creatorContentType")
        if rows:
            return round(sum(r["estimatedMinutesWatched"] for r in rows if str(r.get("creatorContentType")).lower() != "shorts") / 60, 1)
    except httpx.HTTPStatusError:
        pass
    rows = _report(token, start, end, "estimatedMinutesWatched", http)
    return round((rows[0]["estimatedMinutesWatched"] if rows else 0) / 60, 1)


def video_url(video_id: str, short: bool = False) -> str:
    return f"https://youtube.com/shorts/{video_id}" if short else f"https://youtube.com/watch?v={video_id}"


def top_videos(videos: list[dict]) -> tuple[dict | None, dict | None]:
    """The most viewed video and the one with the best average view % (among videos with enough views)."""
    if not videos:
        return None, None
    by_views = max(videos, key=lambda v: v["views"])
    pool = [v for v in videos if v["views"] >= MIN_VIEWS_FOR_RETENTION] or [v for v in videos if v["views"] > 0] or videos
    by_ret = max(pool, key=lambda v: v.get("avg_view_pct") or 0)
    return ({"title": by_views["title"], "url": by_views["url"], "views": by_views["views"]},
            {"title": by_ret["title"], "url": by_ret["url"], "avg_view_pct": by_ret.get("avg_view_pct")})


def stats_key(channel_id: int) -> str:
    return f"channel_stats:{channel_id}"


def refresh(session: Session, channel: Channel, http=httpx, today: date | None = None) -> dict:
    """Pull 7/28/365-day numbers and per-video 28-day metrics; save the snapshot and copy per-video numbers onto
    the channel's publications."""
    token = access_token(channel, http)
    end = (today or datetime.now(UTC).date()) - timedelta(days=1)  # today's numbers are not in yet
    total = lambda days: _report(token, end - timedelta(days=days - 1), end, "views,estimatedMinutesWatched,subscribersGained", http)  # noqa: E731
    t7, t28 = total(7), total(28)
    per_video = _report(token, end - timedelta(days=27), end,
                        "views,estimatedMinutesWatched,averageViewPercentage,subscribersGained", http,
                        dimensions="video", sort="-views", maxResults=200)
    hours = _watch_hours_365(token, end - timedelta(days=364), end, http)
    subs = None
    r = http.get(f"{DATA_URL}/channels", params={"part": "statistics", "mine": "true"},
                 headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code == 200 and r.json().get("items"):
        st = r.json()["items"][0].get("statistics", {})
        subs = None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0))

    pubs = {}
    for p in session.exec(select(Publication).where(Publication.channel_id == channel.id)).all():
        m = YT_ID.search(p.url or "")
        if m:
            pubs[m.group(1)] = p
    titles = _titles(token, [v["video"] for v in per_video if v["video"] not in pubs], http)
    videos = []
    for v in per_video:
        p = pubs.get(v["video"])
        r_ = session.get(Render, p.render_id) if p else None
        title = ((r_.seo or {}).get("title") if r_ else None) or titles.get(v["video"]) or v["video"]
        short = bool(p and "shorts" in (p.url or ""))
        item = {"video": v["video"], "title": title, "url": p.url if p else video_url(v["video"], short),
                "views": int(v.get("views") or 0), "minutes": round(float(v.get("estimatedMinutesWatched") or 0)),
                "avg_view_pct": round(float(v.get("averageViewPercentage") or 0), 1),
                "subs_gained": int(v.get("subscribersGained") or 0), "publication_id": p.id if p else None}
        videos.append(item)
        if p:
            p.metrics = {**(p.metrics or {}), "analytics": {"days": 28, "views": item["views"], "minutes": item["minutes"],
                                                            "avg_view_pct": item["avg_view_pct"],
                                                            "subs_gained": item["subs_gained"], "updated_at": now().isoformat()}}
            session.add(p)
    top_v, top_r = top_videos(videos)
    snap = {"views_7d": int(t7[0]["views"]) if t7 else 0, "views_28d": int(t28[0]["views"]) if t28 else 0,
            "minutes_28d": round(float(t28[0]["estimatedMinutesWatched"])) if t28 else 0,
            "subs_gained_28d": int(t28[0]["subscribersGained"]) if t28 else 0,
            "watch_hours_365d": hours, "subscribers": subs, "top_views": top_v, "top_retention": top_r,
            "videos": videos[:50], "updated_at": now().isoformat(), "error": None}
    row = session.get(Setting, stats_key(channel.id)) or Setting(key=stats_key(channel.id))
    row.value = snap
    session.add(row)
    session.commit()
    return snap


def monetization(hours: float | None, subs: int | None, today: date | None = None) -> dict:
    late = (today or datetime.now(UTC).date()) > date.fromisoformat(DEADLINE)
    return {"hours_target": 8000 if late else HOURS_TARGET, "subs_target": SUBS_TARGET, "deadline": DEADLINE,
            "hours": hours, "subs": subs}


EMPTY = {"views_7d": None, "views_28d": None, "watch_hours_365d": None, "subscribers": None, "top_views": None,
         "top_retention": None, "updated_at": None}


def stats(session: Session, channel: Channel, http=httpx, force: bool = False) -> dict:
    """The channel panel's numbers (contract in cvm/api/stats.py). Never raises for missing auth or network."""
    from cvm.publish import weekly

    extra = {"suggestions": weekly.suggestions(session, channel), "title_pattern_suggestion": weekly.pattern(session, channel)}
    if not connected(channel):
        return {"connected": False, **EMPTY, "monetization": monetization(None, None), **extra}
    row = session.get(Setting, stats_key(channel.id))
    snap = dict(row.value) if row and row.value else {}
    age = (now() - datetime.fromisoformat(snap["updated_at"])).total_seconds() if snap.get("updated_at") else None
    if force or age is None or age > STALE_S:
        try:
            snap = refresh(session, channel, http)
        except Exception as e:  # noqa: BLE001 — show the last numbers with the reason they are old
            log.warning("analytics refresh failed for channel %s: %s", channel.id, type(e).__name__)
            snap = {**EMPTY, **snap, "error": str(e)[:300] if isinstance(e, NotConnected) else f"falha ao atualizar ({type(e).__name__})"}
    out = {k: snap.get(k) for k in EMPTY}
    return {"connected": True, **out, "error": snap.get("error"),
            "monetization": monetization(snap.get("watch_hours_365d"), snap.get("subscribers")), **extra}
