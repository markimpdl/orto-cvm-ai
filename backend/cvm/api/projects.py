import shutil

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from cvm.db import get_session
from cvm.models import Asset, Channel, Job, Project, Publication, Render, Script, Trend, TrendAnalysis

# states where the worker is busy with the project's files; deleting then needs force=true
BUSY_STATES = {"voicing", "sourcing_visuals", "lipsyncing", "composing", "rendering", "validating"}
PRESENTERS = (None, "scenes", "overlay", "podcast", "editorial")
MUSIC_SOURCES = (None, "none", "acestep", "elevenlabs")

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
def list_projects(session: Session = Depends(get_session)):
    projects = session.exec(select(Project).order_by(col(Project.created_at).desc())).all()
    all_jobs = session.exec(select(Job)).all()
    queue = _queue(all_jobs)
    jobs = {j.project_id: j for j in all_jobs}
    titles = _seo_titles(session)
    return [{**p.model_dump(), "job": job_out(jobs.get(p.id), queue), "seo_title": titles.get(p.id)} for p in projects]


def _seo_titles(session: Session) -> dict[int, str]:
    """The title each project goes out with, in its channel's language (the long video's, else any render's), so
    the language versions of one topic don't all read as the same Portuguese topic in the lists."""
    out: dict[int, tuple[int, str]] = {}
    for pid, profile, seo in session.exec(select(Render.project_id, Render.profile, Render.seo)).all():
        title = (seo or {}).get("title") if isinstance(seo, dict) else None
        if not title:
            continue
        rank = 0 if profile == "youtube" else 1
        if pid not in out or rank < out[pid][0]:
            out[pid] = (rank, title)
    return {pid: t for pid, (_, t) in out.items()}


def _queue(all_jobs: list[Job]) -> dict[int, dict]:
    from cvm import pipeline
    from cvm.queueing import queue_info
    from cvm.workers.tasks import CLAUDE_SLOTS

    return queue_info(all_jobs, pipeline.typical_durations(all_jobs), CLAUDE_SLOTS)


def job_out(job: Job | None, queue: dict[int, dict] | None = None) -> dict | None:
    """Job as the UI sees it: flagged when its step lost its worker, plus its place in the GPU/Claude queue."""
    from cvm.progress import orphaned
    from cvm.workers.tasks import running_states

    if not job:
        return None
    q = (queue or {}).get(job.id) or {"waiting_for": None, "queue_position": None, "eta_s": None}
    return {**job.model_dump(), "orphaned": orphaned(job, running_states()), **q}


def _similar_409(e) -> HTTPException:
    """A similar recent video: 409 with the list, so the UI can show it and offer "fazer mesmo assim"."""
    return HTTPException(409, {"code": "similar", "message": str(e), "similar": e.similar})


def _validate_presenter(data: dict) -> None:
    if data.get("presenter") not in PRESENTERS:
        raise HTTPException(422, "presenter: scenes | overlay | podcast | editorial | null")


