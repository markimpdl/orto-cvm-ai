"""Codex writes only while Claude is at its limit, and only where the user accepts it (cvm/scripting/agents.py)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from cvm import claude_limit, db
from cvm.config import settings
from cvm.models import Channel, Platform, Project, Script
from cvm.scripting import agents, claude_runner, codex_runner

LATER = datetime.now(UTC) + timedelta(hours=3)


@pytest.fixture
def s(monkeypatch):
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    monkeypatch.setattr(db, "engine", e)
    monkeypatch.setattr(settings, "codex_fallback", True)
    monkeypatch.setattr(codex_runner, "available", lambda: True)
    with Session(e) as session:
        yield session


def _project(s, **ch):
    c = Channel(platform=Platform.youtube, **{"name": "c", "language": "pt-BR", **ch})
    s.add(c)
    s.commit()
    p = Project(title="t", topic="t", channel_id=c.id, style="kids" if ch.get("made_for_kids") else "explainer")
    s.add(p)
    s.commit()
    return p


def test_policy(s):
    assert agents.codex_allowed(s, "seo") and agents.codex_allowed(s, "ideas")
    assert agents.codex_allowed(s, "scripting", _project(s))
    assert not agents.codex_allowed(s, "scripting", _project(s, made_for_kids=True))
    assert not agents.codex_allowed(s, "scripting", _project(s, language="ar-AE"))
    assert agents.codex_allowed(s, "scripting", _project(s, language="ar-AE", prefs={"codex_scripts": True}))
    assert not agents.codex_allowed(s, "scripting", _project(s, prefs={"codex_scripts": False}))


def test_claude_first_codex_only_at_its_limit(s, monkeypatch):
    calls = []
    monkeypatch.setattr(claude_runner, "run_claude", lambda prompt, timeout=1800, step=None: calls.append("claude") or {"result": "ok"})
    monkeypatch.setattr(codex_runner, "run_codex", lambda prompt, timeout=1800, step=None: calls.append("codex") or {"result": "ok"})
    p = _project(s)
    assert agents.run("Use the scriptwriter subagent", "scripting", s, p)["writer"] == "claude"
    assert agents.wait_until(s, "scripting", p) is None

    def limited(prompt, timeout=1800, step=None):
        raise claude_limit.ClaudeLimit(LATER, "usage limit reached")

    monkeypatch.setattr(claude_runner, "run_claude", limited)
    claude_limit.set_limit(s, LATER, "usage limit reached")

    def codex_writes(prompt, timeout=1800):
        assert "## Instructions of the scriptwriter subagent" in prompt  # Codex gets the agent's rules inline
        s.add(Script(project_id=p.id, language="pt-BR", content={"scenes": []}))
        s.commit()
        return {"result": "ok"}

    monkeypatch.setattr(codex_runner, "run_codex", codex_writes)
    assert agents.wait_until(s, "scripting", p) is None  # Codex can take it
    assert agents.run("Use the scriptwriter subagent", "scripting", s, p)["writer"] == "codex"
    assert s.exec(select(Script)).one().content["writer"] == "codex"
    kids = _project(s, made_for_kids=True)
    assert agents.wait_until(s, "scripting", kids) is not None  # kids wait for Claude
    with pytest.raises(claude_limit.ClaudeLimit):
        agents.run("Use the scriptwriter subagent", "scripting", s, kids)


def test_both_limited_waits_for_the_earliest_reset(s, monkeypatch):
    soon = datetime.now(UTC) + timedelta(minutes=40)
    claude_limit.set_limit(s, LATER, "usage limit reached")
    claude_limit.set_limit(s, soon, "try again in 40 minutes", "codex")
    p = _project(s)
    assert agents.wait_until(s, "scripting", p) == datetime.fromisoformat(soon.isoformat(timespec="seconds"))
    monkeypatch.setattr(claude_runner, "run_claude", lambda prompt, timeout=1800, step=None: (_ for _ in ()).throw(
        claude_limit.ClaudeLimit(LATER, "usage limit reached")))
    with pytest.raises(claude_limit.ClaudeLimit) as e:
        agents.run("Use the seo-optimizer subagent", "seo", s, p)
    assert e.value.until <= LATER


def test_codex_prompt_carries_agents_and_style():
    text = codex_runner.prompt_for("Use the scriptwriter subagent ... Then use the qa-reviewer subagent", "top_list")
    assert "Instructions of the scriptwriter subagent" in text and "Instructions of the qa-reviewer subagent" in text
    assert "script-style-top-list" in text
