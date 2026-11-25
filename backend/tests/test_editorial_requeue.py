import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from cvm import pipeline
from cvm.models import Job, Project

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "requeue_editorial.py"
if not SCRIPT.is_file():  # an owner-only maintenance script, left out of the public copy
    pytest.skip("no scripts/requeue_editorial.py (public copy)", allow_module_level=True)


def load():
    spec = importlib.util.spec_from_file_location("requeue_editorial", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def session():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        s.add(Project(id=1, title="a", topic="a", presenter="podcast", voice_variant="mix"))
        s.add(Job(project_id=1, state="awaiting_publish_review", history=[]))
        s.add(Project(id=2, title="b", topic="b", presenter="podcast"))
        s.add(Job(project_id=2, state="published", history=[]))
        s.commit()
        yield s


def test_revision_from_publish_review():
    job = Job(project_id=1, state="awaiting_publish_review", history=[])
    pipeline.send_to_revision(job, "x")
    assert job.state == "scripting" and job.history[-1]["revise"] is True


def test_requeue_dry_run_changes_nothing(session):
    out = load().requeue(session, [1, 2])
    assert any("dry-run" in line for line in out)
    assert session.get(Project, 1).presenter == "podcast"
    assert session.get(Job, 1).state == "awaiting_publish_review"


def test_requeue_sets_editorial_and_note(session):
    out = load().requeue(session, [1, 2], apply=True)
    p, j = session.get(Project, 1), session.get(Job, 1)
    assert p.presenter == "editorial" and p.voice_variant == "mix"
    assert j.state == "scripting" and j.history[-1]["note"].startswith("refazer no formato editorial")
    assert session.get(Project, 2).presenter == "podcast" and session.get(Job, 2).state == "published"
    assert any(line.startswith("2: skipped") for line in out)
