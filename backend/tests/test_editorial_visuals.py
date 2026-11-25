"""Task 5 of the editorial format: the visuals step for long news-desk videos (real footage/photos per scene with
judge-checked windows, proof screenshots, section backgrounds, plan.json). Network, yt-dlp, judge, CLIP and
Playwright are all faked."""
import json
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm.models import Channel, Job, Project, Script
from cvm.render.editorial import timeline
from cvm.render.editorial.timeline import EditorialError
from cvm.visuals import editorial as ev
from cvm.visuals import footage, judge, proof
from cvm.visuals import select as vselect

_real_find_footage = footage.find_footage

SRC = "https://news.example.com/artigo"


def W(*pairs):
    return [{"w": w, "s": s, "e": s + 0.3} for w, s in pairs]


WORDS = W(("Olha", 0.0), ("isso", 0.5), ("carro", 3.0), ("voa", 3.5), ("custa", 6.0), ("caro", 6.5), ("fim", 9.0))
CENAS = [
    {"em": "Olha", "tipo": "pergunta", "texto": "Isso é real?", "secao": ["01", "O carro"]},
    {"em": "carro", "tipo": "video", "busca": {"termos": ["flying car"], "preferir": "oficial",
                                                 "official_channels": ["Maker"]}},
    {"em": "custa", "tipo": "prova", "prova": {"url": SRC, "frase": "custa caro"}, "fonte": "news.example.com"},
]


@pytest.fixture
def env(monkeypatch, tmp_path):
    import cvm.db
    from cvm.config import settings

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(cvm.db, "engine", eng)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with Session(eng) as s:
        s.add(Channel(id=1, name="Mesa", platform="youtube", language="pt-BR", presenter="editorial",
                      allow_official_clips=True, allow_article_images=True))
        s.add(Project(id=10, title="t", topic="t", channel_id=1, languages=["pt-BR"], platforms=["youtube"]))
        s.add(Script(project_id=10, language="pt-BR", version=1,
                     content={"hook": "h", "scenes": [{"index": 1, "narration": "x"}], "sources": [SRC],
                              "editorial": {"cenas": CENAS}}))
        s.commit()
    monkeypatch.setattr(timeline, "words_for", lambda session, project, lang: (WORDS, 9.3))
    monkeypatch.setattr(ev, "_duration", lambda p: 8.0)
    monkeypatch.setattr(ev, "_black_spans", lambda p: [])
    monkeypatch.setattr(ev, "_strip", lambda video, a, b, out: Path(out))
    monkeypatch.setattr(ev, "_background", lambda src, kind, at, out: Path(out))
    monkeypatch.setattr(judge, "ready", lambda: True)
    monkeypatch.setattr(judge, "judge", lambda items, cartoon=False: [{"clean": True, "ok": True} for _ in items])
    monkeypatch.setattr(proof, "capture", lambda url, phrase, out, timeout_s=45:
                        {"img": str(out), "size": [1200, 600], "boxes": [[10, 20, 300, 40]]})
    from cvm.visuals import clip_rank

    monkeypatch.setattr(footage, "clip_match", lambda path, query: None)  # CLIP missing unless a test says otherwise
    monkeypatch.setattr(clip_rank, "available", lambda: False)
    monkeypatch.setattr(vselect, "_broll", lambda *a, **k: None)
    monkeypatch.setattr(vselect, "_article_image", lambda *a, **k: None)
    calls = {"footage": []}

    def fake_footage(scene, dest, allow_official, used):
        calls["footage"].append(allow_official)
        n = len(calls["footage"])
        return {"kind": "video", "path": str(tmp_path / f"clip{n}.mp4"), "source": f"https://yt/{n}",
                "license": "official upload", "credit": "Maker", "license_kind": "official"}

    monkeypatch.setattr(footage, "find_footage", fake_footage)
    return eng, calls


