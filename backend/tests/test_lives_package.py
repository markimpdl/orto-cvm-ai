"""A live → 1 'melhores momentos' long project + N Shorts, each Short pinned to one moment of the recording."""
import subprocess

import pytest
from huey import MemoryHuey
from sqlmodel import Session, SQLModel, create_engine, select

from cvm import gpu
from cvm.config import settings
from cvm.models import Asset, Channel, Project

LIVE = {"video_id": "a1", "title": "Live GTA 6 #1", "url": "https://www.youtube.com/watch?v=a1", "duration": 7200.0}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu, "lock", MemoryHuey("t").lock_task("gpu"))
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    import cvm.workers.tasks as tasks
    monkeypatch.setattr(tasks, "run_job", lambda job_id: None)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    c = Channel(id=4, name="Studio", platform="youtube", language="pt-BR", youtube_channel_id="UCabc")
    s.add(c)
    s.commit()
    video = tmp_path / "live_a1.mp4"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "4", "-shortest", str(video)], check=True)
    return s, c, video


def _fake_analysis(monkeypatch, moments):
    from cvm.visuals import highlights
    monkeypatch.setattr(highlights, "analyse", lambda paths, durs: [
        {"segments": [{"start": 0.0, "end": 4.0, "score": 1.0}], "whole": True, "moments": moments,
         "dropped": {"dull": 0, "repeat": 0}}])


MOMENTS = [{"start": 0.0, "end": 20.0, "score": 3.0}, {"start": 100.0, "end": 130.0, "score": 2.0},
           {"start": 300.0, "end": 340.0, "score": 1.0}]


def test_package_makes_long_and_shorts(env, monkeypatch):
    from cvm.lives import package
    s, c, video = env
    _fake_analysis(monkeypatch, MOMENTS)
    out = package.make_package(s, c, LIVE, video, n_shorts=3)
    long_p = s.get(Project, out["long"])
    assert long_p.style == "live" and long_p.platforms == ["youtube"] and long_p.media_cut == "highlights"
    assert long_p.media_mode == "mine"
    assert len(out["shorts"]) == 3
    for pid, m in zip(out["shorts"], MOMENTS):
        p = s.get(Project, pid)
        assert p.style == "live" and p.platforms == ["youtube_shorts"]
        a = s.exec(select(Asset).where(Asset.project_id == pid, Asset.kind == "user_media")).one()
        assert a.meta["segments"] == [{"start": m["start"], "end": m["end"]}]
        assert a.meta["segments_auto"] is False and a.meta["keep_audio"] is True
    la = s.exec(select(Asset).where(Asset.project_id == out["long"], Asset.kind == "user_media")).one()
    assert la.meta["keep_audio"] is True and la.meta["segments_auto"] is True


def test_package_fewer_shorts_when_quiet(env, monkeypatch):
    from cvm.lives import package
    s, c, video = env
    _fake_analysis(monkeypatch, [{"start": 10.0, "end": 25.0, "score": 1.0}])
    out = package.make_package(s, c, LIVE, video, n_shorts=3)
    assert len(out["shorts"]) == 1


def test_package_refused_for_paused_channel(env, monkeypatch):
    from cvm import guards
    from cvm.lives import package
    s, c, video = env
    c.status = "paused"
    s.add(c)
    s.commit()
    _fake_analysis(monkeypatch, MOMENTS)
    with pytest.raises(guards.ChannelClosed):
        package.make_package(s, c, LIVE, video)
    assert s.exec(select(Project)).all() == []


def test_other_creators_video_is_react_with_credit(env, monkeypatch):
    from cvm.lives import package
    s, c, video = env
    _fake_analysis(monkeypatch, MOMENTS[:1])
    out = package.make_package(s, c, LIVE, video, n_shorts=1,
                               third_party={"uploader": "Fulano", "url": "https://youtu.be/x", "channel_id": "UCx"})
    assert out["long"] is None  # long videos are the user's own work only
    short_p = s.get(Project, out["shorts"][0])
    assert short_p.style == "react" and short_p.title.startswith("🟡") and "Fulano" in short_p.topic
    la = s.exec(select(Asset).where(Asset.project_id == out["shorts"][0], Asset.kind == "user_media")).one()
    assert "Fulano" in la.source


