from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cvm.api import avatars, channels, jobs, media, projects, publish, trends
from cvm.api import settings as settings_api
from cvm.api import em_alta, grow
from cvm.api import setup as setup_api
from cvm.api import stats as stats_api
from cvm.config import settings
from cvm.db import init_db
from cvm.paths import NO_CACHE


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Content Video Maker", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(em_alta.router)  # Em alta v2: /trends/discover|research|follow, before /trends/{trend_id}
for router in (avatars.router, channels.router, trends.router, projects.router, media.router, jobs.router, publish.router, settings_api.router):
    app.include_router(router)
app.include_router(stats_api.router)  # YouTube Analytics + weekly cycle (Phase 8)
app.include_router(setup_api.router)  # install wizard state
app.include_router(grow.router)  # menu "Crescer": lives → melhores momentos + Shorts, monetization


@app.get("/health")
def health() -> dict:
    return {"ok": True}


def run() -> None:
    uvicorn.run("cvm.main:app", host=settings.api_host, port=settings.api_port, reload=False)


@app.get("/assets/{asset_id}/file")
def asset_file(asset_id: int):
    from pathlib import Path

    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from sqlmodel import Session

    from cvm.db import engine
    from cvm.models import Asset

    with Session(engine) as s:
        a = s.get(Asset, asset_id)
    if not a or not a.path or not Path(a.path).exists():
        raise HTTPException(404)
    return FileResponse(a.path, headers=NO_CACHE)


@app.get("/system/status")
def system_status():
    """Sidebar status: models ready, Claude auto-run, last trend collection."""
    from sqlalchemy import func
    from sqlmodel import Session, select

    from cvm.db import engine
    from cvm.models import Trend
    from cvm import claude_limit, engines
    from cvm.models_registry import status

    models = status()
    with Session(engine) as s:
        last = s.exec(select(func.max(Trend.last_seen))).one()
        limit = claude_limit.state(s) if claude_limit.blocked_until(s) else None
    return {"models_ready": sum(m["ready"] for m in models.values()), "models_total": len(models),
            "claude_auto_run": engines.auto_run(), "claude_limit": limit,
            "trends_last_collected_at": last, "trends_every_min": 30}


@app.get("/system/languages")
def system_languages():
    """Languages offered for new videos/channels/avatars (CVM_VIDEO_LANGUAGES) and the UI languages."""
    from cvm.config import settings

    return {"video": list(settings.video_languages), "ui": ["pt", "en"]}


@app.get("/system/locale")
def system_locale():
    """The studio's timezone (CVM_DEFAULT_TZ, else this machine's): the UI shows publishing times in it beside the
    audience's, and the daily/weekly routines run on it."""
    from cvm.config import home_tz

    return {"tz": home_tz().key}


@app.delete("/system/claude-limit")
def clear_claude_limit():
    """The user knows the limit is over (e.g. plan upgraded): let the waiting Claude steps go now."""
    from cvm.workers.tasks import release_claude_limit

    return {"resumed": release_claude_limit()}


@app.get("/system/models")
def models_status():
    from cvm.models_registry import status

    return status()