def _plan(eng) -> list[dict]:
    with Session(eng) as s:
        path = ev.source(s, s.get(Project, 10), "pt-BR")
    return json.loads(Path(path).read_text())


def test_plan_written_with_credits(env, tmp_path):
    eng, calls = env
    plan = _plan(eng)
    assert Path(tmp_path / "projects" / "10" / "pt-BR" / "editorial" / "plan.json").exists()
    assert [s["tipo"] for s in plan] == ["pergunta", "video", "prova"]
    v = plan[1]["media"]
    assert v["kind"] == "video" and v["file"].endswith("clip1.mp4") and v["credit"] == "Maker"
    assert v["start"] == 0.0 and v["window_end"] == pytest.approx(3.0 + 0.5)  # scene 2.92..5.92 → 3.0 s + 0.5
    assert plan[1]["fonte"] == "Maker" and v["source"] == "https://yt/1" and v["license"] == "official upload"
    p = plan[2]
    assert p["img"].endswith(".png") and p["size"] == [1200, 600] and p["boxes"] == [[10, 20, 300, 40]]
    assert p["fonte"] == "news.example.com"
    assert plan[0]["start"] == 0.0 and plan[1]["start"] == pytest.approx(2.92)
    bg = plan[0]["bg"]  # the section's background comes from its first footage, for every scene of the section
    assert bg and bg.endswith(".jpg") and "/bg/" in bg and plan[2]["bg"] == bg
    assert calls["footage"] == [True]


def test_no_footage_becomes_card(env, monkeypatch):
    eng, _ = env
    monkeypatch.setattr(footage, "find_footage", lambda *a, **k: None)
    plan = _plan(eng)
    card = plan[1]
    assert card["tipo"] == "pergunta" and card["fallback"]
    assert card["texto"].lower().startswith("carro voa")  # the narration's own words
    assert "media" not in card


def test_official_skipped_when_switch_off(env):
    eng, calls = env
    with Session(eng) as s:
        ch = s.get(Channel, 1)
        ch.allow_official_clips = False
        s.add(ch)
        s.commit()
    _plan(eng)
    assert calls["footage"] and not any(calls["footage"])


def test_proof_with_article_switch_off_becomes_numero_card(env, monkeypatch):
    eng, _ = env
    with Session(eng) as s:
        ch = s.get(Channel, 1)
        ch.allow_article_images = False
        s.add(ch)
        sc = s.get(Script, 1)
        cenas = [dict(c) for c in CENAS]
        cenas[2] = {**cenas[2], "valor": "US$ 1 mi"}
        sc.content = {**sc.content, "editorial": {"cenas": cenas}}
        s.add(sc)
        s.commit()
    monkeypatch.setattr(proof, "capture", lambda *a, **k: pytest.fail("no screenshot with the switch off"))
    plan = _plan(eng)
    assert plan[2]["tipo"] == "numero" and plan[2]["valor"] == "US$ 1 mi"
    assert plan[2]["fonte"] == "news.example.com" and plan[2]["fallback"]


def test_proof_capture_returning_a_path_still_writes_memo_and_plan(env, monkeypatch):
    """Review finding 1: a capture handing back a Path (old proof.capture) must not break the memo/plan json."""
    eng, _ = env
    monkeypatch.setattr(proof, "capture", lambda url, phrase, out, timeout_s=45:
                        {"img": Path(out), "size": [1200, 600], "boxes": [[10, 20, 300, 40]]})
    plan = _plan(eng)
    assert isinstance(plan[2]["img"], str) and plan[2]["img"].endswith(".png")
    memos = list((Path(plan[2]["img"]).parent.parent / "cache").glob("proof_*.json"))
    assert memos and json.loads(memos[0].read_text())["img"] == plan[2]["img"]


