import pytest

from cvm.render.editorial import timeline
from cvm.render.editorial.timeline import EditorialError, resolve



def W(*pairs):
    return [{"w": w, "s": s, "e": s + 0.3} for w, s in pairs]


def test_resolve_reference_matches_plan(editorial_set):
    tl, cenas, plan = editorial_set.timeline, editorial_set.cenas, editorial_set.plan
    words = [{"w": w, "s": s, "e": e} for w, s, e in tl["words"]]
    out = resolve(cenas, words, tl["total"], tl["blocks"][0]["speech_end"], 2.5)
    resolved = [s for s in plan if not s.get("fim")]
    assert len(out) == len(resolved)  # the scenes (short ones merged) + the bumper
    assert sum(s["tipo"] == "vinheta" for s in out) == 1
    for k, (got, want) in enumerate(zip(out, plan)):  # plan.json has end-screen scenes appended after the resolved ones
        assert got["tipo"] == want["tipo"]
        assert got["start"] == pytest.approx(want["start"], abs=0.01)
        if k < len(out) - 1:  # the last scene's end was moved by the appended scenes
            assert got["end"] == pytest.approx(want["end"], abs=0.01)
        assert got.get("_items") == want.get("_items")


def test_anchor_spelled_number_matches():
    out = resolve([{"em": "Um", "tipo": "impacto"}, {"em": "6", "tipo": "impacto"}],
                  W(("Um", 0), ("mês", 1), ("seis", 2.0), ("fim", 4)), 5, None, 0)
    assert out[1]["start"] == pytest.approx(1.92)


def test_missing_anchor_raises_editorial_error():
    with pytest.raises(EditorialError) as e:
        resolve([{"em": "zebra", "tipo": "impacto"}], W(("a", 0), ("b", 1)), 3, None, 0)
    assert "zebra" in str(e.value) and EditorialError.fix_state == "scripting"


def test_short_scene_merges():
    cenas = [{"em": "a", "tipo": "impacto"}, {"em": "b", "tipo": "impacto"}, {"em": "c", "tipo": "impacto"}]
    out = resolve(cenas, W(("a", 0), ("b", 3), ("c", 3.5)), 8, None, 0)
    assert [s["em"] for s in out] == ["a", "c"]  # b lasts 0.5 s, so a runs through it
    assert out[0]["end"] == pytest.approx(out[1]["start"]) == pytest.approx(3.42)


def test_bumper_inserted_after_hook():
    cenas = [{"em": "a", "tipo": "impacto"}, {"em": "c", "tipo": "impacto"}]
    words = W(("a", 0), ("b", 2), ("c", 6))
    out = resolve(cenas, words, 8, 3.0, 2.5)
    assert [s["tipo"] for s in out] == ["impacto", "vinheta", "impacto"]
    assert out[0]["end"] == pytest.approx(3.12) == out[1]["start"]
    assert out[1]["end"] == pytest.approx(5.92)
    assert [s["tipo"] for s in resolve(cenas, words, 8, None, 2.5)] == ["impacto", "impacto"]
    assert [s["tipo"] for s in resolve(cenas, words, 8, 3.0, 0)] == ["impacto", "impacto"]


def test_prova_highlight_and_items_relative():
    cenas = [{"em": "a", "tipo": "prova", "prova": {"destaque_em": ["b"]}},
             {"em": "c", "tipo": "lista", "itens": [["c", "X"], ["d", "Y"]]}]
    out = resolve(cenas, W(("a", 1), ("b", 2), ("c", 4), ("d", 5)), 7, None, 0)
    assert out[0]["_hl"] == [2.0]
    assert out[1]["_items"] == [{"t": 0.08, "text": "X"}, {"t": 1.08, "text": "Y"}]


def test_words_for(monkeypatch):
    from types import SimpleNamespace as NS

    import cvm.render.captions as cap
    import cvm.voice.narrate as nar
    monkeypatch.setattr(nar, "narration_asset", lambda s, pid, lang: NS(path="/x.wav", meta={"scenes": [{"index": 1}], "duration": 9.5}))
    monkeypatch.setattr(nar, "latest_script", lambda s, pid, lang: NS(content={"scenes": [{"index": 1}]}))
    monkeypatch.setattr(cap, "word_timings", lambda *a: [{"w": "a", "s": 0, "e": 1}])
    assert timeline.words_for(None, NS(id=1), "pt-BR") == ([{"w": "a", "s": 0, "e": 1}], 9.5)


def test_short_content_scene_survives():
    cenas = [{"em": "a", "tipo": "impacto"}, {"em": "b", "tipo": "prova", "prova": {"url": "u"}}, {"em": "c", "tipo": "impacto"}]
    out = resolve(cenas, W(("a", 0), ("b", 3), ("c", 3.8)), 8, None, 0)
    assert [s["tipo"] for s in out] == ["impacto", "prova", "impacto"]


def test_bumper_never_extended_by_short_card():
    cenas = [{"em": "a", "tipo": "impacto"}, {"em": "b", "tipo": "impacto"}, {"em": "c", "tipo": "impacto"}]
    out = resolve(cenas, W(("a", 0), ("x", 1.5), ("b", 3.0), ("c", 3.5), ("d", 6)), 8, 1.0, 2.5)
    bump = next(s for s in out if s["tipo"] == "vinheta")
    assert bump["end"] == pytest.approx(2.92)
    nxt = out[out.index(bump) + 1]
    assert nxt["start"] == pytest.approx(bump["end"]) and nxt["em"] == "c"


def test_bumper_split_clamps_cues():
    cenas = [{"em": "a", "tipo": "lista", "itens": [["a", "X"], ["d", "Y"]]}, {"em": "e", "tipo": "impacto"}]
    out = resolve(cenas, W(("a", 0), ("b", 1), ("d", 5), ("e", 7)), 9, 1.0, 2.5)
    first = out[0]
    assert first["end"] == pytest.approx(1.12)
    assert max(i["t"] for i in first["_items"]) <= first["end"] - 0.2 + 1e-6


def _fake(monkeypatch, meta, version):
    from types import SimpleNamespace as NS

    import cvm.render.captions as cap
    import cvm.voice.narrate as nar
    monkeypatch.setattr(nar, "narration_asset", lambda s, pid, lang: NS(path="/x.wav", meta=meta))
    monkeypatch.setattr(nar, "latest_script", lambda s, pid, lang: NS(content={"scenes": []}, version=version))
    monkeypatch.setattr(cap, "word_timings", lambda *a: [{"w": "a", "s": 0, "e": 4.2}])


def test_words_for_duration_fallback(monkeypatch):
    from types import SimpleNamespace as NS
    _fake(monkeypatch, {"scenes": []}, 1)
    assert timeline.words_for(None, NS(id=1), "pt-BR")[1] == 4.2


def test_words_for_script_version_mismatch(monkeypatch):
    from types import SimpleNamespace as NS
    _fake(monkeypatch, {"scenes": [], "script_version": 1}, 2)
    with pytest.raises(EditorialError) as e:
        timeline.words_for(None, NS(id=1), "pt-BR")
    assert e.value.fix_state == "voicing"
