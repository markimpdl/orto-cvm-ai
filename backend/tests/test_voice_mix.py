"""Mixed voice: trained voice + ElevenLabs only for sentences with acronyms / English words (user, 2026-11-23)."""
from types import SimpleNamespace

import numpy as np

from cvm.voice import narrate

TRAINED = SimpleNamespace(id=23, params={"engine": "voxcpm2", "mlx_model": "/x"})
ELEVEN = SimpleNamespace(id=25, params={"engine": "elevenlabs", "voice_id": "v"})
SCRIPT = SimpleNamespace(content={"scenes": [
    {"index": 0, "narration": "Começa pelo oficial. A Rockstar lançou GTA 6 só pro PS5 e pro Xbox Series. E o PC ficou de fora."},
    {"index": 1, "narration": "O jogo ainda não inicia. Ninguém confirmou o arquivo."},
]})


def _patch(monkeypatch, fits=True):
    from cvm.voice import eleven
    monkeypatch.setattr(narrate, "voice_profile", lambda s, a, lang, v: ELEVEN if v == "eleven" else TRAINED)
    monkeypatch.setattr(eleven, "available", lambda: True)
    monkeypatch.setattr(eleven, "fits", lambda n: fits)


def test_foreign_sentences_go_to_eleven(monkeypatch):
    _patch(monkeypatch)
    plan, alt = narrate._mix_plan(None, SimpleNamespace(id=1, avatar_id=1), "pt-BR", SCRIPT, {}, TRAINED)
    assert alt is ELEVEN and set(plan) == {0}  # scene 1 has nothing foreign: read normally
    flags = [f for _, f in plan[0]]
    assert flags == [False, True]  # "Começa pelo oficial." trained; the GTA/PS5 + PC sentences together by ElevenLabs


def test_no_credits_means_trained_only(monkeypatch):
    _patch(monkeypatch, fits=False)
    assert narrate._mix_plan(None, SimpleNamespace(id=1, avatar_id=1), "pt-BR", SCRIPT, {}, TRAINED) == ({}, None)


def test_no_mix_outside_pt_br_and_en_us(monkeypatch):
    _patch(monkeypatch)
    assert narrate._mix_plan(None, SimpleNamespace(id=1, avatar_id=1), "ar-AE", SCRIPT, {}, TRAINED) == ({}, None)


def test_en_us_mix_sends_only_acronyms_and_brands(monkeypatch):
    _patch(monkeypatch)
    script = SimpleNamespace(content={"scenes": [{"index": 0, "narration":
        "Let's start with what is official. Rockstar released GTA 6 only on PS5. The code is on GitHub. Nobody has played it."}]})
    plan, alt = narrate._mix_plan(None, SimpleNamespace(id=1, avatar_id=1), "en-US", script, {}, TRAINED)
    assert alt is ELEVEN and [f for _, f in plan[0]] == [False, True, False]


def test_eleven_piece_levelled_to_trained(monkeypatch):
    loud = np.full(24000, 0.4, dtype="float32"); quiet = np.full(24000, 0.1, dtype="float32")
    takes = iter([(quiet, "voxcpm2", {}), (loud, "elevenlabs", {})])
    monkeypatch.setattr(narrate, "_best_take", lambda *a, **k: next(takes))
    out = narrate._mixed_scene([("a", False), ("b", True)], "pt-BR", TRAINED, ELEVEN, None, 1, True, [], set(), 0, [], False)
    assert abs(narrate._rms(out[-1]) - 0.1) < 1e-3


def test_eleven_sentences_skip_foreign_respellings(monkeypatch):
    _patch(monkeypatch)
    script = SimpleNamespace(content={"scenes": [{"index": 0, "narration": "Você termina no Downtown, perto da Times Square."}]})
    plan, _ = narrate._mix_plan(None, SimpleNamespace(id=1, avatar_id=1), "pt-BR", script,
                                {"Times Square": "Táimis Iskuér", "Downtown": "Dáuntaun", "Cacau": "Cacáu"}, TRAINED)
    text, by_eleven = plan[0][0]
    assert by_eleven and "Times Square" in text and "Downtown" in text and "Iskuér" not in text


def test_editorial_short_keeps_the_channel_voice(monkeypatch):
    from cvm.models import Channel
    ch = SimpleNamespace(avatar_id=1, prefs={"voice_variant": "mix"})
    sess = SimpleNamespace(get=lambda model, i: ch if model is Channel else None)
    project = SimpleNamespace(avatar_id=None, channel_id=4, voice_variant=None, presenter="editorial")
    assert narrate.voice_avatar(sess, project) == 1
    assert narrate.project_variant(sess, project) == "mix"


def test_eleven_failure_falls_back_to_trained_for_that_piece(monkeypatch):
    trained_audio = np.full(24000, 0.1, dtype="float32")
    def fake(text, language, voice, *a, **k):
        if voice is ELEVEN:
            raise RuntimeError("ElevenLabs inacessível")
        return trained_audio, "voxcpm2", {}
    monkeypatch.setattr(narrate, "_best_take", fake)
    report = []
    out = narrate._mixed_scene([("a", False), ("b GTA 6", True)], "pt-BR", TRAINED, ELEVEN, None, 1, True, report, set(), 0, [], False)
    assert len(out) == 3 and report[-1]["voice"] == "trained" and "eleven_failed" in report[-1]