def test_proof_phrase_missing_tries_next_source_then_card(env, monkeypatch):
    eng, _ = env
    tried = []
    monkeypatch.setattr(proof, "capture", lambda url, phrase, out, timeout_s=45: tried.append(url))
    plan = _plan(eng)
    assert tried == [SRC]  # the only source; nothing else to try
    assert plan[2]["tipo"] == "pergunta" and plan[2]["fallback"] and plan[2]["fonte"] == "news.example.com"


def test_window_rejected_by_judge_tries_next(env, monkeypatch):
    eng, _ = env
    seen = []

    def fake_judge(items, cartoon=False):
        seen.extend(items)
        return [{"clean": i > 0, "ok": True, "problems": ["logo de TV"] if i == 0 else []} for i, _ in enumerate(items)]

    monkeypatch.setattr(judge, "judge", fake_judge)
    plan = _plan(eng)
    v = plan[1]["media"]
    assert v["kind"] == "video" and v["start"] > 0  # the second window
    assert v["window_end"] - v["start"] == pytest.approx(3.5)
    assert all(it["rule"] == "editorial_window" and it["kind"] == "clip" for it in seen)


def test_judge_unavailable_takes_first_window(env, monkeypatch):
    eng, _ = env
    monkeypatch.setattr(judge, "ready", lambda: False)
    monkeypatch.setattr(judge, "judge", lambda *a, **k: pytest.fail("judge not installed"))
    assert _plan(eng)[1]["media"]["start"] == 0.0


def test_short_clip_slowed_at_most_1_7(env, monkeypatch):
    eng, _ = env
    monkeypatch.setattr(ev, "_duration", lambda p: 1.5)  # 3.5 s needed: 1.5 × 1.7 < 3.5 → no window, next clip
    plan = _plan(eng)
    assert plan[1]["tipo"] == "pergunta" and plan[1]["fallback"]
    monkeypatch.setattr(ev, "_duration", lambda p: 2.5)  # 2.5 × 1.7 ≥ 3.5 → the whole clip, slowed by the renderer
    plan = _plan(eng)
    assert plan[1]["media"]["kind"] == "video" and plan[1]["media"]["window_end"] == pytest.approx(2.5, abs=0.15)


def test_bank_photo_for_foto_scene(env, monkeypatch, tmp_path):
    eng, _ = env
    with Session(eng) as s:
        sc = s.get(Script, 1)
        cenas = [dict(c) for c in CENAS]
        cenas[1] = {"em": "carro", "tipo": "foto", "busca": {"termos": ["flying car"]}}
        sc.content = {**sc.content, "editorial": {"cenas": cenas}}
        s.add(sc)
        s.commit()
    monkeypatch.setattr(footage, "find_footage", lambda *a, **k: pytest.fail("a photo scene asks for no clip"))
    monkeypatch.setattr(vselect, "_broll", lambda scene, portrait, dest, used=None: {
        "kind": "image", "path": str(tmp_path / "p.jpg"), "source": "wikimedia", "license": "cc by", "credit": "Ana"})
    plan = _plan(eng)
    m = plan[1]["media"]
    assert m["kind"] == "photo" and m["file"].endswith("p.jpg") and m["credit"] == "Ana"
    assert m["start"] == 0.0 and m["window_end"] is None


def test_never_an_ai_image(env, monkeypatch):
    eng, _ = env
    monkeypatch.setattr(footage, "find_footage", lambda *a, **k: None)
    monkeypatch.setattr(vselect, "_ai_image", lambda *a, **k: pytest.fail("AI image in an editorial video"))
    assert _plan(eng)[1]["tipo"] == "pergunta"


def test_script_without_cenas_goes_back_to_scripting(env):
    eng, _ = env
    with Session(eng) as s:
        sc = s.get(Script, 1)
        sc.content = {k: v for k, v in sc.content.items() if k != "editorial"}
        s.add(sc)
        s.commit()
        with pytest.raises(EditorialError) as e:
            ev.source(s, s.get(Project, 10), "pt-BR")
    assert e.value.fix_state == "scripting"


