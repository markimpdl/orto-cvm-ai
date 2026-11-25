from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, select

from cvm.db import get_session
from cvm.channels import producing
from cvm.models import Channel, Trend, TrendAnalysis
from cvm.suggest import suggestion, used_trends
from cvm.trends import current

router = APIRouter(prefix="/trends", tags=["trends"])


@router.get("")
def list_trends(
    region: str | None = None,
    source: str | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    q = current(session, select(Trend))
    if region:
        q = q.where(Trend.region == region)
    if source:
        q = q.where(Trend.source == source)
    q = q.order_by(col(Trend.score).desc()).limit(limit)
    channels, used = producing(session), used_trends(session)
    return [{**t.model_dump(), "suggested": suggestion(session, channels, t.region), "project_id": used.get(t.id)}
            for t in session.exec(q).all()]


@router.get("/meta/status")
def trends_status(session: Session = Depends(get_session)):
    """When trends were last collected (the worker collects every 30 min)."""
    from sqlalchemy import func

    return {"last_collected_at": session.exec(select(func.max(Trend.last_seen))).one(), "every_min": 30}


AI_IDEAS_OFF = "As sugestões da IA estão desligadas. Ligue em Configurações para pedir ideias."


def _ai_ideas_on(session: Session) -> bool:
    from cvm.setup import features

    return features.ai_ideas(session)


@router.post("/refresh")
def refresh_trends(body: dict | None = None, session: Session = Depends(get_session)):
    """Collect trends now; {"analyze": true, "region": ...} also refreshes Claude's ideas once collection ends."""
    from cvm.workers.tasks import collect_trends

    body = body or {}
    analysis = None
    if body.get("analyze") and _ai_ideas_on(session):  # "Sugestões da IA" off: only the collection runs
        analysis = TrendAnalysis(region=body.get("region") or None)
        session.add(analysis)
        session.commit()
        session.refresh(analysis)
    collect_trends(analysis.id if analysis else None)
    return {"queued": True, "analysis": analysis}


@router.post("/analyze")
def analyze(body: dict | None = None, session: Session = Depends(get_session)):
    """Ask the trend-analyst agent (Claude subscription) for ranked video ideas. Runs in the worker."""
    from cvm.workers.tasks import run_trend_analysis

    if not _ai_ideas_on(session):
        raise HTTPException(409, AI_IDEAS_OFF)
    a = TrendAnalysis(region=(body or {}).get("region") or None)
    session.add(a)
    session.commit()
    session.refresh(a)
    run_trend_analysis(a.id)
    return a


@router.get("/analyses")
def analyses(region: str | None = None, limit: int = 1, session: Session = Depends(get_session)):
    """Latest analyses; each idea gets its region, trend title, suggested channel defaults and project_id if produced."""
    q = select(TrendAnalysis).where(TrendAnalysis.channel_id.is_(None)).order_by(col(TrendAnalysis.id).desc()).limit(limit)
    if region:
        q = q.where(TrendAnalysis.region == region)
    channels, used = producing(session), used_trends(session)
    out = []
    for a in session.exec(q).all():
        items = []
        for i, idea in enumerate(a.items):
            trend = session.get(Trend, (idea.get("trend_ids") or [None])[0])
            items.append({**idea, "index": i, "trend_id": trend.id if trend else None,
                          "trend_title": trend.title if trend else None, "region": trend.region if trend else a.region,
                          "suggested": suggestion(session, channels, trend.region if trend else a.region, idea),
                          "project_id": used.get(trend.id) if trend else None})
        out.append({**a.model_dump(), "items": items})
    return out


@router.get("/{trend_id}")
def get_trend(trend_id: int, session: Session = Depends(get_session)):
    from fastapi import HTTPException

    t = session.get(Trend, trend_id)
    if not t:
        raise HTTPException(404)
    channels = producing(session)
    return {**t.model_dump(), "suggested": suggestion(session, channels, t.region), "project_id": used_trends(session).get(t.id)}
