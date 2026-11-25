"""Hiss the voice model sometimes makes instead of a pause (a kids episode, PC) is a bad take; speech and fricatives are not."""
import pytest
import soundfile as sf

from cvm import private
from cvm.voice import qa

# real excerpts of an owner's episode (his channel's cloned voice): private/, absent in the public copy
DATA = private.PRIVATE_DIR / "fixtures" / "voice"


def _bursts(name: str):
    if not (DATA / name).exists():
        pytest.skip("no private/fixtures/voice (public copy)")
    audio, sr = sf.read(DATA / name, dtype="float32")
    return qa.noise_bursts(audio, sr)


def test_hiss_is_caught():
    assert _bursts("tts_noise_burst.wav")  # "E aí" then ~0.8 s of static


def test_clean_speech_and_fricatives_pass():
    assert _bursts("tts_clean_speech.wav") == []  # "Depois vieram os carros roubados"
    assert _bursts("tts_fricatives.wav") == []  # "bombas, sequestros e mortes": strong s/x sounds