# --- the handler ---------------------------------------------------------------------------------------------------

def _run_handler(eng, project_kw=None):
    from cvm.workers import tasks

    with Session(eng) as s:
        p = s.get(Project, 10)
        for k, v in (project_kw or {}).items():
            setattr(p, k, v)
        s.add(p)
        s.add(Job(id=5, project_id=10, state="sourcing_visuals", history=[]))
        s.commit()
        job = s.get(Job, 5)
        nxt = tasks.HANDLERS["sourcing_visuals"](job, s)
        s.refresh(job)
        return nxt, job.state, job.history


@pytest.fixture
def handler_spy(env, monkeypatch):
    eng, _ = env
    called = {"editorial": [], "classic": 0}
    monkeypatch.setattr(ev, "source", lambda s, p, lang: called["editorial"].append(lang))

    def classic(session, project, portrait=True):
        called["classic"] += 1
        return []

    monkeypatch.setattr(vselect, "source_visuals", classic)
    return eng, called


def test_vertical_and_non_editorial_untouched(handler_spy):
    eng, called = handler_spy
    assert _run_handler(eng, {"platforms": ["youtube", "youtube_shorts"]})[0] == "lipsyncing"
    assert called == {"editorial": ["pt-BR"], "classic": 1}  # the Short still needs the classic set


def test_non_editorial_project_keeps_classic_path(handler_spy):
    eng, called = handler_spy
    assert _run_handler(eng, {"presenter": "scenes"})[0] == "lipsyncing"
    assert called == {"editorial": [], "classic": 1}


def test_editorial_only_skips_classic_set(handler_spy):
    eng, called = handler_spy
    assert _run_handler(eng)[0] == "lipsyncing"
    assert called == {"editorial": ["pt-BR"], "classic": 0}


def test_editorial_error_sends_script_back(handler_spy, monkeypatch):
    eng, called = handler_spy

    def boom(s, p, lang):
        raise EditorialError("A palavra-âncora 'zebra' não aparece na narração.")

    monkeypatch.setattr(ev, "source", boom)
    nxt, state, history = _run_handler(eng)
    assert nxt is None and state == "scripting"
    assert history[-1]["revise"] and "zebra" in history[-1]["note"]
    assert called["classic"] == 0


def test_editorial_error_voicing_goes_back_to_voicing(handler_spy, monkeypatch):
    eng, _ = handler_spy

    def boom(s, p, lang):
        err = EditorialError("A narração é de outra versão do roteiro; gere a voz de novo.")
        err.fix_state = "voicing"
        raise err

    monkeypatch.setattr(ev, "source", boom)
    nxt, state, history = _run_handler(eng)
    assert nxt is None and state == "voicing" and "outra versão" in history[-1]["note"]


# --- review fixes ------------------------------------------------------------------------------------------------

def _attach(eng, tmp_path, name="meu.mp4", kind="video", description=""):
    from cvm.models import Asset

    f = tmp_path / name
    f.write_bytes(b"x")
    with Session(eng) as s:
        s.add(Asset(project_id=10, kind="user_media", path=str(f), meta={"kind": kind, "description": description}))
        s.commit()
    return str(f)


def test_attached_file_matching_the_scene_comes_first(env, tmp_path):
    eng, calls = env
    own = _attach(eng, tmp_path, description="Flying car prototype test flight")
    plan = _plan(eng)
    assert plan[1]["media"]["file"] == own and plan[1]["media"]["own"] and calls["footage"] == []
    assert "fonte" not in plan[1]  # own material has no third-party credit


def test_attached_file_not_matching_keeps_the_normal_order(env, tmp_path, monkeypatch):
    eng, calls = env
    _attach(eng, tmp_path, description="my cat sleeping")
    monkeypatch.setattr(footage, "clip_match", lambda path, query: 0.15 if "meu" in str(path) else None)
    plan = _plan(eng)
    assert plan[1]["media"]["file"].endswith("clip1.mp4") and calls["footage"] == [True]


