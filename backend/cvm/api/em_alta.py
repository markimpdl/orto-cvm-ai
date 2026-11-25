"""Em alta v2 (plan docs/superpowers/plans/2026-11-24-painel-reestruturacao.md, phase 4) — global, no channel needed:

GET  /trends/discover   → {running, error, results: [{topic, region, at, live, lives, videos, youtube_error,
                           twitch: {items, error, configured}, kick: {items, error, experimental}}]}
POST /trends/discover   {topic?, region} → {started}: YouTube + Twitch + Kick search, in the worker
GET  /trends/research   → {running, credits, reports}: vidIQ topic reports (channel optional)
POST /trends/research   {topic, language, channel_id?, confirm_credits} → {started}; 428 until the user has seen
                           and accepted the credit cost (confirm_credits: true)
GET  /trends/follow     → {keywords, channels: [{platform, handle}], region, last_run, last_error}
PUT  /trends/follow     same body → the cleaned value
POST /trends/follow/run → {started}: bring followed matches in now (the worker also does it every 30 min)

Results live in Settings ("discover:global", "research:global", "follow"); every network call runs in the worker
(cvm.workers.tasks.trends_job → `_job_*` here), never in an API thread. Mounted before cvm/api/trends.py so its
`/trends/{trend_id}` does not swallow these paths.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from cvm.db import get_session
from cvm.models import Channel, Setting

router = APIRouter(prefix="/trends", tags=["em-alta"])

DISCOVER_KEY = "discover:global"
RESEARCH_KEY = "research:global"
RESEARCH_CREDITS = 20  # what one vidIQ topic report costs, shown before running
KEEP = 5


def _in_background(name: str, *args) -> None:
    from cvm.workers.tasks import trends_job

    trends_job(name, list(args))


def _row(session: Session, key: str) -> Setting:
    return session.get(Setting, key) or Setting(key=key, value={})


def _update(session: Session, key: str, **fields) -> None:
    row = _row(session, key)
    row.value = {**(row.value or {}), **fields}
    flag_modified(row, "value")
    session.add(row)
    session.commit()


def _running(row: Setting, lease_s: int) -> bool:
    since = (row.value or {}).get("running_since")
    return bool(since) and (datetime.now(UTC) - datetime.fromisoformat(since)).total_seconds() < lease_s


# ---------------- Lives / Vídeos ----------------

class DiscoverIn(BaseModel):
    topic: str = Field(default="", max_length=120)
    region: str = Field(default="", pattern=r"^(BR|US|AE|)$")


@router.get("/discover")
def discover_list(session: Session = Depends(get_session)):
    from cvm.trends import twitch

    row = _row(session, DISCOVER_KEY)
    v = row.value or {}
    return {"running": _running(row, 600), "error": v.get("error"), "results": v.get("results", []),
            "twitch_configured": twitch.configured()}


@router.post("/discover")
def discover_start(body: DiscoverIn, session: Session = Depends(get_session)):
    if _running(_row(session, DISCOVER_KEY), 600):
        raise HTTPException(409, "Já há uma busca rodando. Espere ela terminar.")
    _update(session, DISCOVER_KEY, running_since=datetime.now(UTC).isoformat(), error=None)
    _in_background("_job_discover", body.topic.strip(), body.region)
    return {"started": True}


# ---------------- Pesquisa vidIQ ----------------

class ResearchIn(BaseModel):
    topic: str = Field(min_length=2, max_length=120)
    language: str = Field(default="pt", pattern=r"^(pt|en)$")
    channel_id: int | None = None
    confirm_credits: bool = False


@router.get("/research")
def research_list(session: Session = Depends(get_session)):
    row = _row(session, RESEARCH_KEY)
    return {"running": _running(row, 1800), "credits": RESEARCH_CREDITS, "reports": (row.value or {}).get("reports", [])}


@router.post("/research")
def research_start(body: ResearchIn, session: Session = Depends(get_session)):
    """vidIQ topic report (~20 credits). Refused (428) until the user accepted the cost; one at a time."""
    if not body.confirm_credits:
        raise HTTPException(428, {"code": "confirm_credits", "credits": RESEARCH_CREDITS,
                                  "message": f"Esta pesquisa gasta cerca de {RESEARCH_CREDITS} créditos do vidIQ."})
    channel = None
    if body.channel_id is not None:
        channel = session.get(Channel, body.channel_id)
        if not channel:
            raise HTTPException(404, "Canal não encontrado.")
    if _running(_row(session, RESEARCH_KEY), 1800):
        raise HTTPException(409, "Já há uma pesquisa rodando. Espere ela terminar.")
    _update(session, RESEARCH_KEY, running_since=datetime.now(UTC).isoformat())
    _in_background("_job_research", body.topic.strip(), body.language, channel.id if channel else None)
    return {"started": True, "credits": RESEARCH_CREDITS}


# ---------------- Seguindo ----------------

class FollowChannel(BaseModel):
    platform: str = Field(pattern=r"^(youtube|twitch|kick)$")
    handle: str = Field(min_length=1, max_length=200)


class FollowIn(BaseModel):
    keywords: list[str] = Field(default_factory=list, max_length=50)
    channels: list[FollowChannel] = Field(default_factory=list, max_length=60)
    region: str = Field(default="BR", pattern=r"^(BR|US|AE)$")


@router.get("/follow")
def follow_get(session: Session = Depends(get_session)):
    from cvm.trends import follow

    return follow.get(session)


@router.put("/follow")
def follow_put(body: FollowIn, session: Session = Depends(get_session)):
    from cvm.trends import follow

    return follow.put(session, body.model_dump())


@router.post("/follow/run")
def follow_run():
    _in_background("_job_follow")
    return {"started": True}


# ---------------- background jobs (worker: cvm.workers.tasks.trends_job) ----------------

def _job_discover(s: Session, topic: str, region: str) -> None:
    from cvm.grow import discover

    row = _row(s, DISCOVER_KEY)
    try:
        res = discover.discover_global(topic, region)
        old = [r for r in (row.value or {}).get("results", []) if (r.get("topic"), r.get("region")) != (topic, region)]
        _update(s, DISCOVER_KEY, results=[res, *old][:KEEP], running_since=None, error=None)
    except Exception as e:  # noqa: BLE001
        _update(s, DISCOVER_KEY, running_since=None, error=str(e)[:300])


def _job_research(s: Session, topic: str, language: str, channel_id: int | None) -> None:
    from cvm.grow import research

    ch = s.get(Channel, channel_id) if channel_id else None
    try:
        res = research.report(topic, language, ch.youtube_channel_id if ch else None,
                              audience=f"o canal “{ch.name}”" if ch else "um criador de vídeos (qualquer canal)")
        res = {**res, "channel_id": ch.id if ch else None, "channel_name": ch.name if ch else None}
        research.save(s, "global", res)
    finally:
        _update(s, RESEARCH_KEY, running_since=None)


def _job_follow(s: Session) -> None:
    from cvm.trends import follow

    follow.tick(s)
