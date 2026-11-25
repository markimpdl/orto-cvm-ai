"""'Fazer este tema para…': the main channel keeps its id, the chosen voice and format go to siblings that can use them."""

from sqlmodel import Session, SQLModel, create_engine

from cvm.models import Avatar, Channel, Platform, VoiceProfile
from cvm.suggest import for_channel


def test_choices_carry_to_the_sibling():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        a = Avatar(name="Studio", status="ready")
        s.add(a)
        s.commit()
        pt = Channel(name="pt", platform=Platform.youtube, language="pt-BR", group="studio", avatar_id=a.id)
        en = Channel(name="en", platform=Platform.youtube, language="en-US", group="studio", avatar_id=a.id)
        ar = Channel(name="ar", platform=Platform.youtube, language="ar-AE", group="studio", avatar_id=a.id)
        s.add_all([pt, en, ar])
        s.add_all([VoiceProfile(avatar_id=a.id, language=lang, engine="voxcpm2", params={"variant": "real"})
                   for lang in ("pt-BR", "en-US")])
        s.commit()
        data = {"topic": "t", "avatar_id": a.id, "voice_variant": "real", "platforms": ["youtube_shorts"]}
        main = for_channel(s, data, pt, pt)
        assert main["channel_id"] == pt.id and main["languages"] == ["pt-BR"]
        sib = for_channel(s, data, pt, en)
        assert sib["voice_variant"] == "real" and sib["platforms"] == ["youtube_shorts"] and sib["languages"] == ["en-US"]
        assert "voice_variant" not in for_channel(s, data, pt, ar)  # no trained Arabic voice: the default one
        pod = {**data, "presenter": "podcast"}
        assert for_channel(s, pod, pt, en)["presenter"] == "podcast"  # same presenter: same show format
        assert for_channel(s, pod, pt, ar)["presenter"] == "podcast"
        other = Avatar(name="Sara", status="ready")
        s.add(other)
        s.commit()
        ar.avatar_id = other.id
        s.add(ar)
        s.commit()
        assert "presenter" not in for_channel(s, pod, pt, ar)  # another presenter: that channel's own format


def test_main_channel_gets_its_default_formats_when_none_chosen():
    # 2026-11-22: a group video made without a format came out with no platform on the main channel (nothing rendered)
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        pt = Channel(name="pt", platform=Platform.youtube, language="pt-BR",
                     prefs={"platforms": ["youtube", "youtube_shorts"]})
        s.add(pt)
        s.commit()
        assert for_channel(s, {"topic": "t"}, pt, pt)["platforms"] == ["youtube", "youtube_shorts"]
        assert for_channel(s, {"topic": "t", "platforms": ["youtube"]}, pt, pt)["platforms"] == ["youtube"]
