from sqlmodel import Session, SQLModel, create_engine

from cvm.models import Avatar, VoiceProfile
from cvm.voice.narrate import voice_profile, voice_variants


def test_named_voices_pick_their_own_reference():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Avatar(id=4, name="Sara"))
        s.add(VoiceProfile(id=4, avatar_id=4, language="ar-AE", ref_audio_path="mira.wav",
                           params={"variant": "mira", "label": "Mira"}))
        s.add(VoiceProfile(id=14, avatar_id=4, language="pt-BR", ref_audio_path="ia_pt.wav",
                           params={"variant": "ia", "label": "Voz IA", "made_from": "ar-AE"}))
        s.add(VoiceProfile(id=22, avatar_id=4, language="ar-AE", ref_audio_path="ia_ar.wav",
                           params={"variant": "ia", "label": "Voz IA"}))
        s.commit()
        assert voice_profile(s, 4, "ar-AE").id == 4  # deterministic low-level fallback; UI/channel policy picks defaults
        assert voice_profile(s, 4, "ar-AE", "ia").id == 22
        assert voice_profile(s, 4, "pt-BR", "ia").id == 14
        assert voice_profile(s, 4, "pt-BR", "mira").id == 4  # no pt-BR take of Mira: cloned across languages
        assert voice_profile(s, 4, "ar-AE", "nope").id == 4
        assert [v["key"] for v in voice_variants(s.query(VoiceProfile).all())] == ["mira", "ia"]


def test_narrator_entry_without_voice_is_not_a_voice(tmp_path):
    from cvm.models import Channel
    from cvm.voice import characters

    c = Channel(id=99, name="x", platform="youtube", characters={"Narrador": {"description": "deep male"}})
    assert characters.narrator_for(c, "pt-BR") is None
    wav = tmp_path / "n.wav"
    wav.write_bytes(b"RIFF")
    c.characters = {"Narrador": {"description": "deep male", "voice": str(wav)}}
    assert characters.narrator_for(c, "pt-BR") is not None


def test_channel_default_voice_applies_only_to_its_avatar():
    from cvm.models import Channel, Project
    from cvm.voice.narrate import project_variant

    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Channel(id=4, name="Studio", platform="youtube", avatar_id=1, prefs={"voice_variant": "real"}))
        s.commit()
        assert project_variant(s, Project(title="a", channel_id=4, avatar_id=1)) == "real"
        assert project_variant(s, Project(title="b", channel_id=4, avatar_id=1, voice_variant="ia")) == "ia"
        assert project_variant(s, Project(title="c", channel_id=4, avatar_id=3)) is None  # another presenter
        assert project_variant(s, Project(title="d", avatar_id=1)) is None


def _ear_lexicon(monkeypatch, tmp_path, lexicon: dict) -> None:
    """A private/ overlay with names picked by ear for one trained voice."""
    import json

    from cvm import private

    (tmp_path / "voice").mkdir(exist_ok=True)
    (tmp_path / "voice" / "trained_lexicon.json").write_text(json.dumps(lexicon), encoding="utf-8")
    monkeypatch.setattr(private, "PRIVATE_DIR", tmp_path)


def test_trained_voice_reads_loanwords_as_written(monkeypatch, tmp_path):
    from cvm.voice.narrate import trained_voice
    from cvm.voice.text import prepare

    _ear_lexicon(monkeypatch, tmp_path, {"as_written": {"pt-BR": ["porto alegre"]}, "say": {"pt-BR": {"Spotify": "Spótifai"}}})
    assert "bâgui" in prepare("Achei um bug.", "pt-BR")
    assert "bug" in prepare("Achei um bug.", "pt-BR", trained=True)
    pron = {"Porto Alegre": "Pórto Alégre", "Curitiba": "Curitíba", "Spotify": "Ispótifai"}
    said = prepare("Porto Alegre, Curitiba e Spotify.", "pt-BR", pron, trained=True)
    assert said.startswith("Porto Alegre") and "Curitíba" in said and "Spótifai" in said  # the ear-picked form beats the script's
    assert "Spótifai" in prepare("No Spotify.", "pt-BR", trained=True)
    assert "Pórto Alégre" in prepare("Porto Alegre", "pt-BR", pron)
    assert trained_voice(VoiceProfile(avatar_id=1, language="pt-BR", params={"mlx_model": "x"}))
    assert not trained_voice(VoiceProfile(avatar_id=1, language="pt-BR", params={"variant": "ia"}))
    assert not trained_voice(None)


