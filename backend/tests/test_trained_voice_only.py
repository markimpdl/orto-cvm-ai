"""User's rule (2026-11-22): a video that asks for the trained voice never falls back to the AI clone or macOS `say`."""
from pathlib import Path
from types import SimpleNamespace

import pytest


def _trained(tmp_path):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"x")
    return SimpleNamespace(id=23, ref_audio_path=str(ref), language="pt-BR",
                           params={"engine": "voxcpm2", "variant": "real", "mlx_model": "/m"})


def test_trained_voice_failure_never_falls_back(tmp_path, monkeypatch):
    from cvm import models_registry
    from cvm.voice import engine

    monkeypatch.setattr(models_registry, "is_ready", lambda name: True)
    used = []

    def boom(*a, **k):
        raise RuntimeError("voxcpm crashed")

    monkeypatch.setattr(engine, "_voxcpm", boom)
    monkeypatch.setattr(engine, "_say", lambda *a, **k: used.append("macos") or a[2])
    with pytest.raises(RuntimeError, match="voz treinada"):
        engine.synthesize("oi", "pt-BR", _trained(tmp_path), tmp_path / "o.wav")
    assert used == []


def test_trained_voice_missing_raises_instead_of_clone(monkeypatch):
    from cvm.voice import narrate

    clone = SimpleNamespace(id=1, ref_audio_path="/r.wav", language="pt-BR", params={"variant": "clone"})
    monkeypatch.setattr(narrate, "voice_profile", lambda s, a, l, v=None: clone)
    with pytest.raises(RuntimeError, match="voz treinada"):
        narrate.checked_profile(None, 1, "pt-BR", "real")


def test_cloned_voice_is_voxcpm2_and_never_macos(tmp_path, monkeypatch):
    """Chatterbox removed (user, 2026-11-24): every voice with a reference goes to VoxCPM2; a lively tone dict
    becomes a style instruction; VoxCPM2 missing fails instead of reading with macOS' voice."""
    from cvm import models_registry
    from cvm.voice import engine

    ref = tmp_path / "r.wav"
    ref.write_bytes(b"x")
    character = SimpleNamespace(id=-5, ref_audio_path=str(ref), ref_text=None, language="pt-BR", params={"engine": "chatterbox"})
    seen = []
    monkeypatch.setattr(models_registry, "is_ready", lambda name: True)
    monkeypatch.setattr(engine, "_voxcpm", lambda text, lang, prof, out, seed, instruct: seen.append(instruct) or out)
    monkeypatch.setattr(engine, "_say", lambda *a, **k: seen.append("macos") or a[2])
    assert engine.synthesize("oi", "pt-BR", character, tmp_path / "o.wav", tone={"exaggeration": 0.85})[1] == "voxcpm2"
    assert seen == [engine.LIVELY]
    monkeypatch.setattr(models_registry, "is_ready", lambda name: False)
    with pytest.raises(RuntimeError, match="VoxCPM2"):
        engine.synthesize("oi", "pt-BR", character, tmp_path / "o.wav")
