"""Final-review findings on the Crescer/lives work (2026-11-21): each test reproduces one finding."""
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from huey import MemoryHuey
from sqlmodel import Session, SQLModel, create_engine, select

from cvm import gpu
from cvm.config import settings
from cvm.models import Asset, Channel, Project, Setting, now

LIVE = {"video_id": "a1", "title": "Live #1", "url": "https://www.youtube.com/watch?v=a1", "duration": 4.0}


def _db():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _channel(s, status="active"):
    c = Channel(id=4, name="Studio", platform="youtube", language="pt-BR", youtube_channel_id="UCabc", status=status,
                prefs={"lives": {"auto": True}})
    s.add(c)
    s.commit()
    return c


def _video(path: Path, seconds: int = 4) -> Path:
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(seconds), "-shortest", str(path)], check=True)
    return path






# 3 — a failing before_job leaves no project behind; a failing analysis creates nothing
def test_before_job_failure_rolls_back_the_project(monkeypatch):
    from cvm import suggest
    monkeypatch.setattr(gpu, "lock", MemoryHuey("t").lock_task("gpu"))
    import cvm.workers.tasks as tasks
    monkeypatch.setattr(tasks, "run_job", lambda job_id: None)
    s = _db()
    _channel(s)

    def boom(project):
        s.add(Asset(project_id=project.id, kind="user_media", path="/x.mp4", meta={}))
        s.commit()
        raise RuntimeError("ffmpeg falhou")
    with pytest.raises(RuntimeError):
        suggest.new_project(s, {"title": "t", "topic": "t", "channel_id": 4, "force_similar": True}, before_job=boom)
    assert s.exec(select(Project)).all() == [] and s.exec(select(Asset)).all() == []


def test_package_analysis_failure_creates_nothing(tmp_path, monkeypatch):
    from cvm.lives import package
    from cvm.visuals import highlights
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(gpu, "lock", MemoryHuey("t").lock_task("gpu"))
    import cvm.workers.tasks as tasks
    monkeypatch.setattr(tasks, "run_job", lambda job_id: None)
    monkeypatch.setattr(highlights, "analyse", lambda *a: (_ for _ in ()).throw(RuntimeError("decode error")))
    s = _db()
    c = _channel(s)
    with pytest.raises(RuntimeError):
        package.make_package(s, c, LIVE, _video(tmp_path / "v.mp4"))
    assert s.exec(select(Project)).all() == []


def test_empty_analysis_fails_the_package(tmp_path, monkeypatch):
    from cvm.lives import package
    from cvm.visuals import highlights
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(highlights, "analyse", lambda *a: [{"segments": [], "whole": False, "moments": [],
                                                            "dropped": {"dull": 4, "repeat": 0}}])
    s = _db()
    c = _channel(s)
    with pytest.raises(RuntimeError, match="momento"):
        package.make_package(s, c, LIVE, _video(tmp_path / "v.mp4"))
    assert s.exec(select(Project)).all() == []


# 4 — removing a Short's clip never deletes the recording other projects still use
def test_delete_media_keeps_shared_file(tmp_path, monkeypatch):
    from cvm.api import media
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    s = _db()
    f = tmp_path / "live.mp4"
    f.write_bytes(b"x")
    p1, p2 = Project(title="a", topic="a"), Project(title="b", topic="b")
    s.add(p1)
    s.add(p2)
    s.commit()
    a1 = Asset(project_id=p1.id, kind="user_media", path=str(f), meta={"kind": "video"})
    a2 = Asset(project_id=p2.id, kind="user_media", path=str(f), meta={"kind": "video"})
    s.add(a1)
    s.add(a2)
    s.commit()
    media.delete_media(p2.id, a2.id, session=s)
    assert f.exists()


# 5 — an upload is packaged in the background and shows in the lives list
def test_upload_runs_in_background_and_is_listed(tmp_path, monkeypatch):
    import io

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlmodel.pool import StaticPool

    from cvm.api import grow
    from cvm.db import get_session
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        _channel(s)
    app = FastAPI()
    app.include_router(grow.router)

    def sess():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    started = []
    monkeypatch.setattr(grow, "_in_background", lambda fn, *a: started.append(a))
    r = TestClient(app).post("/grow/4/lives/upload", files={"file": ("a.mp4", io.BytesIO(b"x"), "video/mp4")}, data={"title": "Hoje"})
    assert r.status_code == 200 and r.json()["started"] is True
    channel_id, live, video = started[0]
    assert live["title"] == "Hoje" and Path(video).exists()


# 6 — the long video's scriptwriter sees the candidate moments
def test_get_project_lists_moments_for_highlights():
    from cvm.mcp_server import server
    meta = {"kind": "video", "segments": [{"start": 0, "end": 100}], "moments": [{"start": 10, "end": 40, "score": 2}]}
    out = server._media_ranges(meta, "highlights")
    assert out["segments"] == meta["segments"] and out["moments"] == meta["moments"]
    assert server._media_ranges(meta, "full") == {}


# 7 — a Short's original sound (and so its scene) stops at the end of its moment
def test_segment_end():
    from cvm.visuals.highlights import segment_end
    meta = {"segments": [{"start": 10.0, "end": 30.0}]}
    assert segment_end(meta, 12.0) == 30.0
    assert segment_end({"segments": []}, 12.0) is None
