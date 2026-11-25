"""'Produzir quando acabar' (Em alta > Lives, 2026-11-24): a live still on air is sent to the Crescer flow now and
packaged once its recording exists (best moments by loudness/motion + chat + viral score → 1 long video + Shorts,
cvm/lives/package.py).

The link carries the time of the request in its fragment ("…#cvm-after=<iso>"), so the waiting item in
Setting `lives:{channel}` keeps it across the hourly retries (lives_retry_tick → runs.retry_waiting):
- YouTube: the watch link stays the same after the live; ingest.probe says NotReady while it is on air/processing.
- Twitch / Kick: the link is the channel page. While the channel is live → NotReady; then the newest saved video
  (VOD) of the channel is used, provided it began after (request − LIVE_MAX_H), so an older VOD is never taken
  when the creator does not save lives.
- After WAIT_H without a recording the item becomes an error (runs.retry_waiting).
"""

import json
import subprocess
from datetime import UTC, datetime, timedelta

from cvm.lives import platforms
from cvm.lives.ingest import NotReady
from cvm.visuals.sources import YTDLP

MARK = "cvm-after="
WAIT_H = 24
LIVE_MAX_H = 12  # the live may have started this long before the click


def wait_url(url: str, at: datetime | None = None) -> str:
    base = strip(url)
    if (ch := platforms.live_channel(base)):
        base = ch["url"]
    return f"{base}#{MARK}{(at or datetime.now(UTC)).astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}"


def strip(url: str) -> str:
    return (url or "").split("#" + MARK)[0].strip()


def since_of(url: str) -> datetime | None:
    if MARK not in (url or ""):
        return None
    try:
        return datetime.strptime(url.split(MARK, 1)[1][:20], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def expired(url: str, now: datetime | None = None) -> bool:
    since = since_of(url)
    return bool(since and (now or datetime.now(UTC)) - since > timedelta(hours=WAIT_H))


def _json(run, args: list[str]) -> dict | None:
    r = run([str(YTDLP), *args], capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return None


def resolve(url: str, run=subprocess.run) -> str:
    """The link to read and download now. NotReady while the live is on air or its recording isn't there yet."""
    base = strip(url)
    ch = platforms.live_channel(base)
    if not ch:
        return base  # YouTube (or a VOD link): ingest.probe refuses it while it is still live
    on_air = _json(run, ["--dump-json", "--skip-download", "--no-playlist", ch["url"]])
    if on_air and (on_air.get("is_live") or on_air.get("live_status") == "is_live"):
        raise NotReady("a live ainda está no ar")
    listing = _json(run, ["--flat-playlist", "-J", "--playlist-end", "3", ch["archives"]]) or {}
    entries = [e for e in listing.get("entries") or [] if e.get("url")]
    if not entries:
        raise NotReady("a gravação da live ainda não apareceu no canal")
    vod = entries[0]["url"].split("#")[0]
    info = _json(run, ["--dump-json", "--skip-download", "--no-playlist", vod]) or {}
    if info.get("is_live") or info.get("live_status") == "is_live":
        raise NotReady("a live ainda está no ar")
    since, ts = since_of(url), info.get("timestamp")
    if since and ts and datetime.fromtimestamp(ts, UTC) < since - timedelta(hours=LIVE_MAX_H):
        raise NotReady("a gravação desta live ainda não apareceu; o último vídeo salvo do canal é de antes")
    return vod
