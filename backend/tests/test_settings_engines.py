"""Configurações: engines read at run time (no restart), the ElevenLabs key kept out of every response and log,
only downloaded models selectable, and the rules per video type reaching get_project."""

import io
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm import db, engines
from cvm.api import settings as settings_api
from cvm.db import get_session
from cvm.engines import _stored as REAL_STORED
from cvm.models import Channel, Job, Platform, Project

KEY = "sk_test_0123456789abcdef0123456789abcdef"


@pytest.fixture
def client(monkeypatch):
    from huey import MemoryHuey

    from cvm import gpu

    monkeypatch.setattr(gpu, "lock", MemoryHuey("tests-engines").lock_task("gpu"))
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    monkeypatch.setattr(db, "engine", e)
    monkeypatch.setattr(engines, "_stored", REAL_STORED)  # conftest hides the live Setting; here the test DB is used
    monkeypatch.setattr(engines, "CACHE_S", 0.0)
    engines.invalidate()
    app = FastAPI()
    app.include_router(settings_api.router)

    def sess():
        with Session(e) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    return TestClient(app), e


def test_writer_toggle_changes_behaviour_without_restart(client, monkeypatch):
    from cvm.config import settings
    from cvm.workers import tasks

    c, e = client
    monkeypatch.setattr(settings, "claude_auto_run", True)
    with Session(e) as s:
        p = Project(title="t", topic="t")
        s.add(p)
        s.commit()
        s.add(Job(project_id=p.id, state="scripting"))
        s.commit()
    started = []
    monkeypatch.setattr(tasks, "run_job", lambda job_id: started.append(job_id))

    assert c.get("/settings/engines").json()["writer"] == "claude"
    assert tasks.slot_kind("scripting") == "claude"
    r = c.put("/settings/engines", json={"writer": "manual"})
    assert r.status_code == 200 and r.json()["writer"] == "manual"
    assert tasks.slot_kind("scripting") is None and "scripting" not in tasks.running_states()
    assert started == []
    r = c.put("/settings/engines", json={"writer": "claude"})
    assert tasks.slot_kind("seo") == "claude"
    assert r.json()["started"] == started == [1]  # the step that waited for the button starts now
    assert c.put("/settings/engines", json={"writer": "gpt"}).status_code == 422


def test_codex_switches(client, monkeypatch):
    from cvm.scripting import agents, claude_runner, codex_runner

    c, e = client
    monkeypatch.setattr(codex_runner, "installed", lambda: True)
    assert codex_runner.available()
    c.put("/settings/engines", json={"codex_fallback": False})
    assert not codex_runner.available()  # the reserve is off

    calls = []
    monkeypatch.setattr(claude_runner, "run_claude", lambda p, timeout=1800, step=None: calls.append("claude") or {"result": "ok"})
    monkeypatch.setattr(codex_runner, "run_codex", lambda p, timeout=1800: calls.append("codex") or {"result": "ok"})
    c.put("/settings/engines", json={"writer": "codex"})
    with Session(e) as s:
        ch = Channel(name="c", platform=Platform.youtube, language="pt-BR")
        s.add(ch)
        s.commit()
        p = Project(title="t", topic="t", channel_id=ch.id, style="explainer")
        s.add(p)
        s.commit()
        assert agents.run("Use the seo-optimizer subagent", "seo", s, p)["writer"] == "codex"
        kids = Project(title="k", topic="k", channel_id=ch.id, style="kids")
        s.add(kids)
        s.commit()
        assert agents.run("Use the scriptwriter subagent", "scripting", s, kids)["writer"] == "claude"  # kids: Claude
    assert calls == ["codex", "claude"]


