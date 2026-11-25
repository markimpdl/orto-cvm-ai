"""Monetization numbers from vidIQ while YouTube Analytics is not connected (Google OAuth client still missing)."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm.config import settings
from cvm.grow import vidiq_stats
from cvm.models import Channel


def test_fetch_parses_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    ans = json.dumps({"used_vidiq": True, "subscribers": 504, "hours_365_long": 18.4, "minutes_28d": 1680, "subs_gained_28d": 28,
                      "shorts_views_90d": 9484, "shorts_views_28d": 4200})
    seen = {}
    out = vidiq_stats.fetch("UCabc", run=lambda p, **kw: seen.update(kw) or {"result": ans})
    assert out["subscribers"] == 504 and out["hours_365_long"] == 18.4 and out["minutes_28d"] == 1680
    assert out["shorts_views_90d"] == 9484 and out["shorts_views_28d"] == 4200
    assert "mcp__cvm__*" in seen["disallowed_tools"]


def test_fetch_without_vidiq_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    assert vidiq_stats.fetch("UCabc", run=lambda p, **kw: {"result": '{"used_vidiq": false}'}) is None


def test_monetization_uses_vidiq_when_analytics_missing():
    from cvm.api import grow
    from cvm.db import get_session
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Channel(id=4, name="M", platform="youtube", youtube_channel_id="UCabc"))
        s.commit()
        vidiq_stats.save(s, 4, {"subscribers": 504, "hours_365_long": 18.0, "minutes_28d": 1680, "subs_gained_28d": 28,
                                "shorts_views_90d": 1_000_000, "shorts_views_28d": 280_000, "at": "2026-11-21T00:00:00+00:00"})
    app = FastAPI()
    app.include_router(grow.router)

    def sess():
        with Session(engine) as x:
            yield x
    app.dependency_overrides[get_session] = sess
    r = TestClient(app).get("/grow/4/monetization").json()
    assert r["source"] == "vidiq" and r["subs"] == 504 and r["hours"] == 18.0 and r["pace"]["days_to_subs"] == 496
    # Shorts path: 10M views in 90 days; at 10k/day the 90-day window reaches 900k — never 10M at this rate
    assert r["shorts"] == {"views_90d": 1_000_000, "target": 10_000_000, "views_per_day": 10000.0, "needed_per_day": 111111}
