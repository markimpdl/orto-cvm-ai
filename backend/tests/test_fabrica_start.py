"""/fabrica/novo v2 (plan 2026-11-24, phases 3 and 7): script draft, manual from–to ranges, YouTube links downloaded
by the worker before the job starts, and ElevenLabs music with ACE-Step as the fallback."""
import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from huey import MemoryHuey
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from cvm import gpu
from cvm.config import settings
from cvm.db import get_session
from cvm.models import Asset, Channel, Job, Project


def _video(path, seconds=6):
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(seconds), "-shortest", str(path)], check=True)
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    from cvm.api import media, projects
    from cvm.mcp_server import server
    import cvm.db
    import cvm.workers.tasks as tasks

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(gpu, "lock", MemoryHuey("t").lock_task("gpu"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(server, "engine", engine)
    monkeypatch.setattr(cvm.db, "engine", engine)
    calls = {"run_job": [], "links": []}
    monkeypatch.setattr(tasks, "run_job", lambda job_id: calls["run_job"].append(job_id))
    monkeypatch.setattr(tasks, "link_projects", lambda key: calls["links"].append(key))
    with Session(engine) as s:
        s.add(Channel(id=4, name="Studio", platform="youtube", language="pt-BR", youtube_channel_id="UCme"))
        s.commit()
    app = FastAPI()
    app.include_router(projects.router)
    app.include_router(media.router)

    def sess():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    return TestClient(app), engine, calls


BASE = {"title": "Golden Visa", "topic": "Golden Visa", "channel_id": 4, "languages": ["pt-BR"], "force_similar": True}


def test_draft_is_saved_and_get_project_returns_it(env):
    from cvm.mcp_server import server

    c, _, calls = env
    draft = "Gancho: quanto custa pôr painéis solares em casa?\nBloco 1: aluguel.\nBloco 2: escola."
    r = c.post("/projects", json={**BASE, "draft": f"  {draft}  ", "music_source": "none"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert r.json()["held"] is False and calls["run_job"]  # no files/links: the job starts at once
    data = server._project_data(pid)
    assert data["project"]["draft"] == draft and data["project"]["music_source"] == "none"
    assert c.post("/projects", json={**BASE, "music_source": "suno"}).status_code == 422


def test_manual_ranges_are_kept_and_shown(env, tmp_path, monkeypatch):
    from cvm.api.media import attach_file
    from cvm.mcp_server import server
    from cvm.visuals import highlights

    c, engine, _ = env
    pid = c.post("/projects", json={**BASE, "hold": True}).json()["id"]
    with Session(engine) as s:
        assert s.exec(select(Job).where(Job.project_id == pid)).first() is None  # held: waits for its files
        mid = attach_file(s, pid, _video(tmp_path / "clip.mp4"), "clip.mp4").id
    r = c.patch(f"/projects/{pid}/media/{mid}", json={"segments": [[4, 30], {"start": 0.5, "end": 2}]})
    assert r.status_code == 200, r.text
    assert r.json()["segments"] == [{"start": 0.5, "end": 2.0}, {"start": 4.0, "end": 6.0}]  # sorted, cut at the end
    assert r.json()["segments_auto"] is False
    assert c.patch(f"/projects/{pid}/media/{mid}", json={"segments": [[3, 3.2]]}).status_code == 422
    # project media_cut stays "full": the hand-picked ranges still limit what plays and what the scriptwriter sees
    m = next(x for x in server._project_data(pid)["user_media"] if x["media_id"] == mid)
    assert m["segments"][1] == {"start": 4.0, "end": 6.0} and m["by_hand"] is True
    with Session(engine) as s:
        p, a = s.get(Project, pid), s.get(Asset, mid)
        assert highlights.ranges_apply(a.meta, p) and highlights.segment_start(a.meta, 3.0) == 4.0
        p.media_cut = "highlights"  # a best-moments pass never overwrites them
        s.add(p)
        s.commit()
    monkeypatch.setattr(highlights, "analyse", lambda paths, durs: [
        {"segments": [{"start": 0.0, "end": 6.0}], "whole": True, "moments": [], "dropped": {}}])
    highlights.refresh_project(pid, True)
    with Session(engine) as s:
        assert s.get(Asset, mid).meta["segments"] == [{"start": 0.5, "end": 2.0}, {"start": 4.0, "end": 6.0}]
    assert c.post("/projects/start", json={"project_ids": [pid]}).json()["started"] == [pid]


LINK = {"url": "https://youtu.be/abcdefghijk", "cut": "ranges", "ranges": [[1, 4]], "keep_audio": True}


def test_link_is_attached_by_the_worker_before_the_job_starts(env, tmp_path, monkeypatch):
    from cvm import media_links

    c, engine, calls = env
    assert c.post("/projects", json={**BASE, "links": [{"url": "https://vimeo.com/1"}]}).status_code == 422
    r = c.post("/projects", json={**BASE, "links": [LINK], "hold": True})
    assert r.status_code == 200, r.text
    pid, key = r.json()["id"], r.json()["link_job"]["key"]
    assert r.json()["link_job"]["status"] == "held" and not calls["links"]  # nothing downloads in the API
    assert c.post("/projects/start", json={"project_ids": [pid]}).json() == {"started": [], "downloading": [pid]}
    assert calls["links"] == [key] and calls["run_job"] == []

    video = _video(tmp_path / "yt.mp4")
    info = {"video_id": "abcdefghijk", "title": "Outro canal", "url": "https://www.youtube.com/watch?v=abcdefghijk",
            "channel_id": "UCother", "uploader": "Fulano"}
    monkeypatch.setattr(media_links, "fetch", lambda link, ch: (info, video))
    seen = []

    def run_job(job_id):  # the material is already there when the job (and its script step) starts
        with Session(engine) as s:
            seen.append(s.exec(select(Asset).where(Asset.project_id == pid, Asset.kind == "user_media")).all())
    import cvm.workers.tasks as tasks
    monkeypatch.setattr(tasks, "run_job", run_job)
    with Session(engine) as s:
        media_links.run(s, key)
        assert media_links.entries(s)[0]["status"] == "ok"
    assert len(seen) == 1 and len(seen[0]) == 1
    a = seen[0][0]
    assert a.meta["segments"] == [{"start": 1.0, "end": 4.0}] and a.meta["segments_auto"] is False
    assert a.meta["keep_audio"] is True and a.meta["audio_under"] is False and "Fulano" in a.source


def test_failed_download_deletes_the_project(env, monkeypatch):
    from cvm import media_links

    c, engine, calls = env
    r = c.post("/projects", json={**BASE, "links": [{"url": "https://www.youtube.com/watch?v=abcdefghijk"}]})
    pid, key = r.json()["id"], r.json()["link_job"]["key"]

    def boom(link, ch):
        raise RuntimeError("não consegui baixar o vídeo: HTTP 403")
    monkeypatch.setattr(media_links, "fetch", boom)
    c.post("/projects/start", json={"project_ids": [pid]})
    with Session(engine) as s:
        media_links.run(s, key)
        assert s.get(Project, pid) is None and s.exec(select(Job)).all() == []
    status = c.get("/projects/links").json()[0]
    assert status["status"] == "error" and "403" in status["error"] and calls["run_job"] == []


# ---- music -------------------------------------------------------------------------------------------------

def _fake_eleven(monkeypatch, remaining_seq, music=b"ID3" + b"\x00" * 4000, fail=False):
    """Fake ElevenLabs HTTP: subscription answers from remaining_seq (one per call), /v1/music returns `music`."""
    from cvm.voice import eleven

    seq, hits = list(remaining_seq), []

    def call_once(path, body=None, method="GET"):
        hits.append(path)
        if path.startswith("/v1/user/subscription"):
            left = seq.pop(0) if len(seq) > 1 else seq[0]
            return {"character_limit": 100000, "character_count": 100000 - left}
        if path.startswith("/v1/music"):
            assert body["force_instrumental"] is True
            if fail:
                raise eleven.ElevenError("ElevenLabs HTTP 500: busy")
            return music
        raise AssertionError(path)
    monkeypatch.setattr(eleven, "available", lambda: True)
    monkeypatch.setattr(eleven, "_call_once", call_once)
    monkeypatch.setattr(settings, "elevenlabs_reserve", 1500)
    return hits


def _fake_acestep(monkeypatch, tmp_path):
    from cvm.render import music, music_gen

    monkeypatch.setattr(music.settings, "music_dir", tmp_path / "music")
    monkeypatch.setattr(music_gen, "ready", lambda: True)

    def grow(style, seed):
        folder = music_gen.library(style)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "x_1.wav").write_bytes(b"RIFF")
        (folder / "x_1.json").write_text(json.dumps({"source": "acestep", "license": "own"}))
        return [folder / "x_1.wav"]
    monkeypatch.setattr(music_gen, "grow", grow)


def test_eleven_music_falls_back_to_acestep_when_credits_are_low(tmp_path, monkeypatch):
    from cvm.render import music

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _fake_acestep(monkeypatch, tmp_path)
    hits = _fake_eleven(monkeypatch, [2000])  # 2000 left − 1500 reserve < one track
    p = SimpleNamespace(id=9, channel_id=None, style="explainer", music_source="elevenlabs")
    track = music.pick_music(p, {})
    assert track and track.parent.name == "explainer" and "acestep" in str(track)
    assert not any(h.startswith("/v1/music") for h in hits)  # never paid


def test_eleven_music_failed_call_falls_back(tmp_path, monkeypatch):
    from cvm.render import music

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _fake_acestep(monkeypatch, tmp_path)
    _fake_eleven(monkeypatch, [90000], fail=True)
    p = SimpleNamespace(id=9, channel_id=None, style="explainer", music_source="elevenlabs")
    assert "acestep" in str(music.pick_music(p, {}))


def test_eleven_music_made_once_per_project_and_rate_measured(env, tmp_path, monkeypatch):
    from cvm.render import music, music_eleven

    _, engine, _ = env
    hits = _fake_eleven(monkeypatch, [90000, 90000, 87000])  # estimate, before, after: the track cost 3000
    p = SimpleNamespace(id=11, channel_id=4, style="dark", music_source="elevenlabs")
    with Session(engine) as s:
        assert music_eleven.estimate(s)["enough"] is True
        track = music.pick_music(p, {"music": False}, s)  # chosen for this video: the channel switch doesn't matter
        assert track == music_eleven.cached(p) and track.exists() and music.credit_for(track) is None
        assert music_eleven.rate(s) == 3000 / (music_eleven.TRACK_S / 60)
        n = len(hits)
        assert music.pick_music(p, {}, s) == track and len(hits) == n  # cached: no second call


def test_music_none_means_no_music_even_with_a_channel_folder(tmp_path, monkeypatch):
    from cvm.render import music

    monkeypatch.setattr(music.settings, "music_dir", tmp_path)
    (tmp_path / "4").mkdir()
    (tmp_path / "4" / "song.mp3").write_bytes(b"ID3")
    assert music.pick_music(SimpleNamespace(id=1, channel_id=4, style="dark", music_source="none"), {"music": True}) is None
    assert music.pick_music(SimpleNamespace(id=1, channel_id=4, style="dark", music_source=None), {}).name == "song.mp3"
