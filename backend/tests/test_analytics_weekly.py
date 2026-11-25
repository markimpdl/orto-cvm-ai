"""Phase 7 (publish kit) and Phase 8 (YouTube Analytics + weekly cycle). No network, no Keychain, no Claude, no Huey."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from cvm.models import Channel, Platform, Project, Publication, Render, Setting, TrendAnalysis, now
from cvm.publish import analytics, weekly


@pytest.fixture
def s():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def tokens(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(analytics, "load_token", lambda ref: store.get(ref))
    monkeypatch.setattr(analytics, "save_token", lambda ref, tok: store.__setitem__(ref, tok))
    monkeypatch.setattr(analytics, "client", lambda: ("cid", "secret"))
    analytics._ACCESS.clear()
    return store


def _ch(s, **kw):
    c = Channel(platform=Platform.youtube, **{"name": "Studio", "language": "pt-BR", **kw})
    s.add(c)
    s.commit()
    return c


def _video(s, ch, title, profile="youtube", url=None, views=0, status="published", project=None, days_ago=1):
    pr = project or Project(title=title, topic=title, channel_id=ch.id)
    s.add(pr)
    s.commit()
    r = Render(project_id=pr.id, language=ch.language, profile=profile, path="/x.mp4", status="done", seo={"title": title})
    s.add(r)
    s.commit()
    p = Publication(render_id=r.id, channel_id=ch.id, status=status, url=url,
                    published_at=now() - timedelta(days=days_ago), metrics={"viewCount": views} if views else {})
    s.add(p)
    s.commit()
    return pr, r, p


# ---------------- Phase 7: kit ----------------

def test_long_video_kit_suggests_the_best_long_video_and_adds_footer_and_cross_links(s):
    from cvm.api.publish import kit

    ch = _ch(s, profile={"description_footer": "Siga o Studio!", "cross_links": ["@VillageKitchen", "@Already"]})
    _video(s, ch, "GTA 6 tudo que sabemos", url="https://youtube.com/watch?v=AAAAAAAAAAA", views=900, days_ago=10)
    _video(s, ch, "Rotina na cozinha", url="https://youtube.com/watch?v=BBBBBBBBBBB", views=50)
    _video(s, ch, "Um short", profile="youtube_shorts", url="https://youtube.com/shorts/CCCCCCCCCCC", views=99999)
    _, r, p = _video(s, ch, "Novo vídeo", status="ready_for_manual_publish")
    r.seo = {"title": "Novo vídeo", "description": "0:00 Abertura\n1:10 Parte 2\n\nveja @Already"}
    r.meta = {"end_screen_from": 605}
    s.add(r)
    s.commit()
    k = kit(p.id, s)
    assert k["end_screen_video"]["url"].endswith("AAAAAAAAAAA") and k["end_screen_video"]["why"] == "best"
    assert k["description"].count("Siga o Studio!") == 1
    assert "https://www.youtube.com/@VillageKitchen" in k["description"] and "/@Already" not in k["description"]
    assert not k["synthetic"] and not k["made_for_kids"] and k["chapters"] and k["category"]["id"]  # no avatar
    text = "\n".join(k["checklist"])
    assert "alterado ou sintético: Não" in text
    for off in ("Promoção paga", "Capítulos automáticos", "localização automática", "notificações aos inscritos"):
        assert off in text
    assert "alterado ou sintético" in text and "AAAAAAAAAAA" in text and "10:05" in text and "Capítulos" in text
    assert k["related_video"] is None


def test_short_kit_links_its_long_video_and_kids_channels_are_locked(s):
    from cvm.api.publish import kit

    ch = _ch(s, name="Dino Curioso", made_for_kids=True)
    _video(s, ch, "Outro episódio", url="https://youtube.com/watch?v=OOOOOOOOOOO", days_ago=1)
    pr, _, _ = _video(s, ch, "Episódio do vulcão", url="https://youtube.com/watch?v=VVVVVVVVVVV", days_ago=5)
    _, _, short = _video(s, ch, "Vulcão em 40 s", profile="youtube_shorts", status="ready_for_manual_publish", project=pr)
    k = kit(short.id, s)
    assert k["is_short"] and k["related_video"]["url"].endswith("VVVVVVVVVVV") and k["related_video"]["why"] == "same_project"
    assert k["made_for_kids"] and k["made_for_kids_locked"] and k["end_screen_video"] is None
    assert any("conteúdo para crianças" in c for c in k["checklist"])


def test_end_screen_falls_back_to_the_latest_long_video(s):
    from cvm.api.publish import end_screen_video

    ch = _ch(s)
    _video(s, ch, "velho", url="https://youtube.com/watch?v=11111111111", days_ago=9)
    _video(s, ch, "novo", url="https://youtube.com/watch?v=22222222222", days_ago=2)
    _, _, p = _video(s, ch, "este", status="scheduled")
    assert end_screen_video(s, p) == {"title": "novo", "url": "https://youtube.com/watch?v=22222222222", "views": None, "why": "latest"}


# ---------------- Phase 8: OAuth + analytics ----------------

class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://x"), response=httpx.Response(self.status_code))


class FakeGoogle:
    """Token endpoint, YouTube Data channels/videos and Analytics reports."""

    def __init__(self, yt_id="UCxxxxxxxxxxxxxxxxxxxxxx"):
        self.yt_id, self.calls = yt_id, []

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, data))
        if data["grant_type"] == "authorization_code":
            return Resp({"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600})
        return Resp({"access_token": "at2", "expires_in": 3600})

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params))
        if url.endswith("/channels"):
            if params["part"] == "statistics":
                return Resp({"items": [{"statistics": {"subscriberCount": "321"}}]})
            return Resp({"items": [{"id": self.yt_id, "snippet": {"title": "Outro canal"}}]})
        if url.endswith("/videos"):
            return Resp({"items": [{"id": i, "snippet": {"title": f"t-{i}"}} for i in params["id"].split(",")]})
        days = (datetime.fromisoformat(params["endDate"]) - datetime.fromisoformat(params["startDate"])).days + 1
        if params.get("dimensions") == "video":
            return Resp({"columnHeaders": [{"name": n} for n in ("video", "views", "estimatedMinutesWatched", "averageViewPercentage", "subscribersGained")],
                         "rows": [["AAAAAAAAAAA", 5000, 9000, 31.5, 40], ["BBBBBBBBBBB", 800, 2000, 62.0, 9], ["CCCCCCCCCCC", 5, 4, 99.0, 0]]})
        if params.get("dimensions") == "creatorContentType":
            return Resp({"columnHeaders": [{"name": "creatorContentType"}, {"name": "estimatedMinutesWatched"}],
                         "rows": [["videoOnDemand", 60000], ["shorts", 30000]]})
        return Resp({"columnHeaders": [{"name": n} for n in ("views", "estimatedMinutesWatched", "subscribersGained")],
                     "rows": [[100 * days, 10 * days, days]]})


def test_connect_builds_a_pkce_consent_url_and_the_callback_keeps_only_the_refresh_token(s, tokens):
    ch = _ch(s, youtube_channel_id="UCxxxxxxxxxxxxxxxxxxxxxx")
    url = analytics.auth_url(s, ch, "http://127.0.0.1:8790/oauth/google/callback")
    q = parse_qs(urlparse(url).query)
    assert q["access_type"] == ["offline"] and q["code_challenge_method"] == ["S256"] and "yt-analytics.readonly" in q["scope"][0]
    analytics.finish(s, q["state"][0], "code", http=FakeGoogle())
    assert tokens == {"cvm-channel-%d" % ch.id: "rt1"} and ch.account_ref == f"cvm-channel-{ch.id}"
    with pytest.raises(analytics.NotConnected):  # a state is single use
        analytics.finish(s, q["state"][0], "code", http=FakeGoogle())


def test_callback_refuses_another_youtube_channel(s, tokens):
    ch = _ch(s, youtube_channel_id="UCaaaaaaaaaaaaaaaaaaaaaa")
    state = parse_qs(urlparse(analytics.auth_url(s, ch, "http://127.0.0.1:1/cb")).query)["state"][0]
    with pytest.raises(analytics.NotConnected, match="Outro canal"):
        analytics.finish(s, state, "code", http=FakeGoogle())
    assert tokens == {}


def test_stats_without_connection_are_nulls(s, tokens):
    ch = _ch(s)
    st = analytics.stats(s, ch)
    assert st["connected"] is False and st["views_7d"] is None and st["top_views"] is None
    assert st["monetization"] == {"hours_target": 4000, "subs_target": 1000, "deadline": "2027-01-31", "hours": None, "subs": None}
    assert st["suggestions"] == [] and st["title_pattern_suggestion"] is None


def test_refresh_pulls_numbers_tops_and_per_video_metrics(s, tokens):
    ch = _ch(s, account_ref="ref1")
    tokens["ref1"] = "rt"
    _, _, pub = _video(s, ch, "Meu GTA", url="https://youtube.com/watch?v=AAAAAAAAAAA", views=10)
    st = analytics.stats(s, ch, http=FakeGoogle())
    assert st["connected"] and st["views_7d"] == 700 and st["views_28d"] == 2800 and st["subscribers"] == 321
    assert st["watch_hours_365d"] == 1000.0  # 60,000 min of long videos; Shorts do not count for YPP
    assert st["top_views"] == {"title": "Meu GTA", "url": "https://youtube.com/watch?v=AAAAAAAAAAA", "views": 5000}
    assert st["top_retention"]["title"] == "t-BBBBBBBBBBB" and st["top_retention"]["avg_view_pct"] == 62.0  # 5-view video ignored
    assert st["monetization"]["hours"] == 1000.0 and st["monetization"]["subs"] == 321
    s.refresh(pub)
    assert pub.metrics["analytics"]["views"] == 5000 and pub.metrics["viewCount"] == 10


def test_a_broken_refresh_keeps_the_last_numbers(s, tokens):
    ch = _ch(s, account_ref="ref1")
    tokens["ref1"] = "rt"
    s.add(Setting(key=analytics.stats_key(ch.id), value={"views_7d": 5, "updated_at": (now() - timedelta(days=1)).isoformat()}))
    s.commit()

    class Down(FakeGoogle):
        def post(self, *a, **k):
            return Resp({}, 400)

    st = analytics.stats(s, ch, http=Down())
    assert st["connected"] and st["views_7d"] == 5 and "conecte de novo" in st["error"]


# ---------------- weekly cycle ----------------

def test_due_from_monday_8_dubai_once_a_week(s):
    dubai = ZoneInfo("Asia/Dubai")  # the tests' studio timezone (conftest)
    sunday = datetime(2026, 11, 15, 23, 0, tzinfo=dubai)
    monday_7 = datetime(2026, 11, 16, 7, 59, tzinfo=dubai)
    monday_8 = datetime(2026, 11, 16, 8, 1, tzinfo=dubai)
    assert not weekly.due(s, sunday) and not weekly.due(s, monday_7) and weekly.due(s, monday_8)
    assert weekly.due(s, datetime(2026, 11, 16, 4, 30, tzinfo=UTC))  # 08:30 in Dubai, worker in UTC
    weekly.mark_done(s, monday_8)
    assert not weekly.due(s, monday_8 + timedelta(hours=3))  # done this week
    assert not weekly.due(s, monday_8 + timedelta(days=2))  # not Monday
    assert weekly.due(s, monday_8 + timedelta(days=7))


def _fake_claude(s, items=6, pattern="<N> coisas que ninguém te conta sobre <tema>"):
    prompts = []

    def claude(prompt, timeout=None):
        prompts.append(prompt)
        a = s.exec(select(TrendAnalysis).order_by(TrendAnalysis.id.desc())).first()
        a.items = [{"topic": f"ideia {i}", "why": f"de {i}", "style": "explainer", "hooks": {"pt-BR": f"gancho {i}"},
                    "channel_id": a.channel_id, "trend_ids": []} for i in range(items)]
        a.summary, a.status = f"Semana boa.\nTITLE_PATTERN: {pattern}", "done"
        s.add(a)
        s.commit()
        return {"result": "ok"}

    return claude, prompts


def test_weekly_cycle_saves_ideas_and_a_pending_title_pattern_and_skips_unconnected(s, tokens):
    ch = _ch(s, account_ref="ref1", profile={"title_patterns": ["velho"]})
    other = _ch(s, name="sem login")
    tokens["ref1"] = "rt"
    claude, prompts = _fake_claude(s)
    assert weekly.run_all(s, claude=claude, http=FakeGoogle()) == [ch.id]
    assert len(prompts) == 1 and "t-BBBBBBBBBBB" in prompts[0] and "TITLE_PATTERN" in prompts[0]
    a = s.exec(select(TrendAnalysis).where(TrendAnalysis.channel_id == ch.id)).one()
    assert a.status == "done" and a.summary == "Semana boa."
    assert not s.exec(select(TrendAnalysis).where(TrendAnalysis.channel_id == other.id)).first()
    st = analytics.stats(s, ch)
    assert len(st["suggestions"]) == 6 and st["suggestions"][0] == {"title": "gancho 0", "why": "de 0", "topic": "ideia 0", "project_id": None}
    assert st["title_pattern_suggestion"] == {"pattern": "<N> coisas que ninguém te conta sobre <tema>", "status": "pending"}
    weekly.decide_pattern(s, ch, "approve")
    assert ch.profile["title_patterns"] == ["velho", "<N> coisas que ninguém te conta sobre <tema>"]
    assert weekly.pattern(s, ch)["status"] == "approved"
    with pytest.raises(LookupError):
        weekly.decide_pattern(s, ch, "reject")


def test_weekly_cycle_marks_a_silent_agent_as_failed(s, tokens):
    ch = _ch(s, account_ref="ref1")
    tokens["ref1"] = "rt"
    a = weekly.run_channel(s, ch, claude=lambda p, timeout=None: {"result": "nada"}, http=FakeGoogle())
    assert a.status == "failed" and weekly.pattern(s, ch) is None


def test_stats_routes(s, tokens, monkeypatch):
    from fastapi.testclient import TestClient

    from cvm.db import get_session
    from cvm.main import app

    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    ch = _ch(s)
    app.dependency_overrides[get_session] = lambda: s
    try:
        c = TestClient(app)
        assert c.get(f"/channels/{ch.id}/stats").json()["connected"] is False
        auth = c.post(f"/channels/{ch.id}/stats/connect").json()["auth_url"]
        assert "redirect_uri=http%3A%2F%2F127.0.0.1" in auth and "%2Foauth%2Fgoogle%2Fcallback" in auth
        assert c.post(f"/channels/{ch.id}/title-patterns/approve").status_code == 409
        assert c.post(f"/channels/{ch.id}/title-patterns/maybe").status_code == 422
        assert c.get("/oauth/google/callback?error=access_denied").status_code == 400
        assert c.get(f"/channels/{ch.id}").json()["id"] == ch.id  # the channels CRUD still answers
    finally:
        app.dependency_overrides.clear()


def test_weekly_cycle_goes_through_the_agents_router(s, tokens, monkeypatch):
    from cvm.scripting import agents

    ch = _ch(s, account_ref="ref1")
    tokens["ref1"] = "rt"
    claude, prompts = _fake_claude(s)
    steps = []
    monkeypatch.setattr(agents, "run", lambda p, step, **kw: (steps.append(step), claude(p))[1])
    assert weekly.run_channel(s, ch, http=FakeGoogle()).status == "done" and steps == ["ideas"]
