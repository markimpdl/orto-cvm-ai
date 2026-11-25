from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from cvm import guards
from cvm.models import Asset, Channel, Job, Platform, Project, Render, Script, now

PT = ("Hoje eu vou mostrar como é a rotina de uma cozinheira que trabalha em São Paulo, quanto custa a feira, "
      "o que dá para preparar no fim de semana e por que você não precisa ter medo do forno. Mas isso não é tudo: "
      "também tem os doces, o café e muito mais para contar.")
EN = ("Today I am going to show you what a day is like for a cook who works in São Paulo, what the groceries cost, "
      "what you can make on the weekend and why you should not be afraid of the oven. But that is not all: there are "
      "the sweets, the coffee and much more to tell you about.")


@pytest.fixture
def s():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def no_worker(monkeypatch):
    from cvm.main import app

    monkeypatch.setattr("cvm.workers.tasks.run_job", lambda job_id: None)
    yield
    app.dependency_overrides.clear()


def _ch(s, **kw):
    c = Channel(platform=Platform.youtube, **{"name": "c", "language": "pt-BR", **kw})
    s.add(c)
    s.commit()
    return c


def _project(s, channel, title, topic=None, days_ago=0, **kw):
    p = Project(title=title, topic=topic or title, channel_id=channel.id, languages=[channel.language],
                created_at=now() - timedelta(days=days_ago), **kw)
    s.add(p)
    s.commit()
    return p


def _client(s):
    from cvm.db import get_session
    from cvm.main import app

    app.dependency_overrides[get_session] = lambda: s
    return TestClient(app)


@pytest.fixture
def group(s):
    return (_ch(s, name="Cozinha da Vila", group="cozinha"),
            _ch(s, name="Village Kitchen", language="en-US", group="cozinha"),
            _ch(s, name="مطبخ القرية", language="ar-AE", group="cozinha"))


# ---- similarity --------------------------------------------------------------------------------------------------

def test_similar_title_on_the_same_channel_in_30_days(s):
    c, other = _ch(s), _ch(s, name="outro")
    old = _project(s, c, "Quanto custa fazer a feira em São Paulo em 2026", days_ago=5)
    _project(s, other, "Quanto custa fazer a feira em São Paulo")  # another channel: not a repeat
    _project(s, c, "Quanto custa fazer a feira em Lisboa", days_ago=45)  # too old
    hits = guards.similar_recent(s, c, "Quanto custa fazer a feira em São Paulo?", "preço da feira")
    assert [h["project_id"] for h in hits] == [old.id]
    assert guards.similar_recent(s, c, "GTA 6: tudo o que sabemos do trailer", "GTA 6") == []


def test_topic_similarity_ignores_the_channel_and_series_words(s):
    c = _ch(s, name="Amigos do Cacau")
    for n, t in enumerate(["O bolo sumiu", "O balão voou", "O arco-íris perdido", "Dia de chuva"], 1):
        _project(s, c, f"Amigos do Cacau – Ep. {n}: {t}", days_ago=1)
    assert guards.similar_recent(s, c, "Amigos do Cacau – Ep. 5: A festa na floresta", "") == []
    assert guards.similar_recent(s, c, "Amigos do Cacau – Ep. 5: O bolo sumiu de novo", "")


def test_creation_refuses_a_similar_video_unless_forced(s):
    c = _ch(s)
    _project(s, c, "Quanto custa fazer a feira em São Paulo")
    data = {"title": "Quanto custa fazer a feira em São Paulo hoje", "topic": "x", "languages": ["pt-BR"]}
    with pytest.raises(guards.SimilarTopic) as e:
        guards.check_creation(s, data, c)
    assert e.value.similar[0]["title"] == "Quanto custa fazer a feira em São Paulo"
    warnings = guards.check_creation(s, {**data, "force_similar": True}, c)
    assert warnings and "Parecido" in warnings[0]


