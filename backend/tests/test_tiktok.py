"""TikTok account linked to a YouTube channel: connect, Shorts also scheduled on TikTok, caption, AI label, kit."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from cvm import channels as chans
from cvm.models import Channel, Job, Platform, Project, Publication, Render, now
from cvm.publish import service, tiktok

SEO = {
    "title": "O carro mais rápido do GTA 6 #shorts",
    "description": ("Testei os 5 carros mais rápidos do GTA 6 na mesma pista.\n"
                    "O último surpreendeu todo mundo.\n\n"
                    "Inscreva-se no canal e ative o sino!\n"
                    "Vídeo completo: https://youtube.com/watch?v=abc\n"
                    "0:00 Começo\n"
                    "#gta6 #shorts"),
    "tags": ["gta 6", "carros", "gta 6 carros mais rápidos do jogo"],
    "hashtags": ["#gta6", "#shorts", "gaming", "#YouTubeShorts"],
}


@pytest.fixture
def s():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(s):
    from cvm.db import get_session
    from cvm.main import app

    app.dependency_overrides[get_session] = lambda: s
    yield TestClient(app)
    app.dependency_overrides.clear()


def _yt(s, **kw):
    c = Channel(**{"name": "Canal Games", "platform": Platform.youtube, "language": "pt-BR", **kw})
    s.add(c)
    s.commit()
    return c


def _short(s, channel, profile="youtube_shorts", duration=45.0, meta=None):
    p = Project(title="Carros do GTA 6", topic="carros", channel_id=channel.id, languages=[channel.language])
    s.add(p)
    s.commit()
    s.add(Job(project_id=p.id, state="awaiting_publish_review"))
    r = Render(project_id=p.id, language=channel.language, profile=profile, path="/nonexistent.mp4", status="done",
               seo=dict(SEO), meta={"duration": duration, **(meta or {})})
    s.add(r)
    s.commit()
    return p, r


# ---- connect ---------------------------------------------------------------------------------------------------

def test_connect_creates_a_sibling_in_the_group(s):
    yt = _yt(s)
    tk = tiktok.connect(s, yt, "@canalteste.games")
    assert tk.platform == Platform.tiktok and tk.language == "pt-BR" and tk.group and tk.group == yt.group
    assert tk.prefs["linked_to"] == yt.id and tk.prefs["accounts"]["tiktok"] == "canalteste.games"
    assert yt.prefs["accounts"]["tiktok"] == "canalteste.games" and yt.prefs["tiktok_also"] is True
    assert tiktok.linked(s, yt).id == tk.id
    # again: same sibling, new handle; an existing group is kept
    assert tiktok.connect(s, yt, "https://www.tiktok.com/@outro_nome").id == tk.id
    assert tk.prefs["accounts"]["tiktok"] == "outro_nome"
    # never takes new videos
    assert tk.id not in [c.id for c in chans.producing(s)] and yt.id in [c.id for c in chans.producing(s)]
    with pytest.raises(chans.ChannelClosed, match="conta do TikTok"):
        chans.check_new_project(tk, ["pt-BR"])


def test_connect_keeps_an_existing_group_and_refuses_bad_input(s):
    yt = _yt(s, group="canalteste")
    assert tiktok.connect(s, yt, "canalteste").group == "canalteste"
    with pytest.raises(ValueError, match="@"):
        tiktok.connect(s, yt, "nome com espaço")
    tk_only = Channel(name="x", platform=Platform.tiktok)
    s.add(tk_only)
    s.commit()
    with pytest.raises(ValueError, match="YouTube"):
        tiktok.connect(s, tk_only, "@x")


def test_made_for_kids_channel_is_off_by_default(s):
    yt = _yt(s, made_for_kids=True)
    tk = tiktok.connect(s, yt, "@kids")
    assert tk.made_for_kids and yt.prefs["tiktok_also"] is False and not tiktok.default_on(yt)


def test_disconnect_archives_the_sibling(s):
    yt = _yt(s)
    tk = tiktok.connect(s, yt, "@a1")
    tiktok.disconnect(s, yt)
    assert tk.status == "archived" and tiktok.linked(s, yt) is None and "tiktok" not in yt.prefs["accounts"]


# ---- scheduling --------------------------------------------------------------------------------------------------

def test_approved_short_also_goes_to_tiktok_with_the_same_file(s):
    yt = _yt(s)
    tk = tiktok.connect(s, yt, "@canalteste")
    p, r = _short(s, yt)
    when = (now() + timedelta(hours=3)).replace(microsecond=0)
    pubs = service.schedule(s, p.id, [{"render_id": r.id, "channel_id": yt.id, "scheduled_at": when.isoformat()}])
    assert [(x.render_id, x.channel_id) for x in pubs] == [(r.id, yt.id), (r.id, tk.id)]
    # no TikTok slots: same time as the YouTube post; browser mode (kit + assist, the user clicks Post)
    assert pubs[1].scheduled_at == pubs[0].scheduled_at and pubs[1].mode.value == "browser"
    assert s.exec(select(Job)).one().state == "scheduled"


def test_tiktok_slots_are_used_when_the_account_has_them(s):
    yt = _yt(s)
    tk = tiktok.connect(s, yt, "@canalteste")
    tk.profile = {"publish_slots": [{"format": "short", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                                     "time": "21:00", "tz": "America/Sao_Paulo"}]}
    s.add(tk)
    s.commit()
    p, r = _short(s, yt)
    when = now() + timedelta(minutes=30)
    pubs = service.schedule(s, p.id, [{"render_id": r.id, "channel_id": yt.id, "scheduled_at": when.isoformat()}])
    local = pubs[1].scheduled_at.astimezone(__import__("zoneinfo").ZoneInfo("America/Sao_Paulo"))
    assert (local.hour, local.minute) == (21, 0)


def test_unticked_long_too_long_and_kids_are_not_added(s):
    yt = _yt(s)
    tiktok.connect(s, yt, "@canalteste")
    p, r = _short(s, yt)
    assert len(service.schedule(s, p.id, [{"render_id": r.id, "channel_id": yt.id, "tiktok": False}])) == 1
    p2, long = _short(s, yt, profile="youtube")  # 16:9: never
    assert len(service.schedule(s, p2.id, [{"render_id": long.id, "channel_id": yt.id, "tiktok": True}])) == 1
    p3, r3 = _short(s, yt, duration=200)
    notes: list[str] = []
    assert len(service.schedule(s, p3.id, [{"render_id": r3.id, "channel_id": yt.id}], notes)) == 1
    assert "200 s" in notes[0] and "180 s" in notes[0]
    kids = _yt(s, name="Dino", made_for_kids=True)
    tiktok.connect(s, kids, "@dino")
    p4, r4 = _short(s, kids)
    assert len(service.schedule(s, p4.id, [{"render_id": r4.id, "channel_id": kids.id}])) == 1  # never automatic
    p5, r5 = _short(s, kids)
    assert len(service.schedule(s, p5.id, [{"render_id": r5.id, "channel_id": kids.id, "tiktok": True}])) == 2


def test_no_linked_account_no_extra_post(s):
    yt = _yt(s)
    p, r = _short(s, yt)
    assert len(service.schedule(s, p.id, [{"render_id": r.id, "channel_id": yt.id}])) == 1


def test_guards_refuse_a_tiktok_account_in_another_language(s):
    from cvm.guards import PublishRefused

    yt = _yt(s)
    tk = tiktok.connect(s, yt, "@canalteste")
    tk.language = "en-US"  # edited by hand
    s.add(tk)
    s.commit()
    p, r = _short(s, yt)
    with pytest.raises(PublishRefused):
        service.schedule(s, p.id, [{"render_id": r.id, "channel_id": yt.id}])


def test_cancel_only_the_tiktok_post(s):
    yt = _yt(s)
    tiktok.connect(s, yt, "@canalteste")
    p, r = _short(s, yt)
    yt_pub, tk_pub = service.schedule(s, p.id, [{"render_id": r.id, "channel_id": yt.id,
                                                 "scheduled_at": (now() - timedelta(minutes=1)).isoformat()}])
    service.mark_published(s, yt_pub, "https://youtube.com/shorts/x")
    assert s.exec(select(Job)).one().state != "published"  # TikTok still pending
    service.cancel(s, tk_pub)
    assert s.exec(select(Job)).one().state == "published"
    with pytest.raises(ValueError):
        service.cancel(s, yt_pub)


# ---- caption and AI label ----------------------------------------------------------------------------------------

def test_caption_drops_youtube_lines_and_keeps_3_to_5_hashtags():
    r = Render(project_id=1, language="pt-BR", profile="youtube_shorts", seo=dict(SEO))
    text = tiktok.caption(r)
    assert text.startswith("O carro mais rápido do GTA 6\n")
    assert "#shorts" not in text.lower() and "#youtubeshorts" not in text.lower()
    assert "Inscreva" not in text and "http" not in text and "0:00" not in text
    assert "Testei os 5 carros" in text and "O último surpreendeu" in text
    tags = text.splitlines()[-1].split()
    assert 3 <= len(tags) <= 5 and tags[:2] == ["#gta6", "#gaming"] and "#carros" in tags
    assert len(text) <= 2200


def test_caption_from_seo_tiktok_fields_and_length_cap():
    r = Render(project_id=1, language="pt-BR", profile="youtube_shorts",
               seo={**SEO, "tiktok": {"caption": "Qual desses você pilotaria?", "hashtags": ["fyp"]}})
    assert tiktok.caption(r).startswith("Qual desses você pilotaria?\n\n#fyp")
    r2 = Render(project_id=1, language="pt-BR", profile="youtube_shorts",
                seo={"title": "x", "description": "palavra " * 800, "hashtags": ["a1", "b2", "c3"]})
    assert len(tiktok.caption(r2)) <= 2200 and tiktok.caption(r2).endswith("#a1 #b2 #c3")


def test_platforms_caption_text_uses_the_tiktok_caption():
    from cvm.publish.platforms import caption_text

    r = Render(project_id=1, language="pt-BR", profile="youtube_shorts", seo=dict(SEO))
    assert caption_text(r, "tiktok") == tiktok.caption(r)


def test_ai_label_follows_the_youtube_rule(s):
    yt = _yt(s)
    tk = tiktok.connect(s, yt, "@canalteste")
    p, r = _short(s, yt, meta={"avatar_on_screen": False})
    assert tiktok.ai_label(s, p, r, tk) is False
    r.meta = {**r.meta, "avatar_on_screen": True}
    assert tiktok.ai_label(s, p, r, tk) is True
    yt.prefs = {**yt.prefs, "publish_defaults": {"synthetic": "no"}}  # the channel's own choice (install wizard)
    s.add(yt)
    s.commit()
    assert tiktok.ai_label(s, p, r, tk) is False


# ---- API -----------------------------------------------------------------------------------------------------------

def test_api_connect_schedule_kit_and_cancel(s, client):
    yt = _yt(s)
    assert client.get(f"/channels/{yt.id}/tiktok").json()["connected"] is False
    assert client.post(f"/channels/{yt.id}/tiktok", json={"handle": "x y"}).status_code == 422
    info = client.post(f"/channels/{yt.id}/tiktok", json={"handle": "@canalteste"}).json()
    assert info["connected"] and info["handle"] == "canalteste" and info["also"] is True
    assert client.put(f"/channels/{yt.id}/tiktok", json={"also": False}).json()["also"] is False
    client.put(f"/channels/{yt.id}/tiktok", json={"also": True})
    goals = client.get("/channels/goals").json()
    assert [g["id"] for g in goals] == [yt.id]  # the TikTok account lives on its YouTube channel's page

    p, r = _short(s, yt, meta={"avatar_on_screen": True})
    out = client.post(f"/projects/{p.id}/schedule", json=[{"render_id": r.id, "channel_id": yt.id}]).json()
    assert len(out["scheduled"]) == 2 and len(out["tiktok"]) == 1 and out["notes"] == []
    kit = client.get(f"/publications/{out['tiktok'][0]}/kit").json()
    assert kit["upload_page"].startswith("https://www.tiktok.com/tiktokstudio/upload")
    assert kit["caption"] == tiktok.caption(r) and kit["ai_label"] is True and kit["handle"] == "canalteste"
    assert any("Conteúdo gerado por IA" in c and "ligar" in c for c in kit["checklist"])
    assert kit["file_url"] == f"/renders/{r.id}/file"
    assert client.delete(f"/publications/{out['tiktok'][0]}").json() == {"deleted": out["tiktok"][0]}
    assert s.exec(select(Publication)).all()[0].channel_id == yt.id
    assert client.delete(f"/channels/{yt.id}/tiktok").json()["connected"] is False
