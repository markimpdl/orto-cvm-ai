"""Automatic validation step "validating" (cvm/curation.py): routing, attempt cap, Claude limit, switch."""

from datetime import UTC, datetime, timedelta

import pytest
from huey import MemoryHuey
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from cvm import claude_limit, curation, db, engines, gpu, pipeline
from cvm.models import Asset, Job, Project, Render, Script
from cvm.scripting import claude_runner, codex_runner
from cvm.workers import tasks


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(gpu, "lock", MemoryHuey("test-curation").lock_task("gpu"))
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    monkeypatch.setattr(tasks, "engine", e)
    monkeypatch.setattr(db, "engine", e)
    monkeypatch.setattr(engines, "auto_run", lambda: True)
    return e


LONG = {"hook": "h", "promise": "p", "intro_end": 1, "blocks": [{"title": "b", "scenes": [2]}],
        "cta_points": [{"scene": 2, "at": "mid"}, {"scene": 2, "at": "end"}], "sources": ["https://example.org/a"],
        "scenes": [{"index": 1, "narration": "Olá.", "visual": {"type": "broll"}},
                   {"index": 2, "narration": "Fato um.", "visual": {"type": "ai_image", "image_prompt": "a desert"}}]}


def _fake_claude(answer='{"ok": true, "problems": []}'):
    calls = []

    def run(prompt, **k):
        calls.append(k)
        return {"result": answer}
    run.calls = calls
    return run


def _make(engine, *, content=None, profile="youtube", narration_qa=None, visual_meta=None, visual_source="flux-schnell (local)",
          render_meta=None, state="validating", history=None):
    with Session(engine) as s:
        p = Project(title="t", topic="t", languages=["pt-BR"])
        s.add(p)
        s.commit()
        s.add(Script(project_id=p.id, language="pt-BR", version=1, content=content or LONG))
        s.add(Asset(project_id=p.id, kind="narration", language="pt-BR", path="n.wav",
                    meta={"script_version": 1, "qa": narration_qa or {"checked": True, "flagged": [], "sentences": 2}}))
        s.add(Asset(project_id=p.id, kind="visual", scene=2, path="v.png", source=visual_source,
                    meta={"kind": "image", "type": "ai_image", **({"judge": visual_meta} if visual_meta else
                                                                   {"judge": {"ok": True, "score": 9}})}))
        meta = {"analysis": {"black_s": 0, "frozen": [], "silences": [], "lufs": -14.0}}
        if profile == "youtube":
            meta |= {"end_screen_s": 20, "end_screen_from": 100.0}
        s.add(Render(project_id=p.id, language="pt-BR", profile=profile, path="r.mp4", meta={**meta, **(render_meta or {})}))
        j = Job(project_id=p.id, state=state, history=history or [])
        s.add(j)
        s.commit()
        return j.id


def _validate(engine, job_id):
    with Session(engine) as s:
        job = s.get(Job, job_id)
        nxt = curation.validate(job, s)
        s.refresh(job)
        return nxt, job, curation.latest_report(s, job.project_id)


def test_clean_video_goes_on_to_seo_with_a_report(engine, monkeypatch):
    fake = _fake_claude()
    monkeypatch.setattr(claude_runner, "run_claude", fake)
    nxt, job, rep = _validate(engine, _make(engine))
    assert nxt is None and job.state == "seo"
    assert all(c["ok"] for c in rep["checks"]), rep["checks"]
    assert {c["name"] for c in rep["checks"]} >= {"voice", "visuals", "render", "end_screen", "structure", "content"}
    assert job.history[-1]["note"].startswith("Conferido pela IA")
    assert job.history[-1]["curation_summary"]["warnings"] == 0
    assert fake.calls[0]["step"] == "curation" and "Bash" in fake.calls[0]["disallowed_tools"]
    # the content check is cached per script version: a second pass does not ask Claude again
    with Session(engine) as s:
        j = s.get(Job, job.id)
        j.state = "validating"
        s.add(j)
        s.commit()
    _validate(engine, job.id)
    assert len(fake.calls) == 1


def test_flagged_narration_goes_back_to_voicing_with_new_seeds(engine, monkeypatch):
    monkeypatch.setattr(claude_runner, "run_claude", _fake_claude())
    qa = {"checked": True, "sentences": 2, "flagged": [{"scene": 2, "text": "Fato um.", "heard": "Fato num", "flag": True}]}
    _, job, rep = _validate(engine, _make(engine, narration_qa=qa))
    assert job.state == "voicing" and rep["routed_to"] == "voicing"
    entry = job.history[-1]
    assert entry["curation"]["checks"] == ["voice"] and entry["curation"]["sentences"] == ["Fato um."]
    assert "Conferência automática" in entry["note"]
    assert curation.voice_redo(job) == {"Fato um.": 101}


