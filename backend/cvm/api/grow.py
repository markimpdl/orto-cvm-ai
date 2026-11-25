"""Menu "Crescer" (docs/superpowers/specs/2026-11-21-clipagem-design.md), per channel:

GET  /grow/{id}/lives            → {items: [{video_id, title, url, own, uploader, status:
                                    working|review|ok|error|waiting, error, review, package: {long, shorts, moments}, at}]}
(how many Shorts a video gives is decided by its analysis: cvm/visuals/highlights.top_moments)
POST /grow/{id}/lives/link       {url, title?} → {started, video_id}: the user's own video/live or another
                                    creator's (nothing is picked up automatically); 409 closed channel / already done
POST /grow/{id}/lives/upload     multipart file (+ title) → {started, video_id}
DELETE /grow/{id}/lives?video_id= → {deleted, freed, files}: item + cut review + files; 409 while still in use
GET  /grow/{id}/monetization     → {hours_target, subs_target, deadline, hours, subs, connected, error, pace:
                                    {hours_per_day, subs_per_day, days_to_hours, days_to_subs}}
"""

import math
import re
import shutil
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from cvm.db import engine, get_session
from cvm.lives import runs
from cvm.models import Channel, Setting
from cvm.publish import analytics

router = APIRouter(prefix="/grow", tags=["grow"])


def _channel(session: Session, channel_id: int) -> Channel:
    c = session.get(Channel, channel_id)
    if not c:
        raise HTTPException(404)
    return c


@router.get("/{channel_id}/lives")
def lives(channel_id: int, session: Session = Depends(get_session)):
    return runs.status(session, _channel(session, channel_id))


@router.delete("/{channel_id}/lives")
def delete_live(channel_id: int, video_id: str, session: Session = Depends(get_session)):
    """"Excluir" a sent video: list item, cut review and its files on disk (runs.delete_item) → {deleted, freed,
    files}. 409 while it is processed or while a project made from it is not rendered yet (nothing deleted).
    The id goes as a query parameter: a "Produzir quando acabar" item is keyed by its URL."""
    c = _channel(session, channel_id)
    try:
        return runs.delete_item(session, c, video_id)
    except KeyError as e:
        raise HTTPException(404, "Esse vídeo não está mais na lista.") from e
    except (ValueError, runs.InUse) as e:
        raise HTTPException(409, str(e)) from e


def _in_background(fn, *args) -> None:
    """Run fn(session, channel, *args) in the WORKER (Huey task cvm.workers.tasks.grow_job): downloads, whisper and
    Claude take minutes, and the GPU lock must belong to the worker so an API restart never leaves it held."""
    from cvm.workers.tasks import grow_job

    grow_job(fn.__name__, list(args))


def _claim(session: Session, c: Channel, video_id: str, **fields) -> None:
    from cvm import guards

    try:
        runs.check_channel(c)
        runs.start_item(session, c, video_id, **fields)
    except guards.ChannelClosed as e:
        raise HTTPException(409, str(e)) from e
    except runs.AlreadyDone as e:
        raise HTTPException(409, str(e)) from e


class LinkIn(BaseModel):
    url: str
    title: str | None = None
    after_live: bool = False  # Em alta "Produzir quando acabar": a live on air, packaged once its recording exists


@router.post("/{channel_id}/lives/link")
def lives_link(channel_id: int, body: LinkIn, session: Session = Depends(get_session)):
    from cvm.lives import after_live

    c = _channel(session, channel_id)
    url = body.url.strip()
    if body.after_live or (not runs.video_id_of(url) and after_live.platforms.live_channel(url)):
        # a Twitch/Kick channel page pasted here is a live: wait for its recording like "Produzir quando acabar"
        if not (runs.video_id_of(url) or after_live.platforms.live_channel(url)):
            raise HTTPException(422, "Esse link não é de uma live do YouTube, da Twitch ou do Kick.")
        url = after_live.wait_url(url)
        vid = url  # one item per request: the channel link alone would clash with the channel's next live
    else:
        vid = runs.video_id_of(url)
    if not vid:
        raise HTTPException(422, "Cole o link de um vídeo ou live do YouTube, Twitch, Kick, TikTok ou Instagram.")
    title = (body.title or "").strip() or None
    _claim(session, c, vid, url=url, title=title or after_live.strip(url))
    _in_background(_job_link, c.id, url, title)
    return {"started": True, "video_id": vid}