@router.post("")
def create_project(data: dict, session: Session = Depends(get_session)):
    """Create a project (usually from a trend) and its pipeline job; the script step starts on its own.
    Without avatar_id it uses the channel's avatar (or the only ready one) unless no_avatar is set.
    `channel_ids: [..]` ("Fazer este tema para…"): one project per channel of the group, each in its channel's
    language with its own defaults and an adaptation note (cvm/suggest.py new_projects); the response is the main
    channel's project plus `projects` (all). Guards (cvm/guards.py): 409 for a closed channel / foreign language,
    409 {code: "similar", similar: [...]} for a similar video in the last 30 days unless `force_similar: true`;
    other warnings come back in `warnings`."""
    from cvm.channels import ChannelClosed
    from cvm.guards import SimilarTopic
    from cvm.suggest import new_project, new_projects, save_prefs

    from cvm import media_links

    _validate_presenter(data)
    from cvm.channels import check_language

    try:
        for lang in data.get("languages") or []:
            check_language(lang)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if data.get("media_cut") not in (None, "full", "highlights"):
        raise HTTPException(422, "media_cut: full | highlights")
    if data.get("music_source") not in MUSIC_SOURCES:
        raise HTTPException(422, "music_source: none | acestep | elevenlabs | null")
    if isinstance(data.get("draft"), str):
        data["draft"] = data["draft"].strip() or None
    try:  # /fabrica/novo links (YouTube, Twitch, Kick, TikTok, Instagram): downloaded by the worker, cvm/media_links.py
        links = media_links.normalize(data.pop("links", None))
    except (ValueError, TypeError, KeyError, IndexError) as e:
        raise HTTPException(422, str(e)) from e
    hold = bool(data.pop("hold", False)) or bool(links)  # files/links first, then POST /projects/start
    if data.pop("save_prefs", False):
        save_prefs(session, data)
    if "music" in data or data.get("music_source"):  # /novo "Música": a channel default too (compose reads prefs)
        on = bool(data.pop("music", None)) if not data.get("music_source") else data["music_source"] != "none"
        for cid in {data.get("channel_id"), *(data.get("channel_ids") or [])} - {None}:
            if ch := session.get(Channel, cid):
                ch.prefs = {**(ch.prefs or {}), "music": on,
                            **({"music_source": data["music_source"]} if on and data.get("music_source") else {})}
                session.add(ch)
        session.commit()
    ref = data.pop("channel_idea", None)  # {analysis_id, index}: a channel niche idea adjusted on /novo
    channel_ids = data.pop("channel_ids", None) or []
    if data.get("channel_id") and channel_ids and data["channel_id"] not in channel_ids:
        channel_ids = [data["channel_id"], *channel_ids]
    warnings: list[str] = []
    try:
        if len(channel_ids) > 1:
            projects = new_projects(session, data, channel_ids, warnings, start_job=not hold)
        else:
            if channel_ids:
                data["channel_id"] = channel_ids[0]
            projects = [new_project(session, data, warnings, start_job=not hold)]
    except SimilarTopic as e:
        raise _similar_409(e) from e
    except (ChannelClosed, ValueError) as e:
        raise HTTPException(409, str(e)) from e
    project = projects[0]
    a = session.get(TrendAnalysis, ref.get("analysis_id")) if ref else None
    if a and a.channel_id and 0 <= ref.get("index", -1) < len(a.items):
        items = [dict(it) for it in a.items]
        items[ref["index"]]["project_id"] = project.id
        a.items = items  # new list so SQLAlchemy saves the JSON change
        session.add(a)
        session.commit()
    link_job = media_links.hold(session, projects, links) if links else None
    for p in projects:
        session.refresh(p)  # a commit expired them: reload so the response carries their fields
    return {**project.model_dump(), "warnings": warnings, "projects": [p.model_dump() for p in projects],
            "held": hold, "link_job": link_job}


@router.post("/start")
def start_projects(body: dict, session: Session = Depends(get_session)):
    """{project_ids}: start projects created with `hold` (or with links) once /fabrica/novo has sent their files.
    Projects with links go to the worker (download + attach, then the job); the others start now."""
    from cvm import media_links
    from cvm.suggest import start

    started, downloading = [], []
    for pid in dict.fromkeys(int(i) for i in body.get("project_ids") or []):
        p = session.get(Project, pid)
        if not p:
            continue
        entry = media_links.entry_for(session, pid)
        if entry and entry.get("status") == "held":
            media_links.dispatch(session, entry)
            downloading += entry.get("project_ids") or []
        elif pid not in downloading and not (entry and entry.get("status") == "working"):
            start(session, p)
            started.append(pid)
    return {"started": started, "downloading": downloading}


@router.get("/links")
def link_jobs(session: Session = Depends(get_session)):
    """Recent /fabrica/novo link downloads: [{key, title, project_ids, status: held|working|review|ok|error, error,
    at, n_links, progress}]; progress = the step while working (cvm/lives/progress.py: download, then the cut
    analysis of a "Melhores momentos" link)."""
    from cvm import media_links
    from cvm.lives import progress

    return [{**{k: v for k, v in e.items() if k != "links"}, "n_links": len(e.get("links") or []),
             "progress": progress.get(session, progress.fabrica_key(e["key"])) if e.get("status") == "working" else None}
            for e in media_links.entries(session)]


