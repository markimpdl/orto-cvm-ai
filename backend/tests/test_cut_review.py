"""Cut editor backend (spec 2026-11-25 editor de cortes, Parte 2): analyse → review → produce."""
import subprocess

import pytest
from huey import MemoryHuey
from sqlmodel import Session, SQLModel, create_engine, select

from cvm import gpu
from cvm.config import settings
from cvm.lives import cuts, runs
from cvm.models import Asset, Channel, CutReview, Job, Project, Script, Setting

LIVE = {"video_id": "AAAAAAAAAA1", "title": "Live GTA 6 #1", "url": "https://www.youtube.com/watch?v=AAAAAAAAAA1",
        "duration": 4.0, "heatmap": [{"start": 0.0, "end": 2.0, "value": 1.0}, {"start": 2.0, "end": 4.0, "value": 0.1}]}
PROPS = [{"start": 0.5, "end": 3.0, "score": 8.4, "viral": 8, "hook": "OLHA ISSO", "why": "susto", "rank": 1,
          "suggested": True, "scores": {"hook": 9}, "tags": "volume↑"},
         {"start": 1.0, "end": 2.0, "score": 4.0, "viral": 4, "hook": "", "why": "calmo", "rank": 2, "suggested": False}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu, "lock", MemoryHuey("t").lock_task("gpu"))
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    import cvm.workers.tasks as tasks
    started = []
    monkeypatch.setattr(tasks, "run_job", lambda job_id: started.append(job_id))
    monkeypatch.setattr("cvm.lives.signals.comment_times", lambda vid, **k: [])
    from cvm.visuals import highlights
    monkeypatch.setattr(highlights, "analyse", lambda paths, durs: [
        {"segments": [{"start": 0.0, "end": 4.0, "score": 1.0}], "whole": True,
         "moments": [{"start": 0.0, "end": 3.0, "score": 2.0}], "dropped": {"dull": 0, "repeat": 0},
         "tracks": {"loudness": [-20.0, -10.0, -30.0, -25.0], "motion": [0.1, 0.5, 0.1, 0.1], "action": [0, 1, 0, 0]}}])
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    c = Channel(id=4, name="Studio", platform="youtube", language="pt-BR", youtube_channel_id="UCabc")
    s.add(c)
    s.commit()
    video = tmp_path / "lives" / "4" / "live_AAAAAAAAAA1.mp4"
    video.parent.mkdir(parents=True)
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "4", "-shortest", str(video)], check=True)
    return s, c, video, started


def _detect(seen=None):
    def detect(video, meta, title, own, language, duration, **k):
        if seen is not None:
            seen.update(k, meta=meta)
        return [dict(p) for p in PROPS], "viral"
    return detect


def _analyse(env, **kw):
    s, c, video, _ = env
    return cuts.analyse(s, c, LIVE, video, item_key=LIVE["video_id"], detect=kw.pop("detect", _detect()), **kw)


def test_analyse_stores_cuts_lanes_and_a_proxy(env):
    s, c, video, _ = env
    seen = {}
    r = _analyse(env, detect=_detect(seen))
    assert r.status == "review" and r.method == "viral" and r.duration == pytest.approx(4.0, abs=0.1)
    assert [x["status"] for x in r.cuts] == ["suggested", "proposed"]
    assert r.cuts[0]["hook"] == "OLHA ISSO" and r.cuts[0]["commentary"] is False  # clip-only by default
    assert r.proxy_path and r.proxy_path.endswith("_proxy480.mp4")
    d = cuts.detail(r)
    assert "replay" in d["tracks"] and "chat" not in d["tracks"]  # absent lanes are left out
    assert len(d["waveform"]["peaks"]) >= 38 and d["has_proxy"]
    assert seen["tracks"]["loudness"] and seen["tracks"]["replay"][0] == 1.0


def test_edits_keep_ai_fields_and_validate(env):
    s, c, video, _ = env
    r = _analyse(env)
    edited = [{**r.cuts[0], "start": 0.2, "end": 3.5, "status": "approved", "comment": "começa no grito", "hook": "x"},
              {"start": 1.0, "end": 2.5}]  # a new cut drawn by the owner
    cuts.update_cuts(s, r, edited)
    a, b = r.cuts
    assert (a["start"], a["end"], a["status"], a["comment"], a["hook"]) == (0.2, 3.5, "approved", "começa no grito", "OLHA ISSO")
    assert b["origin"] == "owner" and b["status"] == "approved" and b["id"].startswith("u")
    for bad in ([{"start": 3.0, "end": 2.0}], [{"start": 0, "end": 99}], [{"start": 0, "end": 2, "status": "x"}]):
        with pytest.raises(ValueError):
            cuts.update_cuts(s, r, bad)
    c2 = cuts.patch_cut(s, r, a["id"], {"status": "discarded", "commentary": True})
    assert c2["status"] == "discarded" and c2["commentary"] is True


