"""Made-up words at the edges of a take, and the ElevenLabs budget rule (whole video in one voice)."""
from types import SimpleNamespace

from cvm.voice import narrate, qa

# word times measured on project 643 scene 25 (VoxCPM2 trained voice, 2026-11-23)
SCENE25 = [("aparecem", 0.0, 1.22), ("os", 1.22, 1.48), ("bichos", 1.48, 1.94), ("mais", 1.94, 2.24),
           ("pesados", 2.24, 2.82), ("do", 2.82, 2.98), ("passeio", 2.98, 3.4), ("inteiro.", 3.4, 3.74),
           ("Tiff", 4.14, 4.84), ("e...", 4.84, 5.32)]
TEXT = "aparecem os bichos mais pesados do passeio inteiro."


def words(rows):
    return [{"w": w, "s": s, "e": e} for w, s, e in rows]


def test_tail_after_a_pause_is_cut():
    r = qa.edge_extras(TEXT, words(SCENE25), "pt-BR")
    assert r["tail"] == "tiff e"
    assert 3.74 < r["cut_at"] < 4.14


def test_tail_glued_to_the_sentence_is_not_cut():
    rows = SCENE25[:8] + [("Tiff", 3.76, 4.2)]
    r = qa.edge_extras(TEXT, words(rows), "pt-BR")
    assert r["tail"] == "tiff" and "cut_at" not in r


def test_clean_take_and_repeated_last_word():
    assert qa.edge_extras(TEXT, words(SCENE25[:8]), "pt-BR") == {}
    rows = SCENE25[:8] + [("inteiro", 3.9, 4.2)]  # whisper repeating the last word is not a made-up word
    assert qa.edge_extras(TEXT, words(rows), "pt-BR") == {}


def test_lead_word_before_the_sentence():
    rows = [("Ans", 0.0, 0.3)] + [(w, s + 0.4, e + 0.4) for w, s, e in SCENE25[:8]]
    assert qa.edge_extras(TEXT, words(rows), "pt-BR")["lead"] == "ans"


def _project():
    return SimpleNamespace(id=1, avatar_id=1)


def _script(chars):
    return SimpleNamespace(content={"scenes": [{"index": 0, "narration": "x" * chars}]})


def test_eleven_profile_kept_when_credits_fit(monkeypatch):
    from cvm.voice import eleven

    prof = SimpleNamespace(id=9, params={"engine": "elevenlabs", "voice_id": "v"})
    monkeypatch.setattr(eleven, "available", lambda: True)
    monkeypatch.setattr(eleven, "fits", lambda n: True)
    got, why = narrate._eleven_or_fallback(None, _project(), "pt-BR", prof, _script(500))
    assert got is prof and why is None


def test_whole_video_falls_back_to_the_trained_voice(monkeypatch):
    from cvm.voice import eleven

    prof = SimpleNamespace(id=9, params={"engine": "elevenlabs", "voice_id": "v"})
    trained = SimpleNamespace(id=23, params={"engine": "voxcpm2", "mlx_model": "/x"})
    asked = []
    monkeypatch.setattr(eleven, "available", lambda: True)
    monkeypatch.setattr(eleven, "fits", lambda n: asked.append(n) or False)
    monkeypatch.setattr(narrate, "voice_profile", lambda s, a, lang, variant: trained if variant == "real" else None)
    got, why = narrate._eleven_or_fallback(None, _project(), "pt-BR", prof, _script(1000))
    assert got is trained and "créditos" in why
    assert asked and asked[0] > 1000  # room for a retake is asked for, not just the bare text


def test_non_eleven_profile_untouched():
    prof = SimpleNamespace(id=23, params={"engine": "voxcpm2", "mlx_model": "/x"})
    assert narrate._eleven_or_fallback(None, _project(), "pt-BR", prof, _script(10)) == (prof, None)