def test_attached_file_clip_match(env, tmp_path, monkeypatch):
    eng, _ = env
    own = _attach(eng, tmp_path)
    monkeypatch.setattr(footage, "clip_match", lambda path, query: 0.31)
    assert _plan(eng)[1]["media"]["file"] == own


def test_bank_and_commons_videos_are_clip_checked(env, tmp_path, monkeypatch):
    eng, calls = env
    monkeypatch.setattr(footage, "find_footage", lambda *a, **k: {
        "kind": "video", "path": str(tmp_path / "commons.webm"), "source": "https://commons/x", "license": "cc by",
        "credit": "", "license_kind": "free"})  # Commons: find_footage did no CLIP
    banks = iter([{"kind": "video", "path": str(tmp_path / "bank_off.mp4"), "source": "pexels", "credit": "Bo",
                   "url": "https://pexels/1"},
                  {"kind": "video", "path": str(tmp_path / "bank_ok.mp4"), "source": "pexels", "credit": "Cy",
                   "url": "https://pexels/2"}])
    monkeypatch.setattr(vselect, "_broll", lambda *a, **k: next(banks, None))
    monkeypatch.setattr(footage, "clip_match", lambda path, query: 0.30 if "ok" in str(path) else 0.12)
    m = _plan(eng)[1]["media"]
    assert m["file"].endswith("bank_ok.mp4") and m["credit"] == "Cy"


def test_judge_without_answer_is_not_judged_clean(env, monkeypatch):
    eng, _ = env
    monkeypatch.setattr(judge, "judge", lambda items, cartoon=False:
                        [{"ok": True, "score": None, "problems": ["juiz indisponível"]} for _ in items])
    m = _plan(eng)[1]["media"]
    assert m["start"] == 0.0 and m["judged"] is False


def test_judge_called_in_chunks(env, monkeypatch):
    eng, _ = env
    sizes = []
    monkeypatch.setattr(ev, "_judge", ev._judge)
    monkeypatch.setattr(judge, "judge", lambda items, cartoon=False: sizes.append(len(items)) or
                        [{"clean": True} for _ in items])
    items = [{"image": str(i)} for i in range(95)]
    assert len(ev._judge(items)) == 95 and sizes == [40, 40, 15]


def test_editorial_send_backs_capped(handler_spy, monkeypatch):
    eng, _ = handler_spy

    def boom(s, p, lang):
        raise EditorialError("A palavra-âncora 'zebra' não aparece na narração.")

    monkeypatch.setattr(ev, "source", boom)
    from cvm.workers import tasks

    old = [{"from": "sourcing_visuals", "to": "scripting", "note": f"{tasks.EDITORIAL_NOTE} x", "revise": True}] * 2
    with Session(eng) as s:
        s.add(Job(id=5, project_id=10, state="sourcing_visuals", history=old))
        s.commit()
        job = s.get(Job, 5)
        assert tasks.HANDLERS["sourcing_visuals"](job, s) is None
        s.refresh(job)
        assert job.state == "failed" and "zebra" in job.error and "Confira o roteiro" in job.error