def test_kids_episode_without_premise_is_not_compared(s):
    c = _ch(s, name="Dino Curioso", made_for_kids=True)
    _project(s, c, "Dino Curioso – Ep. 1", topic="Episódio 1 da série animada Dino Curioso. Crie uma aventura nova", style="kids")
    w = guards.check_creation(s, {"title": "Dino Curioso – Ep. 2", "style": "kids", "languages": ["pt-BR"],
                                  "topic": "Episódio 2 da série animada Dino Curioso. Crie uma aventura nova"}, c)
    assert w == ["Dino Curioso é um canal infantil: o vídeo sai marcado como “conteúdo para crianças”."]


def test_creation_refuses_another_language(s):
    with pytest.raises(guards.ChannelClosed, match="en-US"):
        guards.check_creation(s, {"title": "t", "topic": "t", "languages": ["en-US"]}, _ch(s))


# ---- one project per channel ---------------------------------------------------------------------------------

def test_fazer_este_tema_para_creates_one_project_per_channel(s, group):
    pt, en, ar = group
    r = _client(s).post("/projects", json={"title": "Quanto custa fazer a feira em São Paulo", "topic": "Quanto custa fazer a feira em São Paulo",
                                           "style": "explainer", "languages": ["pt-BR"], "channel_id": pt.id,
                                           "channel_ids": [pt.id, en.id, ar.id], "voice_variant": "v2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel_id"] == pt.id and len(body["projects"]) == 3
    projects = s.exec(select(Project).order_by(Project.id)).all()
    assert [(p.channel_id, p.languages) for p in projects] == [(pt.id, ["pt-BR"]), (en.id, ["en-US"]), (ar.id, ["ar-AE"])]
    assert all("Adaptar, nunca traduzir" in p.topic for p in projects)
    assert "AED" in projects[2].topic and "US$" in projects[1].topic and "R$" in projects[0].topic
    assert projects[0].voice_variant == "v2" and projects[1].voice_variant is None  # avatar choices stay on the main
    assert len(s.exec(select(Job)).all()) == 3


def test_group_creation_is_all_or_nothing_and_force_similar(s, group):
    pt, en, _ = group
    _project(s, en, "How much does grocery shopping cost in São Paulo")
    client = _client(s)
    data = {"title": "How much does grocery shopping cost in São Paulo", "topic": "t", "channel_id": pt.id,
            "languages": ["pt-BR"], "channel_ids": [pt.id, en.id]}
    r = client.post("/projects", json=data)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "similar"
    assert r.json()["detail"]["similar"][0]["channel_id"] == en.id
    assert len(s.exec(select(Project)).all()) == 1  # nothing created
    r = client.post("/projects", json={**data, "force_similar": True})
    assert r.status_code == 200 and r.json()["warnings"]
    assert len(s.exec(select(Project)).all()) == 3


def test_single_channel_create_keeps_its_shape(s):
    c = _ch(s)
    r = _client(s).post("/projects", json={"title": "Tema", "topic": "Tema", "channel_id": c.id, "languages": ["pt-BR"]})
    assert r.status_code == 200 and r.json()["title"] == "Tema" and r.json()["warnings"] == []
    assert "Adapta" not in s.exec(select(Project)).one().topic


def test_batch_with_channel_ids(s, group):
    from cvm.models import Trend

    pt, en, _ = group
    t = Trend(source="google", region="BR", key="k", title="Preço do café sobe no Brasil")
    s.add(t)
    s.commit()
    r = _client(s).post("/projects/batch", json={"items": [{"trend_id": t.id}], "channel_ids": [pt.id, en.id]})
    assert r.status_code == 200, r.text
    assert sorted(p["channel_id"] for p in r.json()["created"]) == [pt.id, en.id]
    assert [p.languages for p in s.exec(select(Project).order_by(Project.id)).all()] == [["pt-BR"], ["en-US"]]


# ---- before scheduling ---------------------------------------------------------------------------------------

def _rendered(s, channel, lang, text, **kw):
    p = _project(s, channel, "Rotina na cozinha", **kw)
    s.add(Job(project_id=p.id, state="awaiting_publish_review"))
    s.add(Script(project_id=p.id, language=lang, content={"scenes": [{"narration": text}]}))
    s.add(Asset(project_id=p.id, kind="narration", language=lang, path="n.wav"))
    r = Render(project_id=p.id, language=lang, profile="youtube")
    s.add(r)
    s.commit()
    return p, r


def test_detect_language():
    assert guards.detect_language(PT) == "pt-BR" and guards.detect_language(EN) == "en-US"
    assert guards.detect_language("مرحبا بكم في قناتنا اليوم سنتحدث عن الحياة في أبوظبي وكم تكلف الإيجارات هناك") == "ar-AE"
    assert guards.detect_language("oi") is None


def test_schedule_refuses_a_foreign_language(s):
    from cvm.publish import service

    pt, en = _ch(s), _ch(s, name="en", language="en-US")
    p, r = _rendered(s, pt, "pt-BR", PT)
    with pytest.raises(guards.PublishRefused, match="cada canal recebe só o seu idioma"):
        service.schedule(s, p.id, [{"render_id": r.id, "channel_id": en.id}])
    p2, r2 = _rendered(s, pt, "pt-BR", EN)  # script written in English on a pt project
    with pytest.raises(guards.PublishRefused, match="parece estar em en-US"):
        service.schedule(s, p2.id, [{"render_id": r2.id, "channel_id": pt.id}])


def test_schedule_flags_kids_and_synthetic_content(s):
    from cvm.publish import service

    kids = _ch(s, name="Dino Curioso", made_for_kids=True)
    p, r = _rendered(s, kids, "pt-BR", PT, style="kids")
    service.schedule(s, p.id, [{"render_id": r.id, "channel_id": kids.id}])
    s.refresh(r)
    # a cartoon with a cloned voice but no avatar on screen: the AI box stays off (user's rule 2026-11-24)
    assert r.meta["made_for_kids"] is True and r.meta["synthetic_disclosure"] is False
    assert "voz sintética/clonada" in r.meta["synthetic_reasons"]


def test_ai_disclosure_only_when_the_avatar_is_on_screen(s):
    from cvm.publish import service

    ch = _ch(s)
    p, r = _rendered(s, ch, "pt-BR", PT)
    r.meta = {"avatar_scenes": [2, 5]}
    s.add(r)
    s.commit()
    service.schedule(s, p.id, [{"render_id": r.id, "channel_id": ch.id}])
    s.refresh(r)
    assert r.meta["synthetic_disclosure"] is True
    # editorial: the avatar picks the voice but never appears; compose records avatar_on_screen=False
    p2, r2 = _rendered(s, ch, "pt-BR", PT)
    p2.presenter, r2.meta = "editorial", {"avatar_scenes": [], "avatar_on_screen": False}
    s.add_all([p2, r2])
    s.commit()
    assert guards.avatar_on_screen(s, p2, r2) is False
    # older render without the flag: the whole-video presenter (overlay) counts as on screen
    p3, r3 = _rendered(s, ch, "pt-BR", PT)
    p3.presenter, p3.avatar_id = "overlay", 1
    assert guards.avatar_on_screen(s, p3, r3) is True


def test_kids_series_never_goes_to_a_channel_not_made_for_kids(s):
    from cvm.publish import service

    series = _ch(s, name="Amigos do Cacau", made_for_kids=True, status="series")
    adult = _ch(s, name="Adulto")
    p, r = _rendered(s, adult, "pt-BR", PT, style="kids", series_id=series.id)
    with pytest.raises(guards.PublishRefused, match="série infantil"):
        service.schedule(s, p.id, [{"render_id": r.id, "channel_id": adult.id}])


def test_channel_idea_produced_on_siblings(s, group):
    from cvm.models import TrendAnalysis

    pt, en, ar = group
    a = TrendAnalysis(channel_id=pt.id, status="done", items=[{"topic": "Golden Visa explicado", "style": "explainer"}])
    s.add(a)
    s.commit()
    r = _client(s).post(f"/channels/{pt.id}/ideas/produce", json={"indexes": [0], "channel_ids": [en.id, ar.id]})
    assert r.status_code == 200, r.text
    projects = s.exec(select(Project).order_by(Project.id)).all()
    assert [p.channel_id for p in projects] == [pt.id, en.id, ar.id] and r.json()["created"] == [p.id for p in projects]
    s.refresh(a)
    assert a.items[0]["project_id"] == projects[0].id
