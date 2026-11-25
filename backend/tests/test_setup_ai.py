"""Install wizard AI step: status without spending a prompt, choice -> engines, Codex-only scripts."""
import json
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from cvm import db, engines
from cvm.api import setup as setup_api
from cvm.config import settings
from cvm.db import get_session
from cvm.models import Channel, Platform, Project
from cvm.scripting import agents, codex_runner
from cvm.setup import ai

REAL_STORED = engines._stored


@pytest.fixture
def s(monkeypatch):
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    monkeypatch.setattr(db, "engine", e)
    monkeypatch.setattr(settings, "codex_fallback", False)
    monkeypatch.setattr(engines, "_stored", REAL_STORED)  # conftest hides the live Setting; here the test DB is used
    monkeypatch.setattr(engines, "CACHE_S", 0.0)
    engines.invalidate()
    ai.invalidate()
    with Session(e) as session:
        yield session
    engines.invalidate()
    ai.invalidate()


def _project(s, **ch):
    c = Channel(platform=Platform.youtube, **{"name": "c", "language": "pt-BR", **ch})
    s.add(c)
    s.commit()
    p = Project(title="t", topic="t", channel_id=c.id, style="kids" if ch.get("made_for_kids") else "explainer")
    s.add(p)
    s.commit()
    return p


def _fake_status(monkeypatch, claude_in):
    ai.invalidate()
    monkeypatch.setattr(ai, "status", lambda fresh=False: {
        "claude": {"installed": claude_in, "logged_in": claude_in}, "codex": {"installed": True, "logged_in": True}})


@pytest.mark.parametrize("choice,writer,fallback", [("claude", "claude", False), ("both", "claude", True),
                                                    ("codex", "codex", False)])
def test_apply_maps_choice_to_engines(s, choice, writer, fallback):
    ai.apply(choice)
    assert engines.writer() == writer and engines.codex_fallback() is fallback


def test_apply_rejects_unknown(s):
    with pytest.raises(ValueError):
        ai.apply("gpt")


def test_codex_only_writes_kids_when_no_claude(s, monkeypatch):
    ai.apply("codex")
    monkeypatch.setattr(codex_runner, "available", lambda: True)
    _fake_status(monkeypatch, False)
    assert agents.codex_allowed(s, "scripting", _project(s, made_for_kids=True))
    assert agents.codex_allowed(s, "scripting", _project(s, language="ar-AE"))


def test_codex_kids_still_blocked_when_claude_present(s, monkeypatch):
    ai.apply("codex")
    _fake_status(monkeypatch, True)
    assert not agents.codex_allowed(s, "scripting", _project(s, made_for_kids=True))


def _run(monkeypatch, payload=None, exc=None, calls=None):
    monkeypatch.setattr(ai.shutil, "which", lambda n: "/bin/" + n)

    def fake(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
    monkeypatch.setattr(ai.subprocess, "run", fake)
    monkeypatch.setattr(codex_runner, "installed", lambda: False)


def test_claude_status_uses_auth_status_not_a_prompt(s, monkeypatch):
    calls = []
    _run(monkeypatch, {"loggedIn": True, "authMethod": "claude.ai"}, calls=calls)
    st = ai.status(fresh=True)
    assert st["claude"] == {"installed": True, "logged_in": True}
    assert calls[0][1:3] == ["auth", "status"] and "-p" not in calls[0]


def test_claude_api_key_is_not_logged_in(s, monkeypatch):
    _run(monkeypatch, {"loggedIn": True, "authMethod": "api_key"})
    c = ai.status(fresh=True)["claude"]
    assert c["logged_in"] is False and c["reason"]


def test_claude_error_and_cache(s, monkeypatch):
    calls = []
    _run(monkeypatch, exc=subprocess.TimeoutExpired("claude", 10), calls=calls)
    assert ai.status(fresh=True)["claude"]["logged_in"] is False
    ai.status()
    ai.status()
    assert len(calls) == 1


def test_routes(s, monkeypatch):
    app = FastAPI()
    app.include_router(setup_api.router)
    app.dependency_overrides[get_session] = lambda: s
    c = TestClient(app)
    _run(monkeypatch, {"loggedIn": True, "authMethod": "claude.ai"})
    assert c.get("/setup/ai").json()["claude"]["logged_in"] is True
    assert c.post("/setup/ai/test", json={"which": "claude"}).json()["logged_in"] is True
    assert c.post("/setup/ai/test", json={"which": "x"}).status_code == 422
    assert c.put("/setup/ai", json={"choice": "both"}).status_code == 200
    assert engines.codex_fallback() is True
    assert c.put("/setup/ai", json={"choice": "zzz"}).status_code == 422
