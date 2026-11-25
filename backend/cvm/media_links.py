"""Links pasted on /fabrica/novo (Phase 3 of docs/superpowers/plans/2026-11-24-painel-reestruturacao.md: YouTube;
Phase 6: Twitch VODs/clips, Kick VODs/clips (experimental), TikTok and Instagram public posts, cvm/lives/platforms.py).

Each link becomes material of the new project, like an uploaded file, through the Crescer code (lives/ingest.probe,
grow/discover.download + attach_source). The download always runs in the WORKER (`tasks.link_projects`), never in an
API thread: POST /projects creates the project(s) without a job, the page uploads its files, POST /projects/start
hands the links to the worker, and the job only starts once every link is attached. A failed download deletes the
project(s) of that request (nothing half-made is left), and the error shows on /fabrica/novo.

Per link: `cut` "full" (the whole video) | "highlights" (best moments, worked out at the script step) | "ranges"
(from–to stretches chosen by hand, kept as segments_auto False); `keep_audio` = its own sound plays in its scenes.
State per request in Setting "fabrica_links" {items: [{key, title, project_ids, links, status: held|working|review|ok|error,
error, at}]} (last 20).
"""

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from cvm.config import settings
from cvm.models import Asset, Channel, Project, Setting

log = logging.getLogger(__name__)
KEY = "fabrica_links"
KEEP = 20
CUTS = ("full", "highlights", "ranges")


def normalize(links: list | None) -> list[dict]:
    """Checked links → [{url, original_url, platform, video_id, cut, keep_audio, segments?}]; ValueError (pt-BR) on
    anything else. video_id = YouTube's bare id, else "<platform>:<id>" (None for a TikTok short link until yt-dlp
    resolves it); url = the canonical page, original_url = what was pasted."""
    from cvm.api.media import manual_segments
    from cvm.lives import platforms

    out = []
    for raw in links or []:
        raw = raw if isinstance(raw, dict) else {"url": raw}
        d = platforms.detect(str(raw.get("url") or ""))
        cut = raw.get("cut") or "full"
        if cut not in CUTS:
            raise ValueError("cut: full | highlights | ranges")
        item = {"url": d["url"], "original_url": d["original_url"], "platform": d["platform"],
                "video_id": platforms.uid(d["platform"], d["id"]) if d["id"] else None, "cut": cut,
                "keep_audio": bool(raw.get("keep_audio"))}
        if cut == "ranges":
            item["segments"] = manual_segments(raw.get("ranges"))["segments"]
        out.append(item)
    return out


def _row(session: Session) -> Setting:
    row = session.get(Setting, KEY)
    if not row:
        row = Setting(key=KEY, value={"items": []})
        session.add(row)
    return row


def _save(session: Session, entry: dict) -> dict:
    row = _row(session)
    items = [e for e in (row.value or {}).get("items", []) if e.get("key") != entry["key"]]
    row.value = {"items": [entry, *items][:KEEP]}
    flag_modified(row, "value")
    session.add(row)
    session.commit()
    if entry.get("status") not in ("held", "working"):
        from cvm.lives import progress

        progress.clear(session, progress.fabrica_key(entry["key"]))
    return entry


def entries(session: Session) -> list[dict]:
    row = session.get(Setting, KEY)
    return list((row.value or {}).get("items", [])) if row else []


def entry_for(session: Session, project_id: int) -> dict | None:
    return next((e for e in entries(session) if project_id in (e.get("project_ids") or [])), None)


def hold(session: Session, projects: list[Project], links: list[dict]) -> dict:
    """Remember the links of just-created projects (no job yet) until POST /projects/start."""
    return _save(session, {"key": uuid.uuid4().hex[:10], "title": projects[0].title if projects else "",
                           "project_ids": [p.id for p in projects], "links": links, "status": "held", "error": None,
                           "at": datetime.now(UTC).isoformat()})


