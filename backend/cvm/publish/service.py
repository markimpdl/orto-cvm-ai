"""Scheduling and publication state, shared by the API, the worker and the MCP server."""

import logging
from datetime import datetime

from sqlmodel import Session, select

from cvm import pipeline
from cvm.models import Channel, Job, Project, Publication, PublishMode, Render, now

log = logging.getLogger(__name__)


def _default_slot(session: Session, channel: Channel, render: Render) -> datetime:
    """No time picked (autopilot): the next free publish_slot of the render's format, else now."""
    from cvm import schedule as calendar

    try:
        return calendar.slot_for(session, channel, render) or now()
    except Exception:  # noqa: BLE001  (a bad slot in the profile must not block publishing)
        log.exception("slot for render %s", render.id)
        return now()


def _guards(session: Session, project_id: int, items: list[dict]) -> None:
    """cvm/guards.py before anything is scheduled: refuses (PublishRefused, a ValueError) on a hard failure and
    sets render.meta made_for_kids / synthetic_disclosure for the kit and the uploaders."""
    from cvm import guards
    from cvm.models import Project

    project = session.get(Project, project_id)
    for it in items:
        channel, render = session.get(Channel, it["channel_id"]), session.get(Render, it["render_id"])
        if project and channel and render and render.project_id == project_id:
            guards.check_publication(session, project, render, channel)


def schedule(session: Session, project_id: int, items: list[dict]) -> list[Publication]:
    """items: [{render_id, channel_id, scheduled_at (ISO), mode?}] — called when the user approves the renders."""
    job = session.exec(select(Job).where(Job.project_id == project_id)).one()
    _guards(session, project_id, items)
    pubs = []
    for it in items:
        channel = session.get(Channel, it["channel_id"])
        render = session.get(Render, it["render_id"])
        if not channel or not render or render.project_id != project_id:
            raise ValueError(f"invalid render/channel: {it}")
        when = datetime.fromisoformat(it["scheduled_at"]) if it.get("scheduled_at") else _default_slot(session, channel, render)
        pub = Publication(render_id=render.id, channel_id=channel.id, mode=PublishMode(it.get("mode") or channel.publish_mode),
                          scheduled_at=when, status="scheduled")
        session.add(pub)
        pubs.append(pub)
    pipeline.advance(job, "scheduled", f"{len(pubs)} publicação(ões) agendada(s)")
    session.add(job)
    session.commit()
    return pubs


def _sync_job(session: Session, project_id: int) -> None:
    job = session.exec(select(Job).where(Job.project_id == project_id)).first()
    pubs = session.exec(select(Publication).join(Render).where(Render.project_id == project_id)).all()
    if not job or not pubs:
        return
    if all(p.status == "published" for p in pubs):
        if job.state == "scheduled" and any(p.mode == PublishMode.manual for p in pubs):
            pipeline.advance(job, "ready_for_manual_publish")
        if job.state in ("scheduled", "ready_for_manual_publish"):
            pipeline.advance(job, "published")
    elif any(p.status == "ready_for_manual_publish" for p in pubs) and job.state == "scheduled":
        pipeline.advance(job, "ready_for_manual_publish")
    session.add(job)


def publish_now(session: Session, pub: Publication) -> Publication:
    """Bring a scheduled post forward: it shows up with its publish kit right away (manual publishing is always
    possible, whatever the channel's mode)."""
    if pub.status != "scheduled":
        raise ValueError(f"publicação está {pub.status}")
    pub.status, pub.scheduled_at = "ready_for_manual_publish", now()
    session.add(pub)
    session.flush()
    _sync_job(session, session.get(Render, pub.render_id).project_id)
    session.commit()
    return pub


def mark_published(session: Session, pub: Publication, url: str | None) -> Publication:
    pub.status, pub.url, pub.published_at = "published", url, now()
    session.add(pub)
    session.flush()
    _sync_job(session, session.get(Render, pub.render_id).project_id)
    session.commit()
    return pub


def unpublish(session: Session, pub: Publication) -> Publication:
    """Undo "Marcar como publicado": back to the publish kit (link cleared); the project's job leaves "published"."""
    if pub.status != "published":
        raise ValueError(f"publicação está {pub.status}")
    pub.status, pub.url, pub.published_at = "ready_for_manual_publish", None, None
    session.add(pub)
    project_id = session.get(Render, pub.render_id).project_id
    job = session.exec(select(Job).where(Job.project_id == project_id)).first()
    if job and job.state == "published":
        pipeline.advance(job, "ready_for_manual_publish", "publicação revertida")
        session.add(job)
    session.commit()
    return pub


def release_due(session: Session, notify) -> int:
    """Called every minute by the worker."""
    from cvm.publish.platforms import NotConfigured, publish_via_api

    due = session.exec(select(Publication).where(Publication.status == "scheduled", Publication.scheduled_at <= now())).all()
    for pub in due:
        render, channel = session.get(Render, pub.render_id), session.get(Channel, pub.channel_id)
        if pub.mode == PublishMode.api:
            from cvm.guards import avatar_on_screen

            # AI disclosure from today's rule (avatar on screen), also for renders scheduled before it
            render.meta = {**(render.meta or {}), "synthetic_disclosure": avatar_on_screen(
                session, session.get(Project, render.project_id), render)}
            try:
                url = publish_via_api(render, channel)
                mark_published(session, pub, url)
                notify("Publicado", f"{channel.name}: {url}")
                continue
            except NotConfigured as e:
                log.warning("api publish not configured, falling back to manual: %s", e)
                pub.mode = PublishMode.manual
            except Exception as e:  # noqa: BLE001
                log.exception("publish failed")
                pub.status = "failed"
                session.add(pub)
                session.commit()
                notify("Falha ao publicar", f"{channel.name}: {e}")
                continue
        pub.status = "ready_for_manual_publish"
        session.add(pub)
        session.flush()
        _sync_job(session, render.project_id)
        session.commit()
        hint = "clique em 'Preparar no navegador' no Kit" if pub.mode == PublishMode.browser else "abra o Kit de publicação no CVM"
        notify("Publicação pronta", f"{channel.name}: {hint}")
    return len(due)