def test_trained_voice_without_private_lexicon(monkeypatch, tmp_path):
    from cvm import private
    from cvm.voice.text import prepare

    monkeypatch.setattr(private, "PRIVATE_DIR", tmp_path)  # public copy: no ear-picked names
    pron = {"Porto Alegre": "Pórto Alégre", "Google": "Gúgol"}
    said = prepare("Porto Alegre e Google.", "pt-BR", pron, trained=True)
    assert "Pórto Alégre" in said and "Google" in said  # the built-in brand names still stay as written


def test_owner_trained_lexicon_still_applies():
    """The owner's ear-picked names (private/voice/trained_lexicon.json) reach his trained voice as before."""
    import json

    import pytest

    from cvm import private
    from cvm.voice.text import prepare, trained_as_written, trained_say

    f = private.PRIVATE_DIR / "voice" / "trained_lexicon.json"
    if not f.exists():
        pytest.skip("no private/voice/trained_lexicon.json (public copy)")
    own = json.loads(f.read_text(encoding="utf-8"))
    for lang, names in own.get("as_written", {}).items():
        assert {n.lower() for n in names} <= trained_as_written(lang)
        for n in names:
            if lang == "pt-BR":
                assert n.lower() in prepare(n.title(), lang, {n.title(): "Xyzzy"}, trained=True).lower()
    for lang, says in own.get("say", {}).items():
        assert trained_say(lang).items() >= says.items()
        for name, said in says.items():
            assert said in prepare(f"{name}.", lang, trained=True)


def test_english_narration_contracts_like_speech():
    from cvm.voice.text import contract_english, prepare

    assert contract_english("You are going to love it. I cannot wait.") == "You're going to love it. I can't wait."
    assert contract_english("Here you are.") == "Here you are."  # nothing follows: full form stays
    assert contract_english("Do not miss it. What is next?") == "Don't miss it. What's next?"
    assert "you're" in prepare("Now you are here.", "en-US")
    assert "you are" in prepare("Now you are here.", "pt-BR")


def test_first_word_must_be_heard():
    from cvm.voice.qa import first_word_ok

    assert first_word_ok("Se inscreve no canal.", "Se inscreve no canal.", "pt-BR")
    assert not first_word_ok("Se inscreve no canal.", "Ans inscreve no canal.", "pt-BR")
    assert not first_word_ok("Pesquisei no Google.", "Pisei no Google.", "pt-BR")
    assert first_word_ok("Pesquisei no Google.", "Pesquizei no Google.", "pt-BR")  # whisper spelling slip


def test_trained_voice_reads_paragraphs():
    from cvm.voice.narrate import paragraphs

    assert paragraphs(["A b.", "C d.", "E f."], limit=10) == ["A b. C d.", "E f."]
    assert paragraphs(["x" * 30, "y."], limit=10) == ["x" * 30, "y."]
    assert paragraphs([]) == []


def test_trained_voice_english_names_and_acronyms():
    from cvm.voice.text import prepare

    pron = {"Lisbon": "Liz-bun", "Porto": "Por-too", "web3": "web three"}
    t = "Lisbon and Porto, web3 and GTA 6."
    said = prepare(t, "en-US", pron, trained=True)
    assert "Lisbon and Porto" in said and "web three" in said and "G T A six" in said  # syllable respellings skipped
    assert "Liz-bun and Por-too" in prepare(t, "en-US", pron) and "jee tee ay" in prepare(t, "en-US", pron)


def test_trained_voice_game_names_picked_by_ear(monkeypatch, tmp_path):
    from cvm.voice.text import prepare

    _ear_lexicon(monkeypatch, tmp_path, {"as_written": {"pt-BR": ["hollow knight", "hollow", "knight"]},
                                         "say": {"pt-BR": {"Need for Speed": "Níd for Spíd"}}})
    lex = {"Hollow Knight": "Rólou Náit", "Need for Speed": "Nid fór Ispid"}
    said = prepare("Hollow Knight e Need for Speed.", "pt-BR", lex, trained=True)
    assert "Hollow Knight" in said and "Níd for Spíd" in said
