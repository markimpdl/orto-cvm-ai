"""Autopilot: queue Claude's best ideas on its own (potential ≥ min_score, at most max_per_day per channel, never
the same trend twice). Approvals stay with the user unless they opt in to auto_approve_script / auto_approve_render
(see auto_approve)."""

from datetime import timedelta

from sqlmodel import Session, col, select

from cvm.models import Project, Setting, Trend, TrendAnalysis, now

DEFAULT = {"enabled": False, "min_score": 75, "max_per_day": 3, "auto_approve_script": False, "auto_approve_render": False}
REANALYZE_AFTER = timedelta(hours=6)


def get(session: Session) -> dict:
    row = session.get(Setting, "autopilot")
    return {**DEFAULT, **(row.value if row else {})}


def put(session: Session, data: dict) -> dict:
    value = {**get(session), **{k: data[k] for k in DEFAULT if k in data}}
    row = session.get(Setting, "autopilot") or Setting(key="autopilot")
    row.value = value
    session.add(row)
    session.commit()
    return value


def _started_today(session: Session) -> dict[int | None, int]:
    """Autopilot projects created today, per channel."""
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    counts: dict[int | None, int] = {}
    for p in session.exec(select(Project).where(Project.created_at >= start)).all():
        if p.autopilot:
            counts[p.channel_id] = counts.get(p.channel_id, 0) + 1
    return counts


def enqueue(session: Session) -> list[int]:
    """Create projects from the latest done analysis. Returns the new project ids."""
    from cvm.models import Channel
    from cvm.suggest import project_from, suggestion, used_trends

    from cvm import queueing

    cfg = get(session)
    if not cfg["enabled"] or queueing.too_busy(session):  # finish before starting (user, 2026-11-22)
        return []
    a = session.exec(select(TrendAnalysis).where(TrendAnalysis.status == "done", TrendAnalysis.channel_id.is_(None))
                     .order_by(col(TrendAnalysis.id).desc())).first()
    if not a:
        return []
    today, used, created = _started_today(session), used_trends(session), []
    # kids series are the user's own work (episodes from /infantil): the autopilot never fills those channels
    from cvm.channels import producing

    channels = [c for c in producing(session) if not c.made_for_kids and c.default_style != "kids"]
    for idea in sorted(a.items, key=lambda i: -(i.get("potential") or 0)):
        if (idea.get("potential") or 0) < cfg["min_score"]:
            break
        trend = session.get(Trend, (idea.get("trend_ids") or [None])[0])
        if not trend or trend.id in used:
            continue
        s = suggestion(session, channels, trend.region, idea)
        channel_id = s["channel_id"]
        if channel_id is None or today.get(channel_id, 0) >= cfg["max_per_day"]:  # no fitting channel / full for today
            continue
        try:
            p = project_from(session, trend, idea, {"autopilot": True, **s})
        except ValueError:  # ChannelClosed / guards.SimilarTopic: a similar video on that channel in the last 30 days
            continue
        used[trend.id] = p.id
        today[channel_id] = today.get(channel_id, 0) + 1
        created.append(p.id)
    return created


def needs_analysis(session: Session) -> bool:
    """Ask Claude for fresh ideas when the last analysis is old and none is running."""
    last = session.exec(select(TrendAnalysis).where(TrendAnalysis.channel_id.is_(None)).order_by(col(TrendAnalysis.id).desc())).first()
    if not last:
        return True
    return last.status != "running" and now() - last.created_at.replace(tzinfo=now().tzinfo) > REANALYZE_AFTER


def held_for_review(session: Session) -> set[int]:
    """Projects the user reviews by hand even with auto-approval on (Setting "manual_review" {"projects": [...]})."""
    row = session.get(Setting, "manual_review")
    return set((row.value or {}).get("projects", [])) if row and isinstance(row.value, dict) else set()


def auto_approve(session: Session, job) -> bool:
    """Pass a human gate on its own when the user turned that approval on. Returns True if the job moved.
    Scripts go straight to narration; finished videos are scheduled now on the project's channel (same render
    choice as the review screen), so manual channels get their publish kit and api/browser channels upload."""
    from cvm import pipeline
    from cvm.models import Channel, Render
    from cvm.publish import service

    cfg = get(session)
    if job.project_id in held_for_review(session):
        return False
    if job.state == "awaiting_approval" and cfg["auto_approve_script"]:
        pipeline.advance(job, "voicing", "aprovado automaticamente")
        session.add(job)
        session.commit()
        return True
    if job.state == "awaiting_publish_review" and cfg["auto_approve_render"]:
        project = session.get(Project, job.project_id)
        if project and (project.title or "").startswith("🟡"):  # another creator's video (cvm/lives/package.py):
            return False  # publishing it is always the user's call (strike/reused-content risk)
        channel = session.get(Channel, project.channel_id) if project and project.channel_id else None
        renders = session.exec(select(Render).where(Render.project_id == job.project_id)).all()
        if not channel or not renders:
            return False
        platform = channel.platform.value
        chosen = [r for r in renders if r.profile == platform or r.profile.startswith(platform)] or renders
        try:
            service.schedule(session, job.project_id, [{"render_id": r.id, "channel_id": channel.id} for r in chosen])
        except ValueError:  # guards.PublishRefused (wrong language, kids series on a non-kids channel): the human decides
            session.rollback()
            return False
        return True
    return False
