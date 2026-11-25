from huey import MemoryHuey
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from cvm import gpu
from cvm.models import Job, Project
from cvm.progress import orphaned
from cvm.workers import tasks


def test_paused_job_stops_and_is_not_orphaned(monkeypatch):
    monkeypatch.setattr(gpu, "lock", MemoryHuey("test").lock_task("gpu"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(tasks, "engine", engine)
    with Session(engine) as s:
        p = Project(topic="pausa", title="pausa")
        s.add(p)
        s.commit()
        job = Job(project_id=p.id, state="voicing", paused=True, progress={"waiting": "gpu", "since": "2026-01-01T00:00:00+00:00"})
        s.add(job)
        s.commit()
        jid = job.id
    called = []
    monkeypatch.setitem(tasks.HANDLERS, "voicing", lambda job, session: called.append(1))
    tasks._drive(jid, set())
    with Session(engine) as s:
        job = s.get(Job, jid)
        assert not called and job.state == "voicing" and job.progress == {}
        assert not orphaned(job, {"voicing"})


def test_job_parked_for_claude_limit_is_not_orphaned():
    job = Job(project_id=1, state="scripting", progress={"waiting": "claude_limit", "until": "2026-11-13T20:52:00+00:00",
                                                         "since": "2026-11-13T09:00:00+00:00"})
    assert not orphaned(job, {"scripting"})  # no "checked": it is parked until claude_limit_tick, not polling


def test_paused_job_still_passes_auto_approved_gate(monkeypatch):
    from cvm import autopilot

    monkeypatch.setattr(gpu, "lock", MemoryHuey("test").lock_task("gpu"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(tasks, "engine", engine)
    with Session(engine) as s:
        autopilot.put(s, {"auto_approve_script": True})
        p = Project(topic="pausa", title="pausa")
        s.add(p)
        s.commit()
        job = Job(project_id=p.id, state="awaiting_approval", paused=True)
        s.add(job)
        s.commit()
        jid = job.id
    called = []
    monkeypatch.setitem(tasks.HANDLERS, "voicing", lambda job, session: called.append(1))
    tasks._drive(jid, set())
    with Session(engine) as s:
        job = s.get(Job, jid)
        assert job.state == "voicing" and job.paused and not called  # approved, then held by the pause
