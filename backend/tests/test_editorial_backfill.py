"""Task 8: backfill of `editorial.cenas` for older editorial scripts (run_light faked)."""
import json

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from cvm import claude_limit
from cvm.models import Channel, Project, Script
from cvm.render.editorial.timeline import EditorialError
from cvm.scripting import agents, editorial_backfill

SRC = "https://news.example.com/a"
NARR = ["Olha isso agora", "carro voa longe", "custa caro hoje", "fim de tudo", "mais um dia", "outro dia aqui",
        "sexto ponto", "sétimo ponto", "oitavo ponto"]
WORDS = ["Olha", "carro", "custa", "fim", "mais", "outro", "sexto", "sétimo"]


def good():
    return [{"em": w, "tipo": "impacto", "texto": "Texto"} for w in WORDS]


@pytest.fixture
def env(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Channel(id=1, name="Mesa", platform="youtube", language="pt-BR", presenter="editorial"))
        s.add(Project(id=10, title="t", topic="t", channel_id=1, languages=["pt-BR"], platforms=["youtube"]))
        s.add(Script(project_id=10, language="pt-BR", version=1,
                     content={"hook": "h", "writer": "claude", "sources": [SRC],
                              "scenes": [{"index": i, "narration": n} for i, n in enumerate(NARR, 1)]}))
        s.commit()
        monkeypatch.setattr(agents, "wait_until", lambda *a, **k: None)
        yield s


def fake(monkeypatch, answers):
    calls = []

    def run_light(prompt, step, **kw):
        calls.append(prompt)
        return {"result": answers[min(len(calls), len(answers)) - 1], "writer": "claude"}

    monkeypatch.setattr(agents, "run_light", run_light)
    return calls


def test_backfill_saves_new_version(env, monkeypatch):
    fake(monkeypatch, [json.dumps({"cenas": good()})])
    p = env.get(Project, 10)
    out = editorial_backfill.backfill(env, p, "pt-BR")
    rows = env.exec(select(Script).order_by(Script.version)).all()
    assert [r.version for r in rows] == [1, 2]
    assert rows[1].content["editorial"]["cenas"] == good() and rows[1].content["writer"] == "claude"
    assert out["editorial"]["cenas"] == good()


def test_backfill_retries_once_then_revision(env, monkeypatch):
    bad = json.dumps({"cenas": [{"em": "inexistente", "tipo": "impacto", "texto": "x"}] * 8})
    calls = fake(monkeypatch, [bad, bad])
    with pytest.raises(EditorialError):
        editorial_backfill.backfill(env, env.get(Project, 10), "pt-BR")
    assert len(calls) == 2 and "problemas" in calls[1]
    assert len(env.exec(select(Script)).all()) == 1


def test_backfill_retry_can_succeed(env, monkeypatch):
    calls = fake(monkeypatch, ["lixo", json.dumps({"cenas": good()})])
    editorial_backfill.backfill(env, env.get(Project, 10), "pt-BR")
    assert len(calls) == 2


def test_backfill_skipped_when_present(env, monkeypatch):
    sc = env.exec(select(Script)).one()
    sc.content = {**sc.content, "editorial": {"cenas": good()}}
    env.add(sc)
    env.commit()
    calls = fake(monkeypatch, ["x"])
    editorial_backfill.backfill(env, env.get(Project, 10), "pt-BR")
    assert calls == [] and len(env.exec(select(Script)).all()) == 1


def test_backfill_waits_at_claude_limit(env, monkeypatch):
    from datetime import datetime, timedelta, timezone

    until = datetime.now(timezone.utc) + timedelta(hours=1)
    monkeypatch.setattr(agents, "wait_until", lambda *a, **k: until)
    calls = fake(monkeypatch, ["x"])
    with pytest.raises(claude_limit.ClaudeLimit):
        editorial_backfill.backfill(env, env.get(Project, 10), "pt-BR")
    assert calls == []


def test_backfill_then_words_for_needs_no_revoice(env, monkeypatch):
    from types import SimpleNamespace as NS

    import cvm.render.captions as cap
    import cvm.voice.narrate as nar
    from cvm.render.editorial import timeline

    fake(monkeypatch, [json.dumps({"cenas": good()})])
    editorial_backfill.backfill(env, env.get(Project, 10), "pt-BR")
    monkeypatch.setattr(nar, "narration_asset", lambda s, pid, lang: NS(path="/x", meta={
        "scenes": [], "duration": 5.0, "script_version": 1}))
    monkeypatch.setattr(cap, "word_timings", lambda *a: [{"w": "a", "s": 0, "e": 1}])
    assert timeline.words_for(env, NS(id=10), "pt-BR")[1] == 5.0
    sc = nar.latest_script(env, 10, "pt-BR")
    sc.content = {**sc.content, "scenes": [{"index": 1, "narration": "outro texto"}]}
    env.add(sc)
    env.commit()
    with pytest.raises(EditorialError) as e:
        timeline.words_for(env, NS(id=10), "pt-BR")
    assert e.value.fix_state == "voicing"


def test_backfill_passes_the_claude_slot(env, monkeypatch):
    got = []

    def run_light(prompt, step, **kw):
        got.append((step, kw.get("claude_slot")))
        return {"result": json.dumps({"cenas": good()}), "writer": "claude"}

    monkeypatch.setattr(agents, "run_light", run_light)
    slot = object()
    editorial_backfill.backfill(env, env.get(Project, 10), "pt-BR", claude_slot=slot)
    assert got == [("editorial", slot)]


@pytest.mark.parametrize("exc", [TimeoutError("claude -p timed out"), RuntimeError("claude crashed"),
                                 agents.ClaudeBusy("ocupado")])
def test_backfill_runner_errors_are_not_editorial_errors(env, monkeypatch, exc):
    """Review finding 4: a timeout or crash of the runner is not the script's fault: it raises as is (the step
    fails/retries like other steps) instead of sending the script back to revision."""
    calls = []

    def run_light(prompt, step, **kw):
        calls.append(1)
        raise exc

    monkeypatch.setattr(agents, "run_light", run_light)
    with pytest.raises(type(exc)) as e:
        editorial_backfill.backfill(env, env.get(Project, 10), "pt-BR")
    assert not isinstance(e.value, EditorialError) and len(calls) == 1
