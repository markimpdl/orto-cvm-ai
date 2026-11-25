import json
from datetime import UTC, datetime, timedelta

import pytest
from huey import MemoryHuey
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from cvm import claude_limit, db, gpu
from cvm.models import Job, Project
from cvm.scripting import claude_runner
from cvm.workers import tasks

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)  # 14:00 in Dubai


def test_reset_time_from_the_cli_messages():
    assert claude_limit.reset_time("Claude AI usage limit reached|1794571200", NOW) == \
        datetime.fromtimestamp(1794571200, UTC) + claude_limit.MARGIN
    at = claude_limit.reset_time("You've hit your limit · resets 3pm (Asia/Dubai)", NOW)
    assert at == datetime(2026, 11, 13, 11, 0, tzinfo=UTC) + claude_limit.MARGIN  # 15:00 Dubai, same day
    at = claude_limit.reset_time("5-hour limit reached ∙ resets 1:30pm (Asia/Dubai)", NOW)
    assert at == datetime(2026, 11, 14, 9, 30, tzinfo=UTC) + claude_limit.MARGIN  # already past today: tomorrow
    at = claude_limit.reset_time("Weekly limit reached ∙ resets Mon 9am (Asia/Dubai)", NOW)  # NOW is a Friday
    assert at == datetime(2026, 11, 16, 5, 0, tzinfo=UTC) + claude_limit.MARGIN
    assert claude_limit.reset_time("usage limit reached", NOW) == NOW + claude_limit.FALLBACK
    assert claude_limit.is_limit("Claude AI usage limit reached|1794571200")
    assert not claude_limit.is_limit("Roteiro salvo com 12 cenas")


@pytest.fixture
def engine(monkeypatch):
    from cvm.config import settings

    monkeypatch.setattr(settings, "codex_fallback", False)  # these tests are about Claude alone
    monkeypatch.setattr(gpu, "lock", MemoryHuey("test").lock_task("gpu"))
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    monkeypatch.setattr(tasks, "engine", e)
    monkeypatch.setattr(db, "engine", e)
    return e


class FakeProc:
    def __init__(self, out, code=1):
        self.out, self.returncode, self.pid = out, code, 4242

    def communicate(self, timeout=None):
        return self.out, ""


def test_limit_message_stops_claude_until_the_reset(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(claude_runner, "RUN_DIR", tmp_path)
    calls = []

    def popen(*a, **k):
        calls.append(1)
        return FakeProc(json.dumps({"is_error": True, "result": "Claude AI usage limit reached|4102444800"}))

    monkeypatch.setattr(claude_runner.subprocess, "Popen", popen)
    with pytest.raises(claude_limit.ClaudeLimit):
        claude_runner.run_claude("oi")
    with pytest.raises(claude_limit.ClaudeLimit):  # still limited: not even called
        claude_runner.run_claude("oi")
    assert len(calls) == 1
    with Session(engine) as s:
        assert claude_limit.blocked_until(s).year == 2100


def _job(engine, state="scripting"):
    with Session(engine) as s:
        p = Project(topic="t", title="t")
        s.add(p)
        s.commit()
        j = Job(project_id=p.id, state=state)
        s.add(j)
        s.commit()
        return j.id


def test_claude_step_waits_instead_of_failing_and_resumes_after_reset(engine, monkeypatch):
    monkeypatch.setattr(tasks.settings, "claude_auto_run", True)
    jid = _job(engine)
    until = datetime.now(UTC) + timedelta(hours=2)

    def limited(job, session):
        raise claude_limit.ClaudeLimit(until, "usage limit reached")

    monkeypatch.setitem(tasks.HANDLERS, "scripting", limited)
    tasks._drive(jid, set())
    with Session(engine) as s:
        job = s.get(Job, jid)
        assert job.state == "scripting" and job.progress["waiting"] == "claude_limit"
        assert "retoma às" in job.progress["label"]
        claude_limit.set_limit(s, until, "usage limit reached")
    # while limited, a new Claude step does not even start; the rest of the queue is untouched
    called = []
    monkeypatch.setitem(tasks.HANDLERS, "scripting", lambda job, session: called.append(1))
    tasks._drive(jid, set())
    assert not called
    resumed = []
    monkeypatch.setattr(tasks, "run_job", lambda job_id: resumed.append(job_id))
    tasks.claude_limit_tick.call_local()
    assert resumed == []  # not yet
    with Session(engine) as s:
        claude_limit.set_limit(s, datetime.now(UTC) - timedelta(minutes=1), "usage limit reached")
    tasks.claude_limit_tick.call_local()
    assert resumed == [jid]
    with Session(engine) as s:
        assert claude_limit.state(s) is None


def test_codex_messages_reset_time():
    at = claude_limit.reset_time("You've hit your usage limit. Upgrade to Pro or try again in 1 day 2 hours 5 minutes.", NOW)
    assert at == NOW + timedelta(days=1, hours=2, minutes=5) + claude_limit.MARGIN
    at = claude_limit.reset_time("You've hit your usage limit, try again at 3:20 PM (Asia/Dubai).", NOW)
    assert at == datetime(2026, 11, 13, 11, 20, tzinfo=UTC) + claude_limit.MARGIN


def test_five_hour_limit_message():
    msg = "You've hit your 5-hour limit · resets 10pm (Asia/Dubai)"
    assert claude_limit.is_limit(msg) and claude_limit.is_limit('"api_error":"usage_limit_reached"')
    assert claude_limit.reset_time(msg, NOW) == datetime(2026, 11, 13, 18, 0, tzinfo=UTC) + claude_limit.MARGIN


def test_visuals_job_waiting_on_the_limit_resumes_after_reset(engine, monkeypatch):
    """Review finding 2: the editorial backfill inside sourcing_visuals waits for the limit; the tick and "Liberar
    agora" must put that job back in line too (not only scripting/seo)."""
    jid = _job(engine, "sourcing_visuals")
    until = datetime.now(UTC) + timedelta(hours=2)

    def limited(job, session):
        raise claude_limit.ClaudeLimit(until, "cenas editoriais esperam o limite")

    monkeypatch.setitem(tasks.HANDLERS, "sourcing_visuals", limited)
    tasks._drive(jid, set())
    with Session(engine) as s:
        job = s.get(Job, jid)
        assert job.state == "sourcing_visuals" and job.progress["waiting"] == "claude_limit"
        claude_limit.set_limit(s, until, "usage limit reached")
    resumed = []
    monkeypatch.setattr(tasks, "run_job", lambda job_id: resumed.append(job_id))
    tasks.claude_limit_tick.call_local()
    assert resumed == []
    with Session(engine) as s:
        claude_limit.set_limit(s, datetime.now(UTC) - timedelta(minutes=1), "usage limit reached")
    tasks.claude_limit_tick.call_local()
    assert resumed == [jid]
    resumed.clear()
    assert tasks.release_claude_limit() == [jid] and resumed == [jid]


def test_editorial_backfill_is_a_light_step():
    assert "editorial" in claude_runner.LIGHT_STEPS