def test_warnings_for_length_and_reused_content(env):
    cut = {"start": 0.0, "end": 5.0, "status": "approved", "commentary": False}
    assert any("curto" in w for w in cuts.warnings(cut))
    assert any("reutilizado" in w for w in cuts.warnings(cut, own=False))
    assert not any("reutilizado" in w for w in cuts.warnings({**cut, "commentary": True}, own=False))


def test_produce_makes_shorts_with_the_owner_comment_and_learns(env):
    s, c, video, started = env
    runs.start_item(s, c, LIVE["video_id"], url=LIVE["url"], title=LIVE["title"])
    r = _analyse(env)
    cuts.update_cuts(s, r, [{**r.cuts[0], "status": "approved", "comment": "mostre a reação inteira"},
                            {**r.cuts[1], "status": "discarded"}])
    cuts.mark_producing(s, r, make_long=False)
    out = cuts.produce(s, r)
    assert r.status == "done" and len(out["shorts"]) == 1 and out["long"] is None
    p = s.get(Project, out["shorts"][0])
    assert "mostre a reação inteira" in p.topic and "SEM COMENTÁRIO" in p.topic and p.presenter == "editorial"
    a = s.exec(select(Asset).where(Asset.project_id == p.id, Asset.kind == "user_media")).one()
    assert a.meta["segments"] == [{"start": 0.5, "end": 3.0}] and a.meta["segments_auto"] is False
    taste = cuts.taste(s, c.id)
    assert any("APROVOU" in t and "mostre a reação inteira" in t for t in taste)
    assert any("DESCARTOU" in t for t in taste)
    item = runs.status(s, c)["items"][0]
    assert item["status"] == "ok" and item["package"]["shorts"] == out["shorts"]
    seen = {}
    _analyse(env, detect=_detect(seen))  # the next analysis of the channel gets the owner's taste
    assert any("APROVOU" in t for t in seen["taste"])


def test_produce_needs_an_approved_cut(env):
    s, c, video, _ = env
    r = _analyse(env, options={"make_long": False})
    with pytest.raises(ValueError):
        cuts.mark_producing(s, r)


def test_finish_waits_for_review_unless_the_channel_approves_cuts_itself(env, monkeypatch):
    s, c, video, _ = env
    monkeypatch.setattr(cuts, "analyse", lambda *a, **k: CutReview(id=7, status="review"))
    item = runs.start_item(s, c, LIVE["video_id"], url=LIVE["url"], title=LIVE["title"])
    item = runs.finish(s, c, item, LIVE, video)
    assert item["status"] == "review" and item["review"] == 7
    with pytest.raises(runs.AlreadyDone):
        runs.start_item(s, c, LIVE["video_id"])
    c.prefs = {"auto_approve_cuts": True}
    s.add(c)
    s.commit()
    made = []
    monkeypatch.setattr("cvm.lives.package.make_package", lambda session, channel, live, video, third_party=None, examples=None, min_shorts=0, taste=None:
                        made.append({"taste": taste}) or {"long": None, "shorts": []})
    item = runs.finish(s, c, {**item, "video_id": "BBBBBBBBBB2"}, LIVE, video)
    assert item["status"] == "ok" and made and "taste" in made[0]


def test_segments_review_sets_the_project_ranges_then_starts_it(env):
    s, c, video, started = env
    p = Project(title="Melhores", channel_id=c.id, style="live", media_cut="highlights", topic="t")
    s.add(p)
    s.commit()
    s.add(Asset(project_id=p.id, kind="user_media", path=str(video), meta={"kind": "video", "duration": 4.0}))
    s.commit()
    r = _analyse(env, kind="segments", source="fabrica", options={"project_ids": [p.id], "make_long": False})
    cuts.update_cuts(s, r, [{**r.cuts[0], "status": "approved", "comment": "corte seco"}])
    cuts.mark_producing(s, r)
    cuts.produce(s, r)
    a = s.exec(select(Asset).where(Asset.project_id == p.id)).one()
    assert a.meta["segments"] == [{"start": 0.5, "end": 3.0}] and a.meta["segments_auto"] is False
    assert "corte seco" in s.get(Project, p.id).topic
    assert s.exec(select(Job).where(Job.project_id == p.id)).first() and started