def dispatch(session: Session, entry: dict) -> dict:
    """Send a held request to the worker (downloads never run in an API thread)."""
    from cvm.workers.tasks import link_projects

    entry = _save(session, {**entry, "status": "working", "at": datetime.now(UTC).isoformat()})
    link_projects(entry["key"])
    return entry


def fetch(link: dict, channel: Channel | None):
    """(info, path): the video's metadata (refuses a live still on air) and its ≤1080p mp4, cached per channel.
    YouTube keeps its download with client fallbacks (discover.download); the other sites go through ingest.download."""
    from cvm.grow import discover
    from cvm.lives import ingest

    info = ingest.probe(link["url"])
    info["original_url"] = link.get("original_url") or info.get("original_url") or link["url"]
    dest = settings.data_dir / "lives" / str(channel.id if channel else "fabrica")
    if info.get("platform", "youtube") == "youtube":
        return info, discover.download(info["url"], dest)
    return info, ingest.download(info, dest)


def is_own(channel: Channel | None, info: dict) -> bool:
    """The user's own video: same YouTube channel id; on the other sites, the account saved in
    Channel.prefs.accounts {platform: handle} (e.g. {"twitch": "meucanal"}), compared without case or "@"."""
    if not channel:
        return False
    platform = info.get("platform") or "youtube"
    if platform == "youtube":
        return bool(channel.youtube_channel_id and channel.youtube_channel_id == info.get("channel_id"))
    handle = str(((channel.prefs or {}).get("accounts") or {}).get(platform) or "").lstrip("@").lower()
    return bool(handle) and handle in {str(info.get(k) or "").lstrip("@").lower() for k in ("channel_id", "uploader")}


def attach(session: Session, project: Project, link: dict, info: dict, path) -> Asset:
    """The downloaded video as project material: the user's own video (same YouTube channel) as his file, someone
    else's with the credit and its sound low under the voice (discover.attach_source); then the cut chosen."""
    from cvm.api.media import attach_file, manual_segments
    from cvm.grow import discover

    from cvm.lives.platforms import LABEL

    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    own = is_own(channel, info)
    site = LABEL.get(info.get("platform") or "youtube", "YouTube")
    title = info.get("title") or path.name
    if own:
        a = attach_file(session, project.id, path, path.name, f"seu vídeo do {site} “{title}”", keep_audio=link["keep_audio"])
        a.source, a.license = f"seu canal ({info['url']})", "próprio"
    else:
        a = discover.attach_source(session, project.id, path, title, f"{info.get('uploader') or site} ({site})",
                                   info["url"])
        mark_third_party(session, project, info, site)
    meta = {**(a.meta or {}), "keep_audio": link["keep_audio"], "link": info["url"],
            "platform": info.get("platform") or "youtube", "original_url": info.get("original_url") or info["url"]}
    if not own:
        meta["audio_under"] = not link["keep_audio"]  # its sound plays in its scenes, or low under the voice
    if link["cut"] == "ranges":
        meta.update(manual_segments(link["segments"], meta.get("duration")))
    elif link["cut"] == "full" and project.media_cut == "highlights" and meta.get("duration"):
        # another file of this video uses best moments: this one still plays whole
        meta.update(manual_segments([{"start": 0, "end": meta["duration"]}], meta["duration"]))
    a.meta = meta
    session.add(a)
    session.commit()
    return a


def mark_third_party(session: Session, project: Project, info: dict, site: str) -> None:
    """Another creator's video: 🟡 in the title and the credit in the topic (the scriptwriter puts it on screen and
    the SEO agent in the description), like Crescer's react videos (cvm/lives/package.py)."""
    if not (project.title or "").startswith("🟡"):
        project.title = f"🟡 {project.title or info.get('title') or ''}".strip()[:200]
    who = info.get("uploader") or "outro criador"
    note = (f"Material de outro criador: {who} ({site}, {info['url']}). 🟡 crédito ao criador na tela e na descrição; "
            "o vídeo comenta, nunca só reposta.")
    if project.style == "live":  # "live" is the user's own lives only: another creator's → react
        project.style = "react"
    if info["url"] not in (project.topic or ""):
        project.topic = f"{project.topic or ''}\n\n{note}".strip()
    session.add(project)