def test_judge_rejected_visual_goes_back_to_visuals(engine, monkeypatch):
    monkeypatch.setattr(claude_runner, "run_claude", _fake_claude())
    bad = {"ok": False, "score": 3, "problems": ["deformed hand"], "action": "mantida (melhor tentativa)"}
    _, job, _ = _validate(engine, _make(engine, visual_meta=bad))
    assert job.state == "sourcing_visuals" and curation.visual_shift(job) == 10000


def test_clip_swapped_for_a_passing_still_is_not_a_problem(engine, monkeypatch):
    monkeypatch.setattr(claude_runner, "run_claude", _fake_claude())
    swapped = {"ok": False, "score": 4, "problems": ["morph"], "action": "clipe trocado pela imagem", "still_score": 8}
    _, job, _ = _validate(engine, _make(engine, visual_meta=swapped))
    assert job.state == "seo"


def test_ai_drawn_flag_goes_back_to_visuals(engine, monkeypatch):
    monkeypatch.setattr(claude_runner, "run_claude", _fake_claude())
    content = {**LONG, "scenes": [LONG["scenes"][0], {"index": 2, "narration": "A bandeira.",
                                                      "visual": {"type": "ai_image", "flag": "AE", "image_prompt": "UAE flag"}}]}
    _, job, rep = _validate(engine, _make(engine, content=content))
    assert job.state == "sourcing_visuals"
    assert any(c["name"] == "flags" and not c["ok"] for c in rep["checks"])


def test_render_problems_go_back_to_composing(engine, monkeypatch):
    monkeypatch.setattr(claude_runner, "run_claude", _fake_claude())
    _, job, rep = _validate(engine, _make(engine, render_meta={"end_screen_from": None, "end_screen_s": None}))
    assert job.state == "composing" and rep["routed_to"] == "composing"
    jid = _make(engine, render_meta={"analysis": {"black_s": 0, "frozen": [20.0, 15.0], "silences": [], "lufs": -14.0}})
    _, job, _ = _validate(engine, jid)
    assert job.state == "composing"  # the 20 s held end-screen frame is fine, a 15 s freeze elsewhere is not


def test_soft_warnings_alone_hand_over(engine, monkeypatch):
    monkeypatch.setattr(claude_runner, "run_claude", _fake_claude())
    jid = _make(engine, render_meta={"analysis": {"black_s": 0, "frozen": [], "silences": [3.0], "lufs": -17.0}})
    _, job, rep = _validate(engine, jid)
    assert job.state == "seo"
    r = next(c for c in rep["checks"] if c["name"] == "render")
    assert not r["ok"] and r["soft"] and "fix_state" not in r


def test_short_asking_to_subscribe_goes_back_to_the_script(engine, monkeypatch):
    monkeypatch.setattr(claude_runner, "run_claude", _fake_claude())
    short = {"hook": "h", "scenes": [{"index": 1, "narration": "Se inscreve no canal!", "visual": {"type": "broll"}}]}
    _, job, rep = _validate(engine, _make(engine, content=short, profile="youtube_shorts"))
    assert job.state == "scripting" and job.history[-1].get("revise") and job.history[-1]["curation"]["to"] == "scripting"
    assert "inscrição" in job.history[-1]["note"]
    assert "Conferência automática" in claude_runner.build_prompt(job)  # the scriptwriter gets the note


def test_fact_error_found_by_claude_goes_back_to_the_script(engine, monkeypatch):
    monkeypatch.setattr(claude_runner, "run_claude", _fake_claude(
        '{"ok": false, "problems": [{"scene": 2, "issue": "data errada", "severity": "high", "fix": "use 2024"}]}'))
    _, job, rep = _validate(engine, _make(engine))
    assert job.state == "scripting"
    assert "data errada" in job.history[-1]["note"]


