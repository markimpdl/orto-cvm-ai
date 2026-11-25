"""Cut editor: the streamer's camera for the 3-panel Shorts (owner, 2026-11-25) — found at analysis, edited in the
editor, written on every Short's recording row; and "Refazer com o layout novo" (cvm/lives/relayout.py)."""
import pytest
from sqlmodel import select
from test_cut_review import _analyse, env  # noqa: F401 — the shared fixture

from cvm.lives import cuts, facecam, relayout
from cvm.models import Asset, Job, Project

BOX = {"x": 0.7, "y": 0.0, "w": 0.3, "h": 0.3}


def _produce_one(s, r):
    cuts.update_cuts(s, r, [{**r.cuts[0], "status": "approved"}])
    cuts.mark_producing(s, r)
    return cuts.produce(s, r)


def _row(s, pid):
    return s.exec(select(Asset).where(Asset.project_id == pid, Asset.kind == "user_media")).one()


def test_analysis_detects_the_camera_and_produce_puts_it_on_the_shorts(env, monkeypatch):  # noqa: F811
    s, c, video, _ = env
    monkeypatch.setattr(facecam, "detect", lambda v, d: {**BOX, "auto": True})
    r = _analyse(env, options={"make_long": False})
    assert r.options["facecam"]["auto"] is True and r.options["facecam_order"] == "youtuber_top"
    cuts.set_layout(s, r, {"order": "header_top"})
    out = _produce_one(s, r)
    a = _row(s, out["shorts"][0])
    assert a.meta["facecam"] == BOX and a.meta["facecam_order"] == "header_top"


def test_own_live_uses_the_channel_camera_and_an_edited_box_wins(env, monkeypatch):  # noqa: F811
    s, c, video, _ = env
    monkeypatch.setattr(facecam, "detect", lambda v, d: pytest.fail("the channel's box is already known"))
    c.prefs = {"facecam": BOX}
    s.add(c)
    s.commit()
    r = _analyse(env)
    assert r.options["facecam"]["from"] == "channel"
    cuts.set_layout(s, r, {"facecam": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}})
    assert r.options["facecam"] == {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "auto": False}
    assert cuts.find_layout(c, video, 4, True, current=r.options)["facecam"]["x"] == 0.1  # a re-analysis keeps it
    cuts.set_layout(s, r, {"enabled": False})
    assert cuts.layout_of(r) == {}
    cuts.set_layout(s, r, {"enabled": True})
    assert cuts.layout_of(r)["facecam"]["x"] == 0.1
    with pytest.raises(ValueError):
        cuts.set_layout(s, r, {"order": "sideways"})
    with pytest.raises(ValueError, match="facecam"):
        cuts.set_layout(s, r, {"facecam": {"x": 0.9, "y": 0, "w": 0.5, "h": 0.2}})
    cuts.set_layout(s, r, {"facecam": None})
    assert cuts.layout_of(r) == {}


def test_no_camera_found_keeps_the_editorial_layout(env, monkeypatch):  # noqa: F811
    s, c, video, _ = env
    monkeypatch.setattr(facecam, "detect", lambda v, d: None)
    r = _analyse(env, options={"make_long": False})
    assert r.options["facecam"] is None and cuts.layout_of(r) == {}
    assert "facecam" not in _row(s, _produce_one(s, r)["shorts"][0]).meta


def test_relayout_writes_the_box_and_recomposes(env, monkeypatch):  # noqa: F811
    s, c, video, started = env
    monkeypatch.setattr(facecam, "detect", lambda v, d: None)
    r = _analyse(env, options={"make_long": False})
    pid = _produce_one(s, r)["shorts"][0]
    p = s.get(Project, pid)
    with pytest.raises(relayout.NotRedoable, match="marque"):  # no box anywhere yet
        relayout.redo(s, p)
    cuts.set_layout(s, r, {"facecam": BOX, "order": "header_top"})  # drawn in the editor after production
    job = s.exec(select(Job).where(Job.project_id == pid)).one()
    job.state, job.progress = "awaiting_publish_review", {}
    s.add(job)
    s.commit()
    monkeypatch.setattr("cvm.voice.narrate.narration_asset", lambda *a: object())
    started.clear()
    out = relayout.redo(s, p)
    assert out["state"] == "composing" and out["order"] == "header_top" and started == [job.id]
    assert _row(s, pid).meta["facecam"] == BOX and job.history[-1]["to"] == "composing"
    out = relayout.redo(s, p, facecam={"x": 0, "y": 0, "w": 0.25, "h": 0.25}, order="youtuber_top")
    assert _row(s, pid).meta["facecam"]["w"] == 0.25 and _row(s, pid).meta["facecam_order"] == "youtuber_top"


def test_relayout_refuses_published_and_avatar_shorts(env, monkeypatch):  # noqa: F811
    s, c, video, _ = env
    monkeypatch.setattr(facecam, "detect", lambda v, d: {**BOX, "auto": True})
    r = _analyse(env, options={"make_long": False})
    pid = _produce_one(s, r)["shorts"][0]
    p = s.get(Project, pid)
    job = s.exec(select(Job).where(Job.project_id == pid)).one()
    job.state = "published"
    s.add(job)
    s.commit()
    with pytest.raises(relayout.NotRedoable, match="publicado"):
        relayout.redo(s, p)
    job.state, p.presenter = "awaiting_publish_review", "scenes"
    s.add(job)
    s.add(p)
    s.commit()
    with pytest.raises(relayout.NotRedoable, match="sem avatar"):
        relayout.redo(s, p)
