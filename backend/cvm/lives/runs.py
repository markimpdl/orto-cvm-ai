"""A pasted link (the user's own video/live or another creator's) or an uploaded recording → the day's package.

Nothing is picked up automatically (user's choice, 2026-11-21): the user sends each link. State per channel in the
Setting `lives:{channel_id}` → {"items": [{video_id, title, url, own, uploader, status: working|review|ok|error|waiting,
error, package, at}]} (last 30). A video already packaged, or one still "working" within LEASE_H, is refused
(AlreadyDone), so a double click or a second tab never makes two packages; a run that died leaves a stale
"working" that frees itself after LEASE_H.
"""

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from cvm.config import settings
from cvm.lives import progress
from cvm.lives.ingest import NotReady, download, probe
from cvm.models import Channel, Setting, now

log = logging.getLogger(__name__)
KEEP_ITEMS = 30
LEASE_H = 8  # a 3 h live: download + best-moments analysis take well under this
ID = re.compile(r"(?:v=|youtu\.be/|/live/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


class AlreadyDone(Exception):
    """This video was already packaged or is being packaged now."""


def setting_key(channel_id: int) -> str:
    return f"lives:{channel_id}"


def video_id_of(url: str) -> str | None:
    """The item key of a pasted link: YouTube's bare id, else "<platform>:<id>" (cvm/lives/platforms.py; a TikTok
    short link keys on its code until yt-dlp resolves it)."""
    m = ID.search(url or "")
    if m:
        return m.group(1)
    from cvm.lives import platforms

    try:
        d = platforms.detect(url)
    except ValueError:
        return None
    return platforms.uid(d["platform"], d["id"] or d["url"].rstrip("/").rsplit("/", 1)[-1])


def lives_dir(channel_id: int):
    return settings.data_dir / "lives" / str(channel_id)


def check_channel(channel: Channel) -> None:
    """Refuse before any download: only active channels get new videos (cvm/guards.py)."""
    from cvm import guards

    if channel.status != "active":
        raise guards.ChannelClosed(f"O canal {channel.name} está {channel.status}: ative-o para gerar vídeos.")


def _items(session: Session, channel: Channel) -> list[dict]:
    row = session.get(Setting, setting_key(channel.id))
    return [dict(i) for i in ((row.value or {}).get("items", []) if row else [])]


def _put(session: Session, channel: Channel, item: dict) -> dict:
    items = [i for i in _items(session, channel) if i["video_id"] != item["video_id"]]
    item = {k: v for k, v in item.items() if k != "progress"}  # progress lives in its own row (status())
    item = {**item, "at": now().isoformat()}
    row = session.get(Setting, setting_key(channel.id)) or Setting(key=setting_key(channel.id), value={})
    row.value = {"items": [item, *items][:KEEP_ITEMS]}
    flag_modified(row, "value")
    session.add(row)
    session.commit()
    if item.get("status") != "working":
        progress.clear(session, progress.lives_key(channel.id, item["video_id"]))
    return item


put = _put  # used by cvm/lives/cuts.py once the reviewed cuts are produced


def start_item(session: Session, channel: Channel, video_id: str, **fields) -> dict:
    """Claim the video for one run; AlreadyDone when it is packaged or being packaged."""
    old = next((i for i in _items(session, channel) if i["video_id"] == video_id), None)
    if old and old.get("status") == "ok":
        raise AlreadyDone("Esse vídeo já virou pacote.")
    if old and old.get("status") == "review":
        raise AlreadyDone("Os cortes desse vídeo já estão esperando a sua revisão.")
    if old and old.get("status") == "working":
        at = datetime.fromisoformat(old["at"])
        if now() - (at if at.tzinfo else at.replace(tzinfo=now().tzinfo)) < timedelta(hours=LEASE_H):
            raise AlreadyDone("Esse vídeo já está sendo processado.")
    return _put(session, channel, {**(old or {}), "video_id": video_id, "status": "working", "error": None, **fields})


def package_takes_examples(fn, name: str = "examples") -> bool:
    import inspect

    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def finish(session: Session, channel: Channel, item: dict, live: dict, video, package=None) -> dict:
    """Analyse a downloaded/uploaded video and record the result on the item. By default the cuts wait for the
    owner in the cut editor (cvm/lives/cuts.py: item status "review" + its review id); Channel.prefs
    auto_approve_cuts, or a `package` given by the caller, keeps the old automatic package.
    item["auto"] (daily suggestions) keeps the strict viral bar; a video the user sent always gives ≥ 1 Short."""
    from cvm.lives import cuts

    own = item.get("own", True)
    third = None if own else {"uploader": live.get("uploader"), "url": live.get("url"), "channel_id": live.get("channel_id")}
    from cvm.grow.viral_search import latest_examples

    if package is None and not cuts.auto_approve(channel):
        try:
            with progress.use(progress.setting_sink(progress.engine_of(session),
                                                    progress.lives_key(channel.id, item["video_id"]))):
                review = cuts.analyse(session, channel, live, video, third_party=third,
                                      examples=latest_examples(session, channel.id), item_key=item["video_id"],
                                      min_shorts=0 if item.get("auto") else 1)
            return _put(session, channel, {**item, "status": "review", "review": review.id, "error": None})
        except Exception as e:  # noqa: BLE001 — shown on the page; the user can send the link again
            log.warning("cut analysis of %s failed: %s", item["video_id"], e)
            return _put(session, channel, {**item, "status": "error", "error": str(e)[:500]})
    if package is None:
        from cvm.lives.package import make_package as package
    try:
        kw = ({"examples": latest_examples(session, channel.id), "min_shorts": 0 if item.get("auto") else 1}
              if package_takes_examples(package) else {})
        if package_takes_examples(package, "taste"):
            kw["taste"] = cuts.taste(session, channel.id)
        pkg = package(session, channel, live, video, third_party=third, **kw)
        return _put(session, channel, {**item, "status": "ok", "package": pkg})
    except Exception as e:  # noqa: BLE001 — shown on the page; the user can send the link again
        log.warning("package of %s failed: %s", item["video_id"], e)
        return _put(session, channel, {**item, "status": "error", "error": str(e)[:500]})


def process_link(session: Session, channel: Channel, url: str, title: str | None = None, probe=probe, fetch=download,
                 package=None, claimed: bool = False) -> dict:
    """Read, download and package one pasted link. claimed=True when the caller already ran start_item."""
    vid = item_key_for(url)
    item = {"video_id": vid, "url": url, "title": title or url}
    if not claimed:
        item = start_item(session, channel, vid, url=url, title=title or url)
    old = next((i for i in _items(session, channel) if i["video_id"] == vid), {})
    from cvm.lives.platforms import file_key

    local = lives_dir(channel.id) / f"live_{file_key(vid)}.mp4"
    sink = progress.setting_sink(progress.engine_of(session), progress.lives_key(channel.id, vid))
    try:
        with progress.use(sink):
            live, video = _fetch(session, channel, item, old, vid, url, title, local, probe, fetch)
    except NotReady as e:
        return _put(session, channel, {**_item(session, channel, vid, item), "status": "waiting", "error": str(e)})
    except Exception as e:  # noqa: BLE001
        return _put(session, channel, {**_item(session, channel, vid, item), "status": "error", "error": str(e)[:500]})
    return finish(session, channel, _item(session, channel, vid, item), live, video, package)


def _item(session: Session, channel: Channel, vid: str, default: dict) -> dict:
    return next((i for i in _items(session, channel) if i["video_id"] == vid), default)


def item_key_for(url: str) -> str:
    """The run item's key of a pasted link (same as POST lives/link, cvm/lives/after_live)."""
    from cvm.lives import after_live

    return url if after_live.since_of(url) else (video_id_of(url) or url)


def _fetch(session: Session, channel: Channel, item: dict, old: dict, vid: str, url: str, title: str | None,
           local, probe, fetch):
    """(live, video): read the link (or reuse what is known) and download it; "Baixando N%" on the item."""
    from cvm.lives import after_live

    progress.report(progress.DOWNLOAD, "Lendo o link")
    if local.exists() and local.stat().st_size > 0 and old.get("uploader") is not None and "own" in old:
        # already downloaded and known: don't ask YouTube again (its anti-bot pause blocks even metadata reads)
        live = {"video_id": vid, "title": old.get("title") or url, "url": old.get("url") or url,
                "duration": float(old.get("duration") or 0), "uploader": old.get("uploader"),
                "channel_id": None if old.get("own") is False else channel.youtube_channel_id,
                "language": old.get("language")}
    else:
        # "Produzir quando acabar": the live's own recording once it exists (NotReady until then)
        live = probe(after_live.resolve(url) if after_live.since_of(url) else url)
    if title:
        live["title"] = title
    from cvm.media_links import is_own

    own = is_own(channel, live)  # YouTube channel id, or the account in prefs.accounts on the other sites
    item = _put(session, channel, {**item, "title": live["title"], "url": live["url"], "own": own,
                                   "uploader": live.get("uploader"), "duration": live.get("duration"),
                                   "language": live.get("language"), "status": "working"})
    progress.report(progress.DOWNLOAD, "Baixando", pct=0.0)
    video = fetch(live, lives_dir(channel.id))
    return live, video


def status(session: Session, channel: Channel) -> dict:
    """The items; a working one carries its step (`progress`, cvm/lives/progress.py)."""
    items = _items(session, channel)
    for i in items:
        if i.get("status") == "working":
            i["progress"] = progress.get(session, progress.lives_key(channel.id, i["video_id"]))
    return {"items": items}


def _requeue(session: Session, channel: Channel, item: dict) -> None:
    from cvm.api import grow

    start_item(session, channel, item["video_id"], url=item.get("url"), title=item.get("title"))
    grow._in_background(grow._job_link, channel.id, item["url"], None)


def retry_waiting(session: Session, channel: Channel, limit: int = 2) -> int:
    """Send again up to `limit` items that are waiting (live not finished, YouTube's anti-bot pause), oldest first."""
    from cvm.lives import after_live

    waiting = [i for i in _items(session, channel) if i.get("status") == "waiting" and i.get("url")]
    for item in [i for i in waiting if after_live.expired(i["url"])]:  # "Produzir quando acabar" gave up
        _put(session, channel, {**item, "status": "error", "error": f"esperei {after_live.WAIT_H} h e a gravação da "
                                f"live não ficou disponível ({item.get('error') or 'sem gravação'})"})
        waiting.remove(item)
    n = 0
    for item in sorted(waiting, key=lambda i: i.get("at") or ""):
        if n >= limit:
            break
        try:
            _requeue(session, channel, item)
            n += 1
        except AlreadyDone:
            continue
    return n


# --- "Excluir" on a sent video (owner, 2026-11-25): free the disk once the cuts are made ------------------------------

# a project at or past these steps no longer reads the recording (its render exists); anything earlier still does
RENDERED = {"awaiting_publish_review", "scheduled", "ready_for_manual_publish", "published"}


class InUse(Exception):
    """Projects still in production read this recording: nothing is deleted."""


def _video_files(channel: Channel, item: dict, reviews: list) -> list:
    """The recording(s) of an item: the review's video, the downloaded live_<key>.mp4 or the uploaded file. Only
    files under data/lives/ (never a project's or another folder's file)."""
    from pathlib import Path

    from cvm.lives.platforms import file_key

    root = (settings.data_dir / "lives").resolve()
    vid = item["video_id"]
    found = [Path(r.video_path) for r in reviews if r.video_path]
    d = lives_dir(channel.id)
    if vid.startswith("upload-"):
        found += sorted(d.glob(f"upload_{vid.removeprefix('upload-')}_*"))
    else:
        found.append(d / f"live_{file_key(vid)}.mp4")
    out = []
    for p in found:
        try:
            ok = p.resolve().is_relative_to(root)
        except OSError:
            ok = False
        if ok and p not in out:
            out.append(p)
    return out


def _companions(video, video_id: str) -> list:
    """What the pipeline left beside a recording: proxies (_proxy480/_proxy360), transcripts, yt-dlp parts, the chat
    replay. Every name starts with this recording's own stem or id, so another item's files never match."""
    stem, d = video.stem, video.parent
    out = [video, *d.glob(f"{stem}.*"), *d.glob(f"{stem}_proxy*")]
    if video_id and "/" not in video_id:
        out += list(d.glob(f"{video_id}.live_chat.json*"))
    return out


def projects_using(session: Session, paths: list) -> list[dict]:
    """Projects with a library item on one of these files, and whether each still needs it (not rendered yet)."""
    from sqlmodel import col, select

    from cvm.models import Asset, Job, Project

    if not paths:
        return []
    rows = session.exec(select(Asset.project_id).where(col(Asset.path).in_([str(p) for p in paths]))).all()
    out = []
    for pid in sorted(set(rows)):
        if not session.get(Project, pid):
            continue
        job = session.exec(select(Job).where(Job.project_id == pid)).first()
        out.append({"project_id": pid, "state": job.state if job else None,
                    "needs_file": not (job and job.state in RENDERED)})
    return out


def delete_item(session: Session, channel: Channel, video_id: str) -> dict:
    """Remove a sent video: its list item, its cut review(s) (+ data/cuts/<id>.json) and its files (recording,
    proxies, transcript, chat). KeyError when unknown; ValueError while it is being processed; InUse (nothing
    deleted) while a project made from it is not rendered yet. → {deleted, freed (bytes), files}."""
    from sqlmodel import select

    from cvm.lives import cuts
    from cvm.models import CutReview

    item = next((i for i in _items(session, channel) if i["video_id"] == video_id), None)
    if not item:
        raise KeyError(video_id)
    if item.get("status") == "working":
        raise ValueError("Esse vídeo ainda está sendo baixado ou analisado. Espere terminar para excluir.")
    reviews = session.exec(select(CutReview).where(CutReview.channel_id == channel.id,
                                                   CutReview.item_key == video_id,
                                                   CutReview.source == "lives")).all()
    if any(r.status in ("analysing", "producing") for r in reviews):
        raise ValueError("Os cortes desse vídeo estão sendo preparados agora. Espere terminar para excluir.")
    videos = _video_files(channel, item, reviews)
    busy = [p for p in projects_using(session, videos) if p["needs_file"]]
    if busy:
        n = len(busy)
        raise InUse(f"Ainda tem {n} {'corte' if n == 1 else 'cortes'} em produção usando este vídeo. "
                    "Exclua depois que terminarem.")
    files = {f for v in videos for f in _companions(v, video_id)}
    freed, removed = 0, 0
    for f in files:
        try:
            if f.is_file():
                size = f.stat().st_size
                f.unlink()
                freed, removed = freed + size, removed + 1
        except OSError as e:
            log.warning("could not delete %s: %s", f, e)
    for r in reviews:
        cuts.data_path(r.id).unlink(missing_ok=True)
        session.delete(r)
    row = session.get(Setting, setting_key(channel.id))
    if row:
        row.value = {"items": [i for i in _items(session, channel) if i["video_id"] != video_id]}
        flag_modified(row, "value")
        session.add(row)
    session.commit()
    progress.clear(session, progress.lives_key(channel.id, video_id))
    return {"deleted": video_id, "freed": freed, "files": removed}