def test_unready_model_is_refused(client, monkeypatch):
    from cvm.visuals import sources

    c, _ = client
    monkeypatch.setattr(engines, "video_ready", lambda eng: eng == "ltx")
    monkeypatch.setattr(engines, "image_ready", lambda eng: eng == "flux")
    body = c.get("/settings/engines").json()
    assert {o["id"]: o["ready"] for o in body["options"]["video"]} == {"ltx": True, "wan": False}
    assert c.put("/settings/engines", json={"video": {"realistic": "wan"}}).status_code == 409
    assert c.put("/settings/engines", json={"image_engine": "z-image"}).status_code == 200  # unchanged default: fine
    assert c.put("/settings/engines", json={"image_engine": "flux"}).json()["image_engine"] == "flux"

    monkeypatch.setattr(engines, "video_ready", lambda eng: True)
    assert c.put("/settings/engines", json={"video": {"realistic": "wan", "kids": "ltx"}}).status_code == 200
    made = []
    monkeypatch.setattr(sources, "ltx_ready", lambda: True)
    monkeypatch.setattr(sources, "ltx_version", lambda: "2.5")
    monkeypatch.setattr(sources, "ltx_clip", lambda *a, **k: made.append("ltx"))
    monkeypatch.setattr(sources, "wan_clip", lambda *a, **k: made.append("wan"))
    sources.animate_clip("a.png", "p", "o.mp4", 4.0, purpose="realistic")
    sources.animate_clip("a.png", "p", "o.mp4", 4.0, purpose="kids")
    assert made == ["wan", "ltx"]
    monkeypatch.setattr("cvm.models_registry.is_ready", lambda name: True)
    monkeypatch.setattr("cvm.models_registry.MLX", True)
    assert sources.image_engine() == "flux"  # the choice reached the drawing code


def test_elevenlabs_key_never_leaves(client, monkeypatch, caplog):
    import urllib.request

    from cvm.voice import eleven

    c, e = client
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr("cvm.config.settings.elevenlabs_api_key", None)
    assert c.get("/settings/secrets/elevenlabs").json()["configured"] is False
    assert c.put("/settings/secrets/elevenlabs", json={"key": "bad key"}).status_code == 422

    seen = []

    class Resp(io.BytesIO):
        headers = type("H", (), {"get_content_type": lambda self: "application/json"})()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        seen.append(req.get_header("Xi-api-key"))
        return Resp(json.dumps({"character_limit": 100000, "character_count": 2500}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    responses = [c.put("/settings/secrets/elevenlabs", json={"key": KEY}), c.get("/settings/secrets/elevenlabs"),
                 c.post("/settings/secrets/elevenlabs/test")]
    assert responses[0].json()["configured"] is True and responses[0].json()["source"] == "keychain"
    assert responses[2].json()["remaining"] == 97500
    assert seen == [KEY]  # the pipeline reads the Keychain copy
    assert eleven.available()
    responses.append(c.delete("/settings/secrets/elevenlabs"))
    assert responses[-1].json()["configured"] is False
    for r in responses:
        assert KEY not in r.text
    assert KEY not in caplog.text
    from sqlmodel import text

    with e.connect() as conn:  # nothing about it in the DB
        assert not any(KEY in str(row) for row in conn.execute(text("SELECT * FROM setting")))

    monkeypatch.setattr("cvm.config.settings.elevenlabs_api_key", "env_key_0123456789abcdef")
    assert c.get("/settings/secrets/elevenlabs").json() == {"configured": True, "source": "env"}  # .env fallback


def test_style_rules_reach_get_project(client, monkeypatch):
    from cvm.mcp_server import server

    c, e = client
    monkeypatch.setattr(server, "engine", e)
    body = c.get("/settings/style-rules").json()
    assert body["fields"] == ["thumbnail", "title", "hook", "ending", "bumper"] and body["rules"]["dark"]["title"] == ""
    r = c.put("/settings/style-rules", json={"rules": {"dark": {"title": "Pergunta curta, sem clickbait", "hook": " "},
                                                      "kids": {"ending": "Sempre um gancho para o próximo episódio"}}})
    assert r.status_code == 200 and r.json()["rules"]["dark"]["title"] == "Pergunta curta, sem clickbait"
    assert c.put("/settings/style-rules", json={"rules": {"dark": {"music": "x"}}}).status_code == 422
    assert c.put("/settings/style-rules", json={"rules": {"nope": {"title": "x"}}}).status_code == 422
    with Session(e) as s:
        dark = Project(title="d", topic="d", style="dark")
        plain = Project(title="e", topic="e", style="explainer")
        s.add(dark)
        s.add(plain)
        s.commit()
        ids = dark.id, plain.id
    assert server._project_data(ids[0])["style_rules"] == {"title": "Pergunta curta, sem clickbait"}
    assert server._project_data(ids[1])["style_rules"] is None
