"""Phase 0 (2026-11-15): category chosen in code, the system hands scripts to the user, isolated `claude -p`."""
from types import SimpleNamespace

from huey import MemoryHuey
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from cvm import gpu
from cvm.models import Channel, Job, Project, Script
from cvm.publish.platforms import category_for
from cvm.workers import tasks


def _render(title="", tags=(), seo_extra=None):
    return SimpleNamespace(seo={"title": title, "tags": list(tags), **(seo_extra or {})}, project_id=None)


def _channel(prefs=None, kids=False, style="explainer"):
    return SimpleNamespace(prefs=prefs or {}, made_for_kids=kids, default_style=style)


def test_category_order():
    gta = _render("GTA 6 adiado de novo")
    assert category_for(gta, _channel({"youtube_category": "29", "youtube_category_fixed": True})) == "29"
    assert category_for(gta, _channel(), style="kids") == "1"  # kids series, even when the title is about a game
    assert category_for(gta, _channel(kids=True), style="explainer") == "1"
    assert category_for(gta, _channel(), style="lesson") == "27"
    assert category_for(gta, _channel(), style="review") == "28"
    assert category_for(gta, _channel(), style="explainer") == "20"  # topic from the title
    plain = _render("Uma história bonita")
    assert category_for(plain, _channel({"youtube_category": "29"}), style="react") == "29"  # channel's usual one
    assert category_for(plain, _channel(), style="dark") == "24"
    assert category_for(plain, _channel(), style="explainer") == "27"
    assert category_for(_render("x", seo_extra={"category_id": "17"}), _channel(), style="kids") == "17"  # typed by hand


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def _setup(monkeypatch, writes):
    monkeypatch.setattr(gpu, "lock", MemoryHuey("test").lock_task("gpu"))
    engine = _db()
    monkeypatch.setattr(tasks, "engine", engine)
    with Session(engine) as s:
        ch = Channel(name="C", platform="youtube", language="pt-BR")
        s.add(ch)
        s.commit()
        p = Project(topic="t", title="t", style="explainer", channel_id=ch.id, languages=["pt-BR", "en-US"])
        s.add(p)
        s.commit()
        job = Job(project_id=p.id, state="scripting")
        s.add(job)
        s.commit()
        jid, pid = job.id, p.id

    def fake_run(job, session, prompt):  # stands for Claude or Codex: saves scripts, never moves the job
        for lang in writes:
            session.add(Script(project_id=pid, language=lang, content={"scenes": []}))
        session.commit()
        return {"result": "Roteiro sobre t.\nQA: aprovado, 1 sugestão corrigida", "writer": "codex"}

    monkeypatch.setattr("cvm.scripting.agents.run_job_step", fake_run)
    return engine, jid


def test_system_hands_script_to_user_after_qa(monkeypatch):
    engine, jid = _setup(monkeypatch, ["pt-BR", "en-US"])
    with Session(engine) as s:
        job = s.get(Job, jid)
        tasks._claude_step(job, s)
        assert job.state == "awaiting_approval"
        assert job.history[-1]["note"] == "QA: aprovado, 1 sugestão corrigida"


def test_missing_language_still_fails(monkeypatch):
    import pytest

    engine, jid = _setup(monkeypatch, ["pt-BR"])
    with Session(engine) as s, pytest.raises(RuntimeError):
        tasks._claude_step(s.get(Job, jid), s)


def test_pipeline_claude_runs_isolated(monkeypatch):
    from cvm.scripting import claude_runner

    monkeypatch.setattr(claude_runner.settings, "claude_isolated", True)
    monkeypatch.setattr(claude_runner.settings, "claude_model", "opus")
    monkeypatch.setattr(claude_runner.settings, "claude_model_light", "sonnet")
    args = claude_runner.isolation_args("scripting")
    assert args[:2] == ["--setting-sources", "project"] and "--strict-mcp-config" in args
    assert args[-2:] == ["--model", "opus"] and "claudeMdExcludes" in args[args.index("--settings") + 1]
    assert claude_runner.isolation_args("seo")[-2:] == ["--model", "sonnet"]
    assert claude_runner.isolation_args("ideas")[-2:] == ["--model", "sonnet"]
    monkeypatch.setattr(claude_runner.settings, "claude_isolated", False)
    assert claude_runner.isolation_args() == []
