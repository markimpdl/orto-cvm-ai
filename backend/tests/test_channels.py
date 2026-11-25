import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from cvm import channels
from cvm.api.crud import _validate_data
from cvm.models import Channel, Platform


@pytest.fixture
def s():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _ch(s, **kw):
    c = Channel(platform=Platform.youtube, **{"name": "c", "language": "pt-BR", **kw})
    s.add(c)
    s.commit()
    return c


def test_only_active_channels_take_new_videos(s):
    a, p, x = _ch(s, name="a"), _ch(s, name="p", status="paused"), _ch(s, name="x", status="archived")
    assert [c.name for c in channels.producing(s)] == ["a"]
    channels.check_new_project(a, ["pt-BR"])
    for c in (p, x):
        with pytest.raises(channels.ChannelClosed):
            channels.check_new_project(c, ["pt-BR"])


def test_a_channel_refuses_other_languages(s):
    with pytest.raises(channels.ChannelClosed, match="en-US"):
        channels.check_new_project(_ch(s), ["pt-BR", "en-US"])


@pytest.mark.parametrize("presenter", ["scenes", "overlay", "podcast", "editorial"])
def test_channel_create_and_patch_presenter_contract_accepts_supported_modes(presenter):
    _validate_data(Channel, {"presenter": presenter})


def test_channel_create_and_patch_presenter_contract_rejects_unknown_mode():
    with pytest.raises(HTTPException) as exc:
        _validate_data(Channel, {"presenter": "unknown"})
    assert exc.value.status_code == 422


def test_brief_is_generated_from_the_profile_plus_notes(s):
    c = _ch(s, name="Studio", brief="Nunca falar de política.", profile={
        "audience": "brasileiros 18–40", "persona": "cozinheira em Lisboa",
        "content_pillars": ["Receitas rápidas", "mercados de Lisboa"],
        "formats": [{"kind": "long", "min_s": 480, "max_s": 900, "per_week": 3},
                    {"kind": "short", "min_s": 35, "max_s": 50, "per_week": 7}]})
    b = channels.brief_text(c)
    assert "Público: brasileiros 18–40" in b and "- Receitas rápidas" in b and "long 8–15 min, 3/semana" in b
    assert "short 35–50 s, 7/semana" in b and b.endswith("Nunca falar de política.")


def test_siblings_share_the_group(s):
    pt, en = _ch(s, name="pt", group="cozinha"), _ch(s, name="en", language="en-US", group="cozinha")
    _ch(s, name="ar", language="ar-AE", group="cozinha", status="archived")
    assert [c.name for c in channels.siblings(s, pt)] == ["en"] and channels.siblings(s, en)[0].name == "pt"


def test_new_project_refuses_a_paused_channel(s, monkeypatch):
    from cvm import suggest

    c = _ch(s, status="paused")
    with pytest.raises(channels.ChannelClosed):
        suggest.new_project(s, {"title": "t", "topic": "t", "channel_id": c.id, "languages": ["pt-BR"]})


def test_a_series_keeps_its_bible_cast_and_sequence_on_another_channel(s):
    from cvm.models import Project

    cacau = _ch(s, name="Amigos do Cacau", status="archived", brief="Personagens: Cacau, Gil, Zeca",
               characters={"Cacau": {"voice": "cacau.wav"}}, profile={"series": True, "series_names": {"en-US": "Cacau and Friends"}})
    novel = _ch(s, name="Story Corner Kids", language="en-US", made_for_kids=True)
    old = Project(title="Amigos do Cacau – Ep. 9", topic="t", channel_id=cacau.id, languages=["pt-BR"])
    other = Project(title="Forest Pals Ep. 1", topic="t", channel_id=novel.id, languages=["en-US"])
    new = Project(title="Cacau and Friends – Ep. 1", topic="t", channel_id=novel.id, series_id=cacau.id, languages=["en-US"])
    for p in (old, other, new):
        s.add(p)
    s.commit()
    assert channels.series_of(s, new).id == cacau.id and channels.series_of(s, other).id == novel.id
    assert [p.id for p in channels.series_episodes(s, cacau.id, new.id + 1)] == [old.id, new.id]  # not Forest Pals
    seen = channels.for_agents(s, new, novel)
    assert seen["characters"] == {"Cacau": {"voice": "cacau.wav"}} and seen["series_name"] == "Cacau and Friends"
    assert "Bíblia da série:\nPersonagens: Cacau" in seen["brief"] and "Story Corner Kids (en-US" in seen["brief"]
    channels.check_new_project(novel, ["en-US"])  # the archived series does not block: the target channel is active
