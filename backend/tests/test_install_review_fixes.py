"""Whole-branch review fixes of the install wizard (part 1A): "Sugestões da IA" off really stops the AI idea runs,
GET /setup/features reads the keystore only when asked, unmapped regions get a clear language, PUT /setup validates."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from cvm.api import channels as channels_api
from cvm.api import setup as setup_api
from cvm.api import trends as trends_api
from cvm.config import settings
from cvm.db import get_session
from cvm.models import Channel, TrendAnalysis
from cvm.setup import features, state
from cvm.workers import tasks


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    monkeypatch.setattr(tasks, "engine", e)
    return e


@pytest.fixture
def queued(monkeypatch):
    """run_trend_analysis never reaches the live Huey storage: the calls are recorded."""
    calls = []
    monkeypatch.setattr(tasks, "run_trend_analysis", lambda aid: calls.append(aid))
    return calls


@pytest.fixture
def client(engine):
    app = FastAPI()
    for r in (setup_api.router, trends_api.router, channels_api.router):
        app.include_router(r)

    def sess():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    return TestClient(app)


def _ideas_off(engine):
    with Session(engine) as s:
        features.put(s, {"ai_ideas": False})


# ---------- 1. "Sugestões da IA" ----------

def test_ai_ideas_on_without_a_row(engine):
    with Session(engine) as s:
        assert features.ai_ideas(s) is True


def test_analyze_refused_when_ai_ideas_off(engine, client, queued):
    _ideas_off(engine)
    r = client.post("/trends/analyze", json={})
    assert r.status_code == 409 and queued == []
    with Session(engine) as s:
        assert s.exec(select(TrendAnalysis)).all() == []


def test_analyze_runs_without_a_features_row(engine, client, queued):
    assert client.post("/trends/analyze", json={}).status_code == 200 and len(queued) == 1


def test_refresh_collects_but_asks_no_ideas_when_off(engine, client, monkeypatch):
    _ideas_off(engine)
    seen = []
    monkeypatch.setattr(tasks, "collect_trends", lambda aid=None: seen.append(aid))
    r = client.post("/trends/refresh", json={"analyze": True})
    assert r.status_code == 200 and r.json()["analysis"] is None and seen == [None]


def test_channel_ideas_refused_when_off(engine, client, queued):
    with Session(engine) as s:
        s.add(Channel(name="c", platform="youtube", language="pt-BR"))
        s.commit()
        cid = s.exec(select(Channel)).one().id
    _ideas_off(engine)
    r = client.post(f"/channels/{cid}/ideas")
    assert r.status_code == 409 and queued == []


def test_run_trend_analysis_spends_nothing_when_off(engine, monkeypatch):
    from cvm.scripting import agents

    _ideas_off(engine)
    monkeypatch.setattr(agents, "run", lambda *a, **k: pytest.fail("AI run with ideas off"))
    with Session(engine) as s:
        a = TrendAnalysis(region=None)
        s.add(a)
        s.commit()
        aid = a.id
    tasks.run_trend_analysis.call_local(aid)
    with Session(engine) as s:
        a = s.get(TrendAnalysis, aid)
        assert a.status == "failed" and "desligadas" in a.error


@pytest.mark.parametrize("on", [True, False])
def test_autopilot_tick_asks_ideas_only_when_on(engine, queued, monkeypatch, on):
    from cvm import autopilot
    from cvm.scripting import agents

    monkeypatch.setattr(autopilot, "get", lambda s: {"enabled": True})
    monkeypatch.setattr(autopilot, "enqueue", lambda s: [])
    monkeypatch.setattr(agents, "wait_until", lambda *a, **k: None)
    if not on:
        _ideas_off(engine)
    tasks.autopilot_tick.call_local()
    assert len(queued) == (1 if on else 0)


def test_weekly_cycle_asks_no_ideas_when_off(engine, monkeypatch):
    from cvm.publish import analytics, weekly

    _ideas_off(engine)
    monkeypatch.setattr(analytics, "connected", lambda c: True)
    monkeypatch.setattr(analytics, "refresh", lambda s, c, **k: {"top_views": [{"id": "x"}]})
    with Session(engine) as s:
        c = Channel(name="c", platform="youtube", language="pt-BR")
        s.add(c)
        s.commit()
        assert weekly.run_channel(s, c, claude=lambda *a, **k: pytest.fail("AI run with ideas off")) is None
        assert s.exec(select(TrendAnalysis)).all() == []


# ---------- 2. keys only when asked ----------

def test_get_features_reads_keys_only_with_the_flag(client, monkeypatch):
    calls = []
    monkeypatch.setattr(features, "keys_set", lambda: calls.append(1) or {"pexels": True})
    r = client.get("/setup/features")
    assert r.status_code == 200 and "keys" not in r.json() and calls == []
    r = client.get("/setup/features?keys=1")
    assert r.json()["keys"] == {"pexels": True} and calls == [1]


# ---------- 5. regions without a language ----------

def test_region_language_fallback():
    from cvm import suggest

    assert suggest.region_lang("BR") == "pt-BR" and suggest.region_lang("AE") == "ar-AE"
    assert suggest.region_lang("US") == "en-US"
    assert suggest.region_lang("DE") == "en-US" and suggest.region_lang("JP") == "en-US"
    assert suggest.region_lang("GLOBAL") == "en-US"
    assert suggest.region_lang(None) is None


def test_suggestion_for_unmapped_region_is_en_us(engine):
    from cvm import suggest

    with Session(engine) as s:
        assert suggest.suggestion(s, [], "DE")["languages"] == ["en-US"]
        assert suggest.suggestion(s, [], "GLOBAL")["languages"] == ["en-US"]
        assert suggest.suggestion(s, [], None)["languages"] == ["pt-BR"]  # no region: as before
        en, pt = Channel(id=1, name="en", platform="youtube", language="en-US"), Channel(id=2, name="pt", platform="youtube", language="pt-BR")
        assert suggest.pick_channel([pt, en], "JP", None) is en


# ---------- 6. PUT /setup validation ----------

@pytest.mark.parametrize("body", [{"step": "nope"}, {"step": 3}, {"step": "ai", "choices": ["x"]},
                                  {"step": "ai", "choices": "x"}])
def test_put_setup_rejects_bad_input(client, body):
    assert client.put("/setup", json=body).status_code == 422


def test_put_setup_accepts_good_input(client):
    r = client.put("/setup", json={"step": "features", "choices": {"language": "pt-BR"}})
    assert r.status_code == 200 and r.json()["step"] == "features"
    assert client.put("/setup", json={}).json()["step"] == "system"
    assert set(state.STEPS) >= {"system", "features"}
