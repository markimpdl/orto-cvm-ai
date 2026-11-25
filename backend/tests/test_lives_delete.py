"""'Excluir' a sent video (owner, 2026-11-25): list item, cut review and files go; refused while a project made from
the recording is not rendered yet (nothing deleted)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm.api import grow
from cvm.config import settings
from cvm.db import get_session
from cvm.lives import cuts, runs
from cvm.models import Asset, Channel, CutReview, Job, Project, Setting

VID = "AAAAAAAAAA1"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    d = tmp_path / "lives" / "4"
    d.mkdir(parents=True)
    files = {"live_AAAAAAAAAA1.mp4": 3000, "live_AAAAAAAAAA1_proxy480.mp4": 500, "live_AAAAAAAAAA1_proxy360.mp4": 400,
             "live_AAAAAAAAAA1.transcript.pt-BR.json": 100, "AAAAAAAAAA1.live_chat.json": 50,
             "live_BBBBBBBBBB2.mp4": 999}  # another item's recording stays
    for name, size in files.items():
        (d / name).write_bytes(b"x" * size)
    video = d / "live_AAAAAAAAAA1.mp4"
    with Session(engine) as s:
        s.add(Channel(id=4, name="Studio", platform="youtube", language="pt-BR"))
        s.add(Setting(key="lives:4", value={"items": [
            {"video_id": VID, "title": "Live 1", "status": "ok", "at": "2026-11-25T10:00:00"},
            {"video_id": "BBBBBBBBBB2", "title": "Live 2", "status": "ok", "at": "2026-11-25T09:00:00"}]}))
        r = CutReview(channel_id=4, item_key=VID, source="lives", title="Live 1", status="done", video_path=str(video))
        s.add(r)
        s.commit()
        cuts.data_path(r.id).parent.mkdir(parents=True, exist_ok=True)
        cuts.data_path(r.id).write_text("{}")
    return engine, d, video


def _project(engine, video, state):
    with Session(engine) as s:
        p = Project(title="Short", topic="corte", channel_id=4)
        s.add(p)
        s.commit()
        s.add(Asset(project_id=p.id, kind="user_media", path=str(video)))
        if state:
            s.add(Job(project_id=p.id, state=state))
        s.commit()
        return p.id


@pytest.mark.parametrize("state", ["voicing", "failed", None])
def test_refused_while_a_project_still_needs_the_recording(env, state):
    engine, d, video = env
    _project(engine, video, "published")
    _project(engine, video, state)  # in production (or failed / not started: it may still run)
    with Session(engine) as s:
        with pytest.raises(runs.InUse, match="Ainda tem 1 corte em produção"):
            runs.delete_item(s, s.get(Channel, 4), VID)
        assert len(runs.status(s, s.get(Channel, 4))["items"]) == 2
        assert s.get(CutReview, 1) is not None
    assert video.exists() and (d / "live_AAAAAAAAAA1_proxy480.mp4").exists()


def test_deletes_item_review_and_files_once_every_project_is_rendered(env):
    engine, d, video = env
    _project(engine, video, "awaiting_publish_review")
    _project(engine, video, "published")
    with Session(engine) as s:
        out = runs.delete_item(s, s.get(Channel, 4), VID)
        assert out["freed"] == 3000 + 500 + 400 + 100 + 50 and out["files"] == 5
        assert [i["video_id"] for i in runs.status(s, s.get(Channel, 4))["items"]] == ["BBBBBBBBBB2"]
        assert s.get(CutReview, 1) is None
    assert not cuts.data_path(1).exists()
    assert sorted(p.name for p in d.iterdir()) == ["live_BBBBBBBBBB2.mp4"]


def test_refused_while_working_or_producing(env):
    engine, d, video = env
    with Session(engine) as s:
        ch = s.get(Channel, 4)
        r = s.get(CutReview, 1)
        r.status = "producing"
        s.add(r)
        s.commit()
        with pytest.raises(ValueError):
            runs.delete_item(s, ch, VID)
        runs.put(s, ch, {"video_id": "BBBBBBBBBB2", "title": "Live 2", "status": "working"})
        with pytest.raises(ValueError):
            runs.delete_item(s, ch, "BBBBBBBBBB2")
        with pytest.raises(KeyError):
            runs.delete_item(s, ch, "nope")


def test_never_deletes_outside_data_lives(env, tmp_path):
    engine, d, video = env
    outside = tmp_path / "projects" / "9" / "live_AAAAAAAAAA1.mp4"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"x")
    with Session(engine) as s:
        r = s.get(CutReview, 1)
        r.video_path = str(outside)
        s.add(r)
        s.commit()
        runs.delete_item(s, s.get(Channel, 4), VID)
    assert outside.exists()


def test_api_delete(env):
    engine, d, video = env
    _project(engine, video, "scripting")
    app = FastAPI()
    app.include_router(grow.router)

    def sess():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    c = TestClient(app)
    r = c.delete("/grow/4/lives", params={"video_id": VID})
    assert r.status_code == 409 and "em produção" in r.json()["detail"]
    with Session(engine) as s:
        j = s.exec(__import__("sqlmodel").select(Job)).first()
        j.state = "published"
        s.add(j)
        s.commit()
    r = c.delete("/grow/4/lives", params={"video_id": VID})
    assert r.status_code == 200 and r.json()["freed"] > 0 and r.json()["deleted"] == VID
    assert c.delete("/grow/4/lives", params={"video_id": VID}).status_code == 404