def test_claude_limit_skips_the_content_check_without_blocking(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(claude_runner, "run_claude", lambda *a, **k: calls.append(1) or {"result": "{}"})
    with Session(engine) as s:
        claude_limit.set_limit(s, datetime.now(UTC) + timedelta(hours=2), "limit")
    _, job, rep = _validate(engine, _make(engine))
    assert job.state == "seo" and not calls
    c = next(c for c in rep["checks"] if c["name"] == "content")
    assert c["skipped"] and "Limite do Claude" in c["detail"]


def test_claude_limit_hit_during_the_check_is_skipped_too(engine, monkeypatch):
    def limited(*a, **k):
        raise claude_limit.ClaudeLimit(datetime.now(UTC) + timedelta(hours=1), "limit")
    monkeypatch.setattr(claude_runner, "run_claude", limited)
    monkeypatch.setattr(codex_runner, "available", lambda: False)  # no Codex reserve: the check is skipped
    _, job, rep = _validate(engine, _make(engine))
    assert job.state == "seo"
    assert next(c for c in rep["checks"] if c["name"] == "content")["skipped"]


def test_cap_stops_the_loop_and_hands_over_with_the_report(engine, monkeypatch):
    monkeypatch.setattr(claude_runner, "run_claude", _fake_claude())
    with Session(engine) as s:
        curation.put(s, {"max_attempts": 1})
    past = [{"from": "validating", "to": "voicing", "at": "2026-11-24T10:00:00", "note": "x",
             "curation": {"to": "voicing", "attempt": 1, "checks": ["voice"], "details": {"voice": "frase ruim"}}}]
    bad = {"ok": False, "score": 3, "problems": ["deformed hand"], "action": "mantida (melhor tentativa)"}
    _, job, rep = _validate(engine, _make(engine, visual_meta=bad, history=past))
    assert job.state == "seo" and rep["capped"] and rep["left"] == ["visuals"]
    assert rep["attempts"] == 1
    assert rep["fixed"] == [{"name": "voice", "label": "Narração", "attempts": 1, "detail": "frase ruim"}]


def test_same_check_is_not_sent_back_twice(engine, monkeypatch):
    monkeypatch.setattr(claude_runner, "run_claude", _fake_claude())
    past = [{"from": "validating", "to": "voicing", "at": "2026-11-24T10:00:00", "note": "x",
             "curation": {"to": "voicing", "attempt": 1, "checks": ["voice"], "details": {}}}]
    qa = {"checked": True, "sentences": 2, "flagged": [{"scene": 2, "text": "Fato um.", "heard": "Fato num"}]}
    _, job, rep = _validate(engine, _make(engine, narration_qa=qa, history=past))
    assert job.state == "seo" and rep["left"] == ["voice"]
    assert "não resolveu" in next(c for c in rep["checks"] if c["name"] == "voice")["detail"]


def test_a_rejection_at_publish_review_starts_the_count_again(engine):
    hist = [{"from": "validating", "to": "voicing", "curation": {"to": "voicing", "attempt": 1, "checks": ["voice"]}},
            {"from": "awaiting_publish_review", "to": "composing", "note": "music too loud"}]
    assert curation.attempts(Job(project_id=1, history=hist)) == 0


def test_disabled_goes_straight_to_seo(engine, monkeypatch):
    monkeypatch.setattr(claude_runner, "run_claude", _fake_claude())
    with Session(engine) as s:
        curation.put(s, {"enabled": False})
    qa = {"checked": True, "sentences": 2, "flagged": [{"scene": 2, "text": "Fato um.", "heard": "Fato num"}]}
    jid = _make(engine, narration_qa=qa)
    with Session(engine) as s:
        assert tasks.HANDLERS["validating"](s.get(Job, jid), s) == "seo"
        assert curation.latest_report(s, s.get(Job, jid).project_id) is None


def test_worker_drives_validating_to_seo(engine, monkeypatch):
    monkeypatch.setattr(engines, "auto_run", lambda: False)  # manual writer: SEO waits for the button, no Claude
    jid = _make(engine)
    tasks._drive(jid, set())
    with Session(engine) as s:
        job = s.get(Job, jid)
        assert job.state == "seo"
        assert next(c for c in curation.latest_report(s, job.project_id)["checks"] if c["name"] == "content")["skipped"]


def test_state_machine_has_the_new_step():
    assert pipeline.STATES.index("validating") == pipeline.STATES.index("rendering") + 1
    assert pipeline.can_transition("rendering", "validating") and pipeline.can_transition("validating", "voicing")
    job = Job(project_id=1, state="validating")
    pipeline.send_to_revision(job, "fix")
    assert job.state == "scripting"
    assert "validating" in tasks.WORK_STATES and tasks.slot_kind("validating") is None
