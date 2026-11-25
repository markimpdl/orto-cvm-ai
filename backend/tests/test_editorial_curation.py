"""Curation checks for the news-desk long format (cvm/curation.py::check_editorial)."""
import json

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from cvm import curation, paths
from cvm.models import Project


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "project_dir", lambda pid, lang=None: tmp_path / str(pid) / (lang or ""))
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        yield s


def _project(session, **kw):
    p = Project(title="t", topic="t", languages=["pt-BR"], style="explainer", platforms=["youtube"],
                presenter="editorial", **kw)
    session.add(p)
    session.commit()
    return p


def _plan(tmp_path, p, scenes):
    d = tmp_path / str(p.id) / "pt-BR" / "editorial"
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps(scenes))


def _media(i, **kw):
    m = {"kind": "video", "file": f"/x/c{i}.mp4", "start": 0, "window_end": 3, "credit": "Canal X", "source": "u",
         "own": False, "judged": True}
    m.update(kw)
    return {"tipo": "video", "start": i * 3.0, "end": i * 3.0 + 3.0, "media": m}


def _run(session, p):
    return {c["name"]: c for c in curation.check_editorial(session, p, {}, [])}


def test_not_editorial_returns_nothing(session):
    p = Project(title="t", topic="t", languages=["pt-BR"], style="explainer", platforms=["youtube"], presenter="scenes")
    session.add(p)
    session.commit()
    assert curation.check_editorial(session, p, {}, []) == []


def test_credit_missing_is_hard(session, tmp_path):
    p = _project(session)
    _plan(tmp_path, p, [_media(0), _media(1, credit=None)])
    c = _run(session, p)["editorial_credit"]
    assert not c["ok"] and c["fix_state"] == "sourcing_visuals"


def test_own_media_needs_no_credit(session, tmp_path):
    p = _project(session)
    _plan(tmp_path, p, [_media(0, credit=None, own=True)])
    assert _run(session, p)["editorial_credit"]["ok"]


def test_proof_without_boxes_is_hard(session, tmp_path):
    p = _project(session)
    _plan(tmp_path, p, [{"tipo": "prova", "start": 0, "end": 3, "img": "a.png", "boxes": []}])
    c = _run(session, p)["editorial_proof"]
    assert not c["ok"] and c["fix_state"] == "sourcing_visuals"


def test_proof_with_boxes_passes(session, tmp_path):
    p = _project(session)
    _plan(tmp_path, p, [{"tipo": "prova", "start": 0, "end": 3, "img": "a.png", "boxes": [[0, 0, 1, 1]]}])
    assert _run(session, p)["editorial_proof"]["ok"]


def test_pace_long_scene_and_median(session, tmp_path):
    p = _project(session)
    scenes = [_media(0), {**_media(1), "start": 3, "end": 12}]
    _plan(tmp_path, p, scenes)
    c = _run(session, p)["editorial_pace"]
    assert not c["ok"] and c["fix_state"] == "scripting"
    # a long end screen is not counted
    _plan_ok = [_media(0), {"tipo": "fim", "start": 3, "end": 30, "end_screen": True}]
    (tmp_path / str(p.id) / "pt-BR" / "editorial" / "plan.json").write_text(json.dumps(_plan_ok))
    assert _run(session, p)["editorial_pace"]["ok"]


def test_pace_median_too_high(session, tmp_path):
    p = _project(session)
    _plan(tmp_path, p, [{**_media(i), "start": i * 6, "end": i * 6 + 6} for i in range(3)])
    assert not _run(session, p)["editorial_pace"]["ok"]


def test_ai_asset_in_video_scene_is_hard(session, tmp_path):
    p = _project(session)
    _plan(tmp_path, p, [_media(0, source="flux-schnell (local)")])
    c = _run(session, p)["editorial_ai"]
    assert not c["ok"] and c["fix_state"] == "sourcing_visuals"


def test_many_fallback_cards_is_soft(session, tmp_path):
    p = _project(session)
    cards = [{"tipo": "impacto", "tipo_pedido": "video", "fallback": "sem material", "start": i * 3, "end": i * 3 + 3}
             for i in range(2)]
    _plan(tmp_path, p, [_media(5)] + cards)
    c = _run(session, p)["editorial_cards"]
    assert not c["ok"] and c.get("soft") and "fix_state" not in c
    assert "pouco material real encontrado" in c["detail"]


def _convert(plan):
    """Bake-off plan (`src`/`de`, prova `img`) -> the shape `visuals.editorial.source` writes."""
    out = []
    for s in plan:
        s = dict(s)
        if s["tipo"] == "video":
            s["media"] = {"kind": "video", "file": f"/m/{s.pop('src')}.mp4", "start": s.pop("de"), "window_end": None,
                          "credit": "Canal", "source": "https://youtube.com/x", "license": None, "own": False,
                          "judged": True}
        elif s["tipo"] == "prova":
            s["img"] = f"provas/{s['img']}"
            s["size"] = [1920, 1080]
            s["boxes"] = [[0.1, 0.1, 0.5, 0.1]]
        out.append(s)
    return out


def test_approved_reference_passes_every_check(session, tmp_path, editorial_set):
    p = _project(session)
    _plan(tmp_path, p, _convert(editorial_set.plan))
    got = _run(session, p)
    assert {"editorial_credit", "editorial_proof", "editorial_pace", "editorial_ai", "editorial_cards"} <= set(got)
    assert all(c["ok"] for c in got.values()), {k: c["detail"] for k, c in got.items() if not c["ok"]}


def _stale_ai_visual(session, p):
    from cvm.models import Asset

    session.add(Asset(project_id=p.id, kind="visual", scene=1, source="ai:zimage", path="/x/old.png",
                      meta={"still": True, "judge": {"ok": False, "problems": ["mão torta"]}}))
    session.commit()


def test_stale_classic_assets_ignored_on_editorial_only_project(session, tmp_path, monkeypatch):
    """Review finding 5: old classic visual rows (from an earlier classic render) never send an editorial-only
    video back; with a Short beside it (classic set in use) they are still checked."""
    monkeypatch.setattr(curation, "check_content", lambda *a, **k: curation._check("content", True, "ok"))
    p = _project(session)
    _plan(tmp_path, p, [_media(i) for i in range(4)])
    _stale_ai_visual(session, p)
    names = {c["name"] for c in curation.run_checks(session, p)}
    assert not names & {"visuals", "real_footage", "flags", "clips"}
    p.platforms = ["youtube", "youtube_shorts"]
    session.add(p)
    session.commit()
    checks = {c["name"]: c for c in curation.run_checks(session, p)}
    assert "visuals" in checks and not checks["visuals"]["ok"]