@router.get("/music/estimate")
def music_estimate(session: Session = Depends(get_session)):
    """What an ElevenLabs track costs before the user picks it (cvm/render/music_eleven.py): {available, seconds,
    credits, remaining, reserve, enough}. Nothing is generated here."""
    from cvm.render import music_eleven

    return music_eleven.estimate(session)


@router.post("/batch")
def create_batch(body: dict, session: Session = Depends(get_session)):
    """{items: [{trend_id, analysis_id?, idea_index?, channel_ids?}], channel_ids?, force_similar?}: produce several
    trends/ideas with each suggested channel's defaults — or, with `channel_ids` (per item or for all), one project
    per chosen channel, each adapted to its audience. Trends that already have a project, closed channels and
    similar recent videos (unless force_similar) are skipped."""
    from cvm.channels import ChannelClosed
    from cvm.models import Channel
    from cvm.suggest import adaptation_note, project_from, used_trends

    used, created, skipped = used_trends(session), [], []
    force = bool(body.get("force_similar"))
    for it in body.get("items", []):
        trend = session.get(Trend, it.get("trend_id"))
        if not trend or trend.id in used:
            skipped.append(it.get("trend_id"))
            continue
        idea = None
        if it.get("analysis_id") is not None and it.get("idea_index") is not None:
            a = session.get(TrendAnalysis, it["analysis_id"])
            idea = a.items[it["idea_index"]] if a and it["idea_index"] < len(a.items) else None
        ids = list(dict.fromkeys(it.get("channel_ids") or body.get("channel_ids") or []))
        # closed channels are skipped here: suggestion() would fall back to another channel
        targets = [c for c in (session.get(Channel, i) for i in ids) if c and (c.status or "active") == "active"] \
            if ids else [None]
        made = []
        for c in targets:
            note = adaptation_note(c, [x for x in targets if x.id != c.id]) if c and len(targets) > 1 else ""
            try:
                made.append(project_from(session, trend, {**(idea or {}), **({"channel_id": c.id} if c else {})},
                                         {"force_similar": force}, note=note))
            except ChannelClosed:  # also SimilarTopic (cvm/guards.py)
                continue
        if not made:
            skipped.append(it.get("trend_id"))
            continue
        used[trend.id] = made[0].id
        created += made
    return {"created": created, "skipped": skipped}


@router.patch("/{project_id}")
def update_project(project_id: int, data: dict, session: Session = Depends(get_session)):
    """Change the avatar/channel of a project (takes effect from the next voicing/compose)."""
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404)
    _validate_presenter(data)
    if data.get("media_mode") not in (None, "mix", "mine", "online"):
        raise HTTPException(422, "media_mode: mix | mine | online")
    if data.get("media_cut") not in (None, "full", "highlights"):
        raise HTTPException(422, "media_cut: full | highlights")
    if data.get("music_source") not in MUSIC_SOURCES:
        raise HTTPException(422, "music_source: none | acestep | elevenlabs | null")
    for k in ("avatar_id", "channel_id", "title", "media_mode", "media_cut", "presenter", "voice_variant", "draft",
              "music_source"):
        if k in data:
            setattr(project, k, data[k])
    session.add(project)
    session.commit()
    session.refresh(project)
    if project.media_cut == "highlights":
        from cvm.api.media import refresh_highlights

        refresh_highlights(project.id)
    return project


class CleanupIn(BaseModel):
    project_ids: list[int]
    days: int = 3


@router.get("/cleanup/preview")
def cleanup_preview(days: int = 3, session: Session = Depends(get_session)):
    """Old published videos whose big files can go (cvm/cleanup.py); nothing is deleted here."""
    from cvm import cleanup

    return cleanup.preview(session, days)


@router.post("/cleanup")
def cleanup_run(body: CleanupIn, session: Session = Depends(get_session)):
    """Delete only the video files of the chosen projects (eligibility checked again); history is kept."""
    from cvm import cleanup

    return cleanup.run(session, body.project_ids, body.days)