@router.post("/{channel_id}/lives/upload")
def lives_upload(channel_id: int, file: UploadFile = File(...), title: str = Form(""),
                 session: Session = Depends(get_session)):
    c = _channel(session, channel_id)
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    vid = f"upload-{stamp}"
    live = {"video_id": vid, "title": title.strip() or (file.filename or "Live"), "url": ""}
    _claim(session, c, vid, title=live["title"], url="", own=True)
    safe = re.sub(r"[^\w.\-]+", "_", file.filename or "live.mp4")[-80:]
    d = runs.lives_dir(c.id)
    d.mkdir(parents=True, exist_ok=True)
    video = d / f"upload_{stamp}_{safe}"
    with video.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    _in_background(_job_upload, c.id, live, str(video))
    return {"started": True, "video_id": vid}


class ViralSettings(BaseModel):
    topics: list[str] = Field(default_factory=list, max_length=6)
    language: str = Field(default="pt", pattern=r"^(pt|en)$")
    daily_credits: int = Field(default=40, ge=0, le=200)
    auto_cuts: int = Field(default=2, ge=0, le=5)  # best daily suggestions turned into cuts on their own


@router.get("/{channel_id}/viral")
def viral_list(channel_id: int, session: Session = Depends(get_session)):
    from cvm.grow import daily

    c = _channel(session, channel_id)
    row = session.get(Setting, f"viral:{c.id}")
    return {**daily.prefs(c), "running": daily.running(session, c.id),
            "searches": (row.value or {}).get("searches", []) if row else []}


@router.put("/{channel_id}/viral/settings")
def viral_settings(channel_id: int, body: ViralSettings, session: Session = Depends(get_session)):
    c = _channel(session, channel_id)
    prefs = dict(c.prefs or {})
    prefs["viral"] = {**prefs.get("viral", {}), **body.model_dump()}
    c.prefs = prefs
    flag_modified(c, "prefs")
    session.add(c)
    session.commit()
    return viral_list(channel_id, session)


@router.delete("/{channel_id}/viral/searches")
def viral_remove(channel_id: int, at: str, session: Session = Depends(get_session)):
    """Delete one saved search (its tab), found by its `at` time stamp."""
    from cvm.grow import viral_search

    c = _channel(session, channel_id)
    if not viral_search.remove(session, c.id, at):
        raise HTTPException(404, "Pesquisa não encontrada.")
    return viral_list(channel_id, session)


class ViralIn(BaseModel):
    topic: str = Field(min_length=2, max_length=120)
    language: str = Field(default="pt", pattern=r"^(pt|en)$")


@router.post("/{channel_id}/viral")
def viral_start(channel_id: int, body: ViralIn, session: Session = Depends(get_session)):
    from cvm.grow import daily

    c = _channel(session, channel_id)
    if not daily.start(session, c.id):
        raise HTTPException(409, "Já há uma pesquisa rodando para este canal.")
    _in_background(_job_viral, c.id, body.topic.strip(), body.language)
    return {"started": True}


class ResearchIn(BaseModel):
    topic: str = Field(min_length=2, max_length=120)
    language: str = Field(default="pt", pattern=r"^(pt|en)$")


def _research_running(session: Session, channel_id: int) -> bool:
    row = session.get(Setting, f"research:{channel_id}")
    since = (row.value or {}).get("running_since") if row else None
    return bool(since) and (datetime.now(UTC) - datetime.fromisoformat(since)).total_seconds() < 1800