def test_media_links_highlights_link_goes_to_the_editor(env, monkeypatch):
    from cvm import media_links
    s, c, video, started = env
    p = Project(title="Melhores", channel_id=c.id, style="live", media_cut="highlights", topic="t")
    s.add(p)
    s.commit()
    link = {"url": LIVE["url"], "original_url": LIVE["url"], "platform": "youtube", "video_id": LIVE["video_id"],
            "cut": "highlights", "keep_audio": True}
    entry = media_links.hold(s, [p], [link])
    monkeypatch.setattr(media_links, "fetch", lambda link, ch: ({**LIVE, "channel_id": "UCabc", "platform": "youtube"}, video))
    monkeypatch.setattr(media_links, "attach", lambda *a, **k: None)
    monkeypatch.setattr(cuts, "analyse", lambda *a, **k: CutReview(id=9, status="review"))
    media_links.run(s, entry["key"])
    e = media_links.entries(s)[0]
    assert e["status"] == "review" and e["review"] == 9 and not started


def test_clip_only_conversion_rewrites_the_script_and_resets_the_job(env):
    from cvm.lives import clip_only
    s, c, video, started = env
    p = Project(title="Short 1", channel_id=c.id, style="react", presenter="scenes", topic="comente", languages=["pt-BR"])
    s.add(p)
    s.commit()
    a = Asset(project_id=p.id, kind="user_media", path=str(video),
              meta={"kind": "video", "segments": [{"start": 100.0, "end": 130.0}], "keep_audio": False, "audio_under": True})
    s.add(a)
    s.add(Script(project_id=p.id, language="pt-BR", version=2, approved=True,
                 content={"title": "x", "scenes": [{"index": 0, "narration": "olha", "visual": {"type": "avatar_full"}}],
                          "cta_points": [{"at": 0.4}], "packaging": {"title": "T"}}))
    job = Job(project_id=p.id, state="composing")
    s.add(job)
    s.commit()
    out = clip_only.convert(s, p)
    assert out["state"] == "voicing" and out["scripts"] == ["pt-BR"] and started == [job.id]
    sc = s.exec(select(Script).where(Script.project_id == p.id).order_by(Script.version.desc())).first()
    assert sc.version == 3 and sc.content["packaging"] == {"title": "T"} and "cta_points" not in sc.content
    assert all(x["narration"] == "" and x["visual"]["keep_audio"] for x in sc.content["scenes"])
    assert sc.content["scenes"][0]["visual"]["media_start"] == 100.0
    assert sum(x["target_seconds"] for x in sc.content["scenes"]) == pytest.approx(30.0)
    assert s.get(Project, p.id).presenter == "editorial" and "SEM COMENTÁRIO" in s.get(Project, p.id).topic
    assert s.get(Asset, a.id).meta["keep_audio"] is True and s.get(Asset, a.id).meta["audio_under"] is False
    job.state = "published"
    s.add(job)
    s.commit()
    with pytest.raises(clip_only.NotConvertible):
        clip_only.convert(s, p)


def test_api_review_range_video_edit_and_produce(env, monkeypatch):
    from fastapi.testclient import TestClient

    from cvm.api import cuts as api
    from cvm.db import get_session
    from cvm.main import app
    s, c, video, _ = env
    r = _analyse(env)
    app.dependency_overrides[get_session] = lambda: s
    queued = []
    monkeypatch.setattr("cvm.workers.tasks.produce_cuts", lambda rid: queued.append(rid))
    try:
        cl = TestClient(app)
        assert cl.get("/cuts?status=review").json()["items"][0]["id"] == r.id
        d = cl.get(f"/cuts/{r.id}").json()
        assert d["cuts"][0]["warnings"] and d["transcript"] == [] and "replay" in d["tracks"]
        v = cl.get(f"/cuts/{r.id}/video", headers={"Range": "bytes=0-99"})
        assert v.status_code == 206 and len(v.content) == 100
        bad = cl.put(f"/cuts/{r.id}/cuts", json={"cuts": [{"start": 3, "end": 1}]})
        assert bad.status_code == 422
        ok = cl.patch(f"/cuts/{r.id}/cuts/{d['cuts'][0]['id']}", json={"status": "approved"})
        assert ok.json()["status"] == "approved"
        assert cl.post(f"/cuts/{r.id}/produce", json={"make_long": False}).json()["status"] == "producing"
        assert queued == [r.id]
        st = cl.put(f"/cuts/channels/{c.id}/settings", json={"shorts_commentary": True}).json()
        assert st == {"auto_approve_cuts": False, "shorts_commentary": True}
    finally:
        app.dependency_overrides.clear()
    assert api.router.prefix == "/cuts"
    assert s.get(Setting, f"cut_taste:{c.id}") is None  # nothing learned before producing