@router.get("/{project_id}")
def get_project(project_id: int, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404)
    scripts = session.exec(
        select(Script).where(Script.project_id == project_id).order_by(col(Script.version).desc())
    ).all()
    job = session.exec(select(Job).where(Job.project_id == project_id)).first()
    narrations = session.exec(select(Asset).where(Asset.project_id == project_id, Asset.kind == "narration")).all()
    voice_qa = {a.language: (a.meta or {}).get("qa") for a in narrations if (a.meta or {}).get("qa")}
    from cvm import curation
    from cvm.voice.narrate import project_variant

    return {**project.model_dump(), "scripts": scripts, "job": job_out(job, _queue(session.exec(select(Job)).all())),
            "voice_qa": voice_qa, "voice_effective": project_variant(session, project),  # what narration really uses
            "curation": curation.latest_report(session, project_id)}  # "Conferido pela IA" (cvm/curation.py)


def remove_project(session: Session, project: Project) -> None:
    """Delete a project with its job, scripts, assets, renders, publications and files."""
    from cvm.config import settings

    renders = session.exec(select(Render).where(Render.project_id == project.id)).all()
    render_ids = [r.id for r in renders]
    rows = [
        *session.exec(select(Publication).where(col(Publication.render_id).in_(render_ids))).all(),
        *renders,
        *session.exec(select(Asset).where(Asset.project_id == project.id)).all(),
        *session.exec(select(Script).where(Script.project_id == project.id)).all(),
        *session.exec(select(Job).where(Job.project_id == project.id)).all(),
    ]
    for row in rows:
        session.delete(row)
    session.flush()
    session.delete(project)
    session.commit()
    shutil.rmtree(settings.data_dir / "projects" / str(project.id), ignore_errors=True)


@router.delete("/{project_id}")
def delete_project(project_id: int, force: bool = False, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404)
    job = session.exec(select(Job).where(Job.project_id == project_id)).first()
    if job and job.state in BUSY_STATES and not force:
        raise HTTPException(409, f"projeto em processamento ({job.state})")
    remove_project(session, project)
    return {"deleted": project_id}


@router.put("/{project_id}/scripts/{language}")
def save_script(project_id: int, language: str, content: dict, session: Session = Depends(get_session)):
    """Save an edited script as a new version (used by the UI editor and the MCP server)."""
    from cvm.scripting.schema import ScriptContent

    ScriptContent.model_validate(content)
    last = session.exec(
        select(Script)
        .where(Script.project_id == project_id, Script.language == language)
        .order_by(col(Script.version).desc())
    ).first()
    script = Script(project_id=project_id, language=language, content=content, version=(last.version + 1) if last else 1)
    session.add(script)
    from cvm.pipeline import name_episode

    name_episode(session, project_id, content)
    session.commit()
    session.refresh(script)
    return script


@router.get("/{project_id}/visuals")
def list_visuals(project_id: int, session: Session = Depends(get_session)):

    assets = session.exec(select(Asset).where(Asset.project_id == project_id, Asset.kind == "visual").order_by(Asset.scene)).all()
    return [{**a.model_dump(), "file_url": f"/assets/{a.id}/file" if a.path and not (a.meta or {}).get("removed") else None} for a in assets]


@router.post("/{project_id}/visuals/{scene}/choose")
def choose_visual(project_id: int, scene: int, body: dict, session: Session = Depends(get_session)):
    """Swap a scene's visual: {"candidate": <index in meta.candidates>}, {"media_id": <your file>} or {"type": "card"}."""
    from cvm.paths import project_dir
    from cvm.visuals.sources import download

    a = session.exec(select(Asset).where(Asset.project_id == project_id, Asset.kind == "visual", Asset.scene == scene)).first()
    if not a:
        raise HTTPException(404)
    if body.get("media_id") is not None:
        from cvm.api.media import use_on_scene

        media = session.get(Asset, int(body["media_id"]))
        if not media or media.project_id != project_id or media.kind != "user_media":
            raise HTTPException(404)
        return use_on_scene(session, project_id, scene, media)
    if body.get("type") == "card":
        a.path, a.meta = "", {**a.meta, "kind": "card"}
    else:
        cand = a.meta.get("candidates", [])[int(body["candidate"])]
        a.path = str(download(cand["url"], project_dir(project_id) / "visuals"))
        a.source, a.license = cand["source"], cand["license"]
        a.meta = {**a.meta, **{k: v for k, v in cand.items() if k != "candidates"}}
    session.add(a)
    session.commit()
    return a