def test_second_run_reuses_searches_and_proofs(env, monkeypatch, tmp_path):
    eng, _ = env
    monkeypatch.setattr(footage, "find_footage", _real_find_footage)
    ytdlp, caps = [], []

    def fake_ytdlp(args, timeout=60):
        ytdlp.append(args)
        if "--skip-download" in args:
            return {"duration": 120, "license": "Creative Commons Attribution", "channel": "Maker"}
        if any(str(a).startswith("ytsearch") for a in args):
            return {"entries": [{"url": "https://yt/v1", "title": "Flying car test", "channel": "Maker",
                                 "duration": 120, "view_count": 5}]}
        return {"entries": []}

    monkeypatch.setattr(footage, "_ytdlp_json", fake_ytdlp)
    monkeypatch.setattr(footage.sources, "source_clip", lambda url, out, start=0, length=20: tmp_path / "yt.mp4")

    def cap(url, phrase, out, timeout_s=45):
        caps.append(url)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"png")
        return {"img": str(out), "size": [10, 10], "boxes": []}

    monkeypatch.setattr(proof, "capture", cap)
    first = _plan(eng)
    assert first[1]["media"]["source"] == "https://yt/v1" and ytdlp and caps == [SRC]
    n = len(ytdlp)
    second = _plan(eng)
    assert len(ytdlp) == n and caps == [SRC]  # nothing searched or captured again
    assert second[1]["media"] == first[1]["media"] and second[2]["img"] == first[2]["img"]


def test_long_scene_skips_youtube(env, monkeypatch, tmp_path):
    eng, calls = env
    monkeypatch.setattr(timeline, "words_for", lambda session, project, lang: (
        W(("Olha", 0.0), ("carro", 3.0), ("custa", 20.0), ("fim", 22.0)), 23.0))
    monkeypatch.setattr(ev, "_duration", lambda p: 30.0)
    monkeypatch.setattr(vselect, "_broll", lambda *a, **k: {
        "kind": "video", "path": str(tmp_path / "long.mp4"), "source": "pixabay", "credit": "Di", "url": "https://px/1"})
    m = _plan(eng)[1]["media"]
    assert calls["footage"] == [] and m["file"].endswith("long.mp4")
    assert m["window_end"] - m["start"] == pytest.approx(17.0 + 0.5)


def test_overlapping_windows_of_one_file_never_reused(env, tmp_path, monkeypatch):
    eng, _ = env
    monkeypatch.setattr(ev, "_duration", lambda p: 30.0)
    own = _attach(eng, tmp_path, description="flying car")
    with Session(eng) as s:
        sc = s.get(Script, 1)
        cenas = [dict(c) for c in CENAS]
        cenas[2] = {"em": "custa", "tipo": "video", "busca": {"termos": ["flying car"], "preferir": "meus"}}
        sc.content = {**sc.content, "editorial": {"cenas": cenas}}
        s.add(sc)
        s.commit()
    plan = _plan(eng)
    a, b = plan[1]["media"], plan[2]["media"]
    assert a["file"] == b["file"] == own
    assert a["window_end"] <= b["start"] or b["window_end"] <= a["start"]


def test_fonte_falls_back_to_source_host(env, monkeypatch, tmp_path):
    eng, _ = env
    monkeypatch.setattr(footage, "find_footage", lambda *a, **k: {
        "kind": "video", "path": str(tmp_path / "c.mp4"), "source": "https://www.example.org/v", "license": "cc",
        "credit": None, "clip": 0.3})
    assert _plan(eng)[1]["fonte"] == "example.org"


def test_search_without_terms_uses_text_and_section():
    s = {"em": "isso", "tipo": "foto", "texto": "Um carro que voa", "secao": ["01", "Carros voadores"]}
    assert ev._terms(s) == ["Um carro que voa", "Carros voadores"]
    assert ev._terms({"em": "isso", "tipo": "foto"}) == ["isso"]


def test_backfill_without_a_free_claude_slot_retries_later(handler_spy, monkeypatch):
    """Review finding 2: the backfill runs with the worker's Claude slot; none free → the visuals step waits and
    retries (no failure, no send-back)."""
    from cvm.scripting import agents, editorial_backfill
    from cvm.workers import tasks

    eng, called = handler_spy
    with Session(eng) as s:
        sc = s.get(Script, 1)
        sc.content = {k: v for k, v in sc.content.items() if k != "editorial"}
        s.add(sc)
        s.commit()
    seen = []

    def busy(session, project, lang, claude_slot=None):
        seen.append(claude_slot)
        raise agents.ClaudeBusy("ocupado")

    monkeypatch.setattr(editorial_backfill, "backfill", busy)
    scheduled = []
    monkeypatch.setattr(tasks.run_job, "schedule", lambda args, delay=None: scheduled.append((args, delay)))
    nxt, state, history = _run_handler(eng)
    assert nxt is None and state == "sourcing_visuals" and history == []
    assert seen == [tasks._claude_slot_for_check] and scheduled == [((5,), tasks.RETRY_S)]
    assert called == {"editorial": [], "classic": 0}