def _research_mark(session: Session, channel_id: int, running: bool) -> None:
    row = session.get(Setting, f"research:{channel_id}") or Setting(key=f"research:{channel_id}", value={})
    row.value = {**(row.value or {}), "running_since": datetime.now(UTC).isoformat() if running else None}
    flag_modified(row, "value")
    session.add(row)
    session.commit()


@router.get("/{channel_id}/research")
def research_list(channel_id: int, session: Session = Depends(get_session)):
    c = _channel(session, channel_id)
    row = session.get(Setting, f"research:{c.id}")
    return {"running": _research_running(session, c.id), "reports": (row.value or {}).get("reports", []) if row else []}


class DiscoverIn(BaseModel):
    topic: str = Field(min_length=2, max_length=120)
    region: str = Field(default="BR", pattern=r"^(BR|US|AE|)$")


class MakeIn(BaseModel):
    kind: str = Field(pattern=r"^(short|video|cortes)$")
    item: dict


def _discover_row(session: Session, channel_id: int) -> Setting:
    return session.get(Setting, f"discover:{channel_id}") or Setting(key=f"discover:{channel_id}", value={})


def _discover_running(row: Setting) -> bool:
    since = (row.value or {}).get("running_since")
    return bool(since) and (datetime.now(UTC) - datetime.fromisoformat(since)).total_seconds() < 600


@router.get("/{channel_id}/discover")
def discover_list(channel_id: int, session: Session = Depends(get_session)):
    """"Em alta": the last searches ({topic, region, at, live, lives, videos}) and what is being made from them."""
    c = _channel(session, channel_id)
    row = _discover_row(session, c.id)
    v = row.value or {}
    return {"running": _discover_running(row), "error": v.get("error"), "results": v.get("results", []),
            "made": v.get("made", {}), "topics": ((c.prefs or {}).get("viral") or {}).get("topics") or []}


@router.post("/{channel_id}/discover")
def discover_start(channel_id: int, body: DiscoverIn, session: Session = Depends(get_session)):
    c = _channel(session, channel_id)
    row = _discover_row(session, c.id)
    if _discover_running(row):
        raise HTTPException(409, "Já há uma busca rodando para este canal.")
    row.value = {**(row.value or {}), "running_since": datetime.now(UTC).isoformat(), "error": None}
    flag_modified(row, "value"); session.add(row); session.commit()
    _in_background(_job_discover, c.id, body.topic.strip(), body.region)
    return {"started": True}


@router.post("/{channel_id}/discover/make")
def discover_make(channel_id: int, body: MakeIn, session: Session = Depends(get_session)):
    """Make a Short / a comment video / the cuts of a result (download + project, in the worker)."""
    c = _channel(session, channel_id)
    if body.item.get("kind") == "live":
        raise HTTPException(409, "A live ainda está no ar: dá para cortar quando ela terminar.")
    if not str(body.item.get("url", "")).startswith("https://www.youtube.com/watch?v="):
        raise HTTPException(422, "link inválido")
    row = _discover_row(session, c.id)
    made = dict((row.value or {}).get("made") or {})
    made[f"{body.item['id']}:{body.kind}"] = {"status": "working", "at": datetime.now(UTC).isoformat()}
    row.value = {**(row.value or {}), "made": made}
    flag_modified(row, "value"); session.add(row); session.commit()
    _in_background(_job_make, c.id, body.item, body.kind)
    return {"started": True}


@router.post("/{channel_id}/research")
def research_start(channel_id: int, body: ResearchIn, session: Session = Depends(get_session)):
    """Topic report from vidIQ (~20 credits): verdict, keywords, competitors' outliers, ideas."""
    c = _channel(session, channel_id)
    if _research_running(session, c.id):
        raise HTTPException(409, "Já há uma pesquisa rodando para este canal.")
    _research_mark(session, c.id, True)
    _in_background(_job_research, c.id, body.topic.strip(), body.language)
    return {"started": True}


