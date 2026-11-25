from sqlmodel import Session, SQLModel, create_engine

from cvm.models import Channel, Platform
from cvm.suggest import default_avatar, save_prefs, suggestion


def test_adjust_choices_become_channel_defaults_but_language_is_the_channels():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        ch = Channel(name="Canal", platform=Platform.youtube, language="pt-BR", prefs={"languages": ["pt-BR", "en-US"]})
        s.add(ch)
        s.commit()
        assert suggestion(s, [ch], "BR")["languages"] == ["pt-BR"]  # one channel = one language
        save_prefs(s, {"channel_id": ch.id, "languages": ["pt-BR", "en-US"], "platforms": ["youtube", "youtube_shorts"],
                       "avatar_id": None, "no_avatar": True})
        sug = suggestion(s, [ch], "BR")
        assert sug["languages"] == ["pt-BR"] and sug["platforms"] == ["youtube", "youtube_shorts"]
        assert "languages" not in ch.prefs
        assert default_avatar(s, ch) is None