def _send_back_with(eng, monkeypatch, history):
    from cvm.workers import tasks

    def boom(s, p, lang):
        raise EditorialError("A palavra-âncora 'zebra' não aparece na narração.")

    monkeypatch.setattr(ev, "source", boom)
    with Session(eng) as s:
        s.add(Job(id=5, project_id=10, state="sourcing_visuals", history=history))
        s.commit()
        job = s.get(Job, 5)
        tasks.HANDLERS["sourcing_visuals"](job, s)
        s.refresh(job)
        return job.state


def test_retry_after_the_cap_gets_fresh_attempts(handler_spy, monkeypatch):
    """Review finding 4: send-backs count only after the last failure / human move."""
    from cvm.workers import tasks

    eng, _ = handler_spy
    back = {"from": "sourcing_visuals", "to": "scripting", "note": f"{tasks.EDITORIAL_NOTE} x", "revise": True}
    hist = [back, back, {"from": "sourcing_visuals", "to": "failed", "note": f"{tasks.EDITORIAL_NOTE} cap"},
            {"from": "failed", "to": "sourcing_visuals", "note": "retry"}]
    assert _send_back_with(eng, monkeypatch, hist) == "scripting"


def test_human_approval_resets_but_autopilot_does_not(handler_spy, monkeypatch):
    from cvm.workers import tasks

    back = {"from": "sourcing_visuals", "to": "scripting", "note": f"{tasks.EDITORIAL_NOTE} x", "revise": True}
    human = {"from": "awaiting_approval", "to": "voicing", "note": None}
    auto = {"from": "awaiting_approval", "to": "voicing", "note": "aprovado automaticamente"}
    assert tasks._editorial_returns([back, back, human]) == 0
    assert tasks._editorial_returns([back, back, human, back]) == 1
    assert tasks._editorial_returns([back, auto, back]) == 2
    eng, _ = handler_spy
    assert _send_back_with(eng, monkeypatch, [back, auto, back]) == "failed"


def test_window_with_black_is_skipped(env, monkeypatch):
    """A clip that opens on black (an official trailer's fade-in) never gives the scene a black window (728)."""
    eng, _ = env
    monkeypatch.setattr(ev, "_black_spans", lambda p: [(0.0, 2.6)])
    v = _plan(eng)[1]["media"]
    assert v["kind"] == "video" and v["start"] >= 2.6 - ev.BLACK_MIN


def test_clear_of_black():
    spans = [(10.0, 13.0)]
    assert ev.clear_of_black((0.0, 4.0), spans)
    assert ev.clear_of_black((9.5, 13.5), [(10.0, 10.4)])  # a short dip (a cut) is fine
    assert not ev.clear_of_black((9.0, 12.0), spans)
    assert not ev.clear_of_black((12.0, 16.0), spans)


def test_black_spans_parses_blackdetect(monkeypatch, tmp_path):
    import subprocess

    class R:
        stderr = "[blackdetect @ 0x1] black_start:0 black_end:2.5 black_duration:2.5\n" \
                 "[blackdetect @ 0x1] black_start:30.04 black_end:31.2 black_duration:1.16\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    f = tmp_path / "c.mp4"
    f.write_bytes(b"x")
    assert ev._black_spans(str(f)) == [(0.0, 2.5), (30.04, 31.2)]