def test_package_uses_viral_picks(env, monkeypatch):
    from cvm.lives import package
    s, c, video = env
    _fake_analysis(monkeypatch, MOMENTS)
    picks = [{"start": 100.0, "end": 130.0, "viral": 9, "hook": "ELE PULOU 😱", "why": "susto e reação"},
             {"start": 300.0, "end": 340.0, "viral": 7, "hook": "QUE ISSO", "why": "virada"}]
    out = package.make_package(s, c, LIVE, video, pick=lambda *a, **k: (picks, "viral"))
    assert out["method"] == "viral" and len(out["shorts"]) == 2
    p = s.get(Project, out["shorts"][0])
    assert "ELE PULOU 😱" in p.topic and "susto e reação" in p.topic


def test_package_falls_back_on_claude_limit(env, monkeypatch):
    from datetime import datetime

    from cvm import claude_limit
    from cvm.lives import package, viral
    s, c, video = env
    _fake_analysis(monkeypatch, MOMENTS)
    monkeypatch.setattr(viral, "score", lambda *a, **k: (_ for _ in ()).throw(claude_limit.ClaudeLimit(datetime.now(), "limite")))
    monkeypatch.setattr(viral, "transcribe_windows", lambda v, w, l, **k: w)
    out = package.make_package(s, c, LIVE, video)
    assert out["method"] == "volume" and len(out["shorts"]) == 3  # MOMENTS scores 3, 2, 1 ≥ VIRAL_MIN


def test_short_source_makes_only_shorts(env, monkeypatch):
    from cvm.lives import package
    s, c, video = env
    _fake_analysis(monkeypatch, MOMENTS[:1])
    out = package.make_package(s, c, {**LIVE, "duration": 35.0}, video, n_shorts=1)
    assert out["long"] is None and len(out["shorts"]) == 1


def test_source_language_drives_transcription(env, monkeypatch):
    from cvm.lives import package
    s, c, video = env
    _fake_analysis(monkeypatch, MOMENTS[:1])
    got = {}
    package.make_package(s, c, {**LIVE, "language": "en"}, video,
                         pick=lambda v, meta, title, own, language, **k: got.update(language=language) or ([], "viral"))
    assert got["language"] == "en-US"


def test_other_creators_long_video_gives_shorts_only(env, monkeypatch):
    """User's rule (2026-11-21): long videos are his own work only; another creator's video gives Shorts only."""
    from cvm.lives import package
    s, c, video = env
    _fake_analysis(monkeypatch, MOMENTS[:2])
    out = package.make_package(s, c, LIVE, video, n_shorts=2,
                               third_party={"uploader": "MrBeast", "url": "https://youtu.be/x", "channel_id": "UCx"})
    assert out["long"] is None and len(out["shorts"]) == 2


def test_package_shows_the_avatar_only_in_its_own_scenes(env, monkeypatch):
    """Faster and right for reacts: the presenter appears only in avatar scenes, never over the whole cut."""
    from cvm.lives import package
    s, c, video = env
    _fake_analysis(monkeypatch, MOMENTS[:1])
    out = package.make_package(s, c, LIVE, video, n_shorts=1)
    for pid in [out["long"], *out["shorts"]]:
        assert s.get(Project, pid).presenter == "scenes"


def test_foreign_language_source_is_explained_by_the_presenter(env, monkeypatch):
    """582 (2026-11-22): an English tutorial on the pt-BR channel must not play the creator's English voice; the
    presenter explains what it teaches over the footage, on screen the whole time."""
    from cvm.lives import package
    s, c, video = env
    _fake_analysis(monkeypatch, MOMENTS[:2])
    tp = {"uploader": "GrandGecko", "url": "https://youtu.be/x", "channel_id": "UCx"}
    out = package.make_package(s, c, {**LIVE, "language": "en"}, video, n_shorts=2, third_party=tp)
    for pid in out["shorts"]:
        a = s.exec(select(Asset).where(Asset.project_id == pid, Asset.kind == "user_media")).one()
        assert a.meta["keep_audio"] is False
        topic = s.get(Project, pid).topic
        assert "explica" in topic and "som original" not in topic


def test_same_language_source_keeps_its_voice(env, monkeypatch):
    from cvm.lives import package
    s, c, video = env
    _fake_analysis(monkeypatch, MOMENTS[:1])
    out = package.make_package(s, c, {**LIVE, "language": "pt"}, video, n_shorts=1,
                               third_party={"uploader": "Fulano", "url": "https://youtu.be/x", "channel_id": "UCx"})
    a = s.exec(select(Asset).where(Asset.project_id == out["shorts"][0], Asset.kind == "user_media")).one()
    assert a.meta["keep_audio"] is True