def _store_review(session: Session, render_id: int, result: dict) -> None:
    """Merge the review into the render's current meta (the review takes minutes; meta may have changed)."""
    from cvm.models import Render

    r = session.get(Render, render_id)
    session.refresh(r)
    r.meta = {**(r.meta or {}), "vidiq": result, "vidiq_running_since": None}
    flag_modified(r, "meta")
    session.add(r)
    session.commit()


@router.post("/renders/{render_id}/vidiq")
def vidiq_review_start(render_id: int, session: Session = Depends(get_session)):
    """'Avaliar com vidIQ' (~20 credits long, ~10 Short): result lands in the render's meta["vidiq"]."""
    from cvm.models import Project, Render

    r = session.get(Render, render_id)
    if not r:
        raise HTTPException(404)
    p = session.get(Project, r.project_id)
    if not p or not p.channel_id:
        raise HTTPException(409, "Este vídeo não tem canal.")
    since = (r.meta or {}).get("vidiq_running_since")
    if since and (datetime.now(UTC) - datetime.fromisoformat(since)).total_seconds() < 900:
        raise HTTPException(409, "A avaliação deste vídeo já está rodando.")
    r.meta = {**(r.meta or {}), "vidiq_running_since": datetime.now(UTC).isoformat()}
    flag_modified(r, "meta")
    session.add(r)
    session.commit()
    _in_background(_job_vidiq, p.channel_id, r.id)
    return {"started": True}


def pace(snapshot: dict, m: dict) -> dict:
    """Days to each goal at the last 28 days' rate (None when a number is missing or the rate is zero)."""
    def per_day(v):
        return round(v / 28, 2) if isinstance(v, int | float) else None

    minutes = snapshot.get("minutes_28d")
    hpd = per_day(minutes / 60) if isinstance(minutes, int | float) else None
    spd = per_day(snapshot.get("subs_gained_28d"))

    def days(target, have, rate):
        if rate is None or rate <= 0 or have is None:
            return None
        return max(0, math.ceil((target - have) / rate))

    return {"hours_per_day": hpd, "subs_per_day": spd, "days_to_hours": days(m["hours_target"], m.get("hours"), hpd),
            "days_to_subs": days(m["subs_target"], m.get("subs"), spd)}


@router.get("/{channel_id}/monetization")
def monetization(channel_id: int, session: Session = Depends(get_session)):
    c = _channel(session, channel_id)
    st = analytics.stats(session, c)
    m = st["monetization"]
    row = session.get(Setting, analytics.stats_key(c.id))
    snap = (row.value or {}) if row and st.get("connected") else {}
    source = "analytics" if st.get("connected") else None
    if not st.get("connected"):  # Google OAuth not set up yet: the numbers vidIQ gave in the daily run
        from cvm.grow import vidiq_stats

        if v := vidiq_stats.load(session, c.id):
            m = {**m, "hours": v.get("hours_365_long"), "subs": v.get("subscribers")}
            snap, source = v, "vidiq"
    from cvm.grow import vidiq_stats

    sync = vidiq_stats.sync_state(session, c.id)
    return {**m, "connected": st.get("connected", False), "error": st.get("error"), "pace": pace(snap, m),
            "source": source, "updated_at": snap.get("at") or snap.get("updated_at"), "shorts": shorts_path(snap),
            "vidiq_sync": {"running": sync["running"], "error": sync.get("error"), "credits": vidiq_stats.CREDITS,
                           "available": bool(c.youtube_channel_id)}}


@router.post("/{channel_id}/monetization/vidiq")
def monetization_vidiq(channel_id: int, session: Session = Depends(get_session)):
    """Monetização > "Sincronizar com o vidIQ": the channel's hours/subs/Shorts views now (~10 vidIQ credits) instead
    of waiting for the 07:00 daily run. Runs in the worker."""
    from cvm.grow import vidiq_stats

    c = _channel(session, channel_id)
    if not c.youtube_channel_id:
        raise HTTPException(409, "Este canal não tem o ID do canal do YouTube salvo; o vidIQ precisa dele.")
    if not vidiq_stats.start_sync(session, c.id):
        raise HTTPException(409, "Já há uma sincronização rodando para este canal.")
    _in_background(_job_vidiq_stats, c.id)
    return {"started": True}