def run(session: Session, key: str) -> None:
    """Worker side: download and attach every link to every project of the request, then start their jobs.
    Any failure deletes those projects (with the files already attached) and keeps the error for the page.
    Each step shows on the request ("Baixando N%", then the cut analysis: cvm/lives/progress.py)."""
    from cvm.lives import progress

    entry = next((e for e in entries(session) if e.get("key") == key), None)
    if not entry:
        return
    with progress.use(progress.setting_sink(progress.engine_of(session), progress.fabrica_key(key))):
        _run(session, entry)


def _run(session: Session, entry: dict) -> None:
    from cvm.api.projects import remove_project
    from cvm.lives import progress
    from cvm.suggest import start

    projects = [p for p in (session.get(Project, pid) for pid in entry.get("project_ids") or []) if p]
    fetched = []
    ch = None
    try:
        if not projects:
            raise RuntimeError("o projeto foi apagado antes de o link baixar")
        ch = session.get(Channel, projects[0].channel_id) if projects[0].channel_id else None
        links = entry.get("links") or []
        for n, link in enumerate(links, 1):
            progress.report(progress.DOWNLOAD, "Lendo o link" + (f" {n} de {len(links)}" if len(links) > 1 else ""))
            info, path = fetch(link, ch)
            fetched.append((link, info, path))
            for p in projects:
                attach(session, p, link, info, path)
    except Exception as e:  # noqa: BLE001 — shown on /fabrica/novo
        log.warning("links of %s failed: %s", entry.get("project_ids"), e)
        session.rollback()
        for p in projects:
            remove_project(session, p)
        _save(session, {**entry, "status": "error", "error": str(e)[:300], "at": datetime.now(UTC).isoformat()})
        return
    if review_highlights(session, entry, projects, ch, fetched):
        return  # the projects start once the owner approves the stretches in the cut editor
    for p in projects:
        start(session, p)
    _save(session, {**entry, "status": "ok", "error": None, "at": datetime.now(UTC).isoformat()})


def review_highlights(session: Session, entry: dict, projects: list[Project], channel: Channel | None,
                      fetched: list) -> bool:
    """A "Melhores momentos" link goes through the cut editor (cvm/lives/cuts.py, kind "segments") before the
    projects start: the approved stretches become their kept ranges. Not when the channel approves cuts by itself
    (Channel.prefs.auto_approve_cuts: the stretches are found at the script step, as before), and an analysis that
    fails starts the projects the old way instead of losing the request. True when a review now waits."""
    from cvm.lives import cuts

    hl = [(link, info, path) for link, info, path in fetched if link.get("cut") == "highlights"]
    if not hl or not channel or cuts.auto_approve(channel):
        return False
    link, info, path = hl[0]  # one editor per request; other highlight files keep the automatic ranges
    third = None if is_own(channel, info) else {"uploader": info.get("uploader"), "url": info.get("url"),
                                                 "channel_id": info.get("channel_id")}
    _save(session, {**entry, "status": "working", "error": None, "at": datetime.now(UTC).isoformat()})
    try:
        review = cuts.analyse(session, channel, info, Path(path), third_party=third, item_key=entry["key"],
                              source="fabrica", kind="segments",
                              options={"project_ids": [p.id for p in projects], "make_long": False}, min_shorts=1)
    except Exception as e:  # noqa: BLE001
        log.warning("cut review of %s failed, starting the old way: %s", entry.get("project_ids"), e)
        return False
    _save(session, {**entry, "status": "review", "review": review.id, "error": None,
                    "at": datetime.now(UTC).isoformat()})
    return True


def set_status(session: Session, key: str, status: str, error: str | None = None) -> None:
    """The cut editor finished (or dropped) a request held in review."""
    entry = next((e for e in entries(session) if e.get("key") == key), None)
    if entry:
        _save(session, {**entry, "status": status, "error": error, "at": datetime.now(UTC).isoformat()})
