from sqlmodel import Session, SQLModel, create_engine

from cvm.models import Channel, Platform
from cvm.voice import characters


def test_character_voice_is_reused_and_narrator_keeps_default(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    ch = Channel(name="Turma", platform=Platform.youtube)
    s.add(ch)
    s.commit()
    designed = []

    def fake_design(channel, name, description, language):
        wav = tmp_path / f"{name}.wav"
        wav.write_bytes(b"x")
        designed.append(name)
        return {"description": description, "voice": str(wav), "ref_text": "oi", "language": language}

    monkeypatch.setattr(characters, "design_voice", fake_design)
    monkeypatch.setattr("cvm.models_registry.is_ready", lambda name: True)
    assert characters.profile_for(s, ch, "Narradora", "pt-BR", {}) is None
    assert characters.profile_for(s, ch, "Gil", "pt-BR", {}) is None  # no description: narrator's voice
    p1 = characters.profile_for(s, ch, "Cacau", "pt-BR", {"Cacau": "small turtle girl, high and bright"})
    p2 = characters.profile_for(s, ch, "cacau", "pt-BR", {})  # next line/episode: same saved voice, no new design
    assert designed == ["Cacau"] and p1.ref_audio_path == p2.ref_audio_path and p1.id == p2.id < 0
    assert "Cacau" in s.get(Channel, ch.id).characters


def test_series_narrator_voice_beats_default(tmp_path):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    wav = tmp_path / "narradora.wav"
    wav.write_bytes(b"x")
    ch = Channel(name="Série", platform=Platform.youtube, characters={"Narradora": {"voice": str(wav), "ref_text": "era uma vez"}})
    s.add(ch)
    s.commit()
    assert characters.narrator_for(ch, "pt-BR").ref_audio_path == str(wav)
    assert characters.profile_for(s, ch, "narrador", "pt-BR", {}).ref_audio_path == str(wav)
    assert characters.narrator_for(Channel(name="x", platform=Platform.youtube), "pt-BR") is None