def _job_vidiq_stats(s: Session, ch: Channel) -> None:
    from cvm.grow import vidiq_stats

    vidiq_stats.run_sync(s, ch.id, ch.youtube_channel_id)


SHORTS_TARGET = 10_000_000  # YPP Shorts path: 1,000 subs + 10M public Shorts views in the last 90 days


def shorts_path(snap: dict) -> dict:
    """Views of Shorts in the last 90 days vs 10M, the current daily rate (last 28 days) and the rate needed."""
    v28 = snap.get("shorts_views_28d")
    return {"views_90d": snap.get("shorts_views_90d"), "target": SHORTS_TARGET,
            "views_per_day": round(v28 / 28, 1) if isinstance(v28, int | float) else None,
            "needed_per_day": round(SHORTS_TARGET / 90)}


# --- background jobs (run by the worker through cvm.workers.tasks.grow_job) ---

def _job_discover(s: Session, ch: Channel, topic: str, region: str) -> None:
    from cvm.grow import discover

    row = _discover_row(s, ch.id)
    try:
        res = discover.discover(topic, region)
        results = [res, *[r for r in (row.value or {}).get("results", []) if (r.get("topic"), r.get("region")) != (topic, region)]][:5]
        row.value = {**(row.value or {}), "results": results, "running_since": None, "error": None}
    except Exception as e:  # noqa: BLE001
        row.value = {**(row.value or {}), "running_since": None, "error": str(e)[:300]}
    flag_modified(row, "value"); s.add(row); s.commit()


def _job_make(s: Session, ch: Channel, item: dict, kind: str) -> None:
    from cvm.grow import discover

    key = f"{item['id']}:{kind}"
    try:
        res = discover.make(s, ch, item, kind)
        done = {"status": "ok", "project_id": getattr(res, "id", None)}
    except Exception as e:  # noqa: BLE001
        done = {"status": "error", "error": str(e)[:300]}
    row = _discover_row(s, ch.id)
    s.refresh(row)
    made = dict((row.value or {}).get("made") or {})
    made[key] = {**done, "at": datetime.now(UTC).isoformat()}
    row.value = {**(row.value or {}), "made": made}
    flag_modified(row, "value"); s.add(row); s.commit()


def _job_link(s: Session, ch: Channel, url: str, title: str | None) -> None:
    runs.process_link(s, ch, url, title, claimed=True)


def _job_upload(s: Session, ch: Channel, live: dict, path: str) -> None:
    from pathlib import Path

    item = next(i for i in runs.status(s, ch)["items"] if i["video_id"] == live["video_id"])
    runs.finish(s, ch, item, live, Path(path))


def _job_viral(s: Session, ch: Channel, topic: str, language: str) -> None:
    from cvm.grow import daily, viral_search

    try:
        viral_search.save(s, ch.id, viral_search.search(topic, language))
    finally:
        daily.finish(s, ch.id)


def _job_vidiq(s: Session, ch: Channel, render_id: int) -> None:
    from cvm.grow import vidiq_review
    from cvm.models import Render

    rr = s.get(Render, render_id)
    try:
        result = vidiq_review.review(rr, ch, video_id=vidiq_review._published_id(rr))
    except Exception as e:  # noqa: BLE001 — shown on the review panel
        result = {"error": str(e)[:300], "at": datetime.now(UTC).isoformat()}
    _store_review(s, render_id, result)


def _job_research(s: Session, ch: Channel, topic: str, language: str) -> None:
    from cvm.grow import research

    try:
        research.save(s, ch.id, research.report(topic, language, ch.youtube_channel_id))
    finally:
        _research_mark(s, ch.id, False)
