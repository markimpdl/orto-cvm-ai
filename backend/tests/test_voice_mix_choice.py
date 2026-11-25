"""The voice picker knows "mix" (trained + ElevenLabs on acronyms): no "selected voice unavailable (mix)"."""
from cvm.api.avatars import _with_mix


def test_mix_offered_when_trained_and_eleven_exist():
    voices = [{"key": "real", "label": "Voz real", "languages": ["pt-BR", "en-US"]},
              {"key": "eleven", "label": "ElevenLabs", "languages": ["pt-BR", "en-US"]}]
    assert _with_mix(voices)[-1] == {"key": "mix", "label": "Treinada + ElevenLabs nas siglas", "languages": ["pt-BR", "en-US"]}


def test_no_mix_without_both():
    voices = [{"key": "real", "label": "Voz real", "languages": ["pt-BR"]}, {"key": "clone", "label": "IA", "languages": ["ar-AE"]}]
    assert _with_mix(voices) == voices
