"""Em alta v2 (phase 4): Twitch/Kick parsers (sample JSON, no network), global discover without channel or topic,
Seguindo (clean + dedupe against existing trends), the vidIQ credit guard, and the /trends/* endpoints."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from cvm.api import em_alta
from cvm.db import get_session
from cvm.grow import discover
from cvm.models import Channel, Setting, Trend
from cvm.trends import current, follow, kick, twitch

TWITCH = {"data": [
    {"id": "4101", "user_id": "1", "user_login": "gaules", "user_name": "Gaules", "game_id": "32982",
     "game_name": "Grand Theft Auto V", "type": "live", "title": "GTA RP hoje", "viewer_count": 41000,
     "started_at": "2026-11-24T10:00:00Z", "language": "pt",
     "thumbnail_url": "https://static-cdn.jtvnw.net/previews-ttv/live_user_gaules-{width}x{height}.jpg"},
    {"id": "4102", "user_login": "", "type": "live", "title": "sem login"},
    {"id": "4103", "user_login": "x", "type": "", "title": "rerun", "viewer_count": 5},
], "pagination": {}}

KICK_LIST = {"current_page": 1, "data": [
    {"id": 139768761, "session_title": "بث 30 يوم", "is_live": True, "language": "Arabic", "viewer_count": 196162,
     "thumbnail": {"src": "https://images.kick.com/a.webp"}, "start_time": "2026-11-23 09:55:27",
     "channel": {"slug": "sxb", "user": {"username": "SXB"}}, "categories": [{"name": "Grand Theft Auto V (GTA)"}]},
    {"id": 2, "session_title": "GTA 6 trailer react", "is_live": True, "language": "Portuguese", "viewer_count": 900,
     "thumbnail": {"src": "https://images.kick.com/b.webp"}, "channel": {"slug": "gaules", "user": {"username": "Gaules"}},
     "categories": [{"name": "Just Chatting"}]},
    {"id": 3, "session_title": "offline", "is_live": False, "language": "Portuguese",
     "channel": {"slug": "off", "user": {}}},
]}

KICK_CHANNEL = {"slug": "gaules", "user": {"username": "Gaules"},
                "livestream": {"id": 9, "session_title": "KINGS CUP", "is_live": True, "viewer_count": 69286,
                               "language": "Portuguese", "thumbnail": {"url": "https://k/t.webp"},
                               "categories": [{"name": "Kings League"}]}}


def test_twitch_parse_streams():
    items = twitch.parse_streams(TWITCH)
    assert len(items) == 1  # no login / not live → dropped
    it = items[0]
    assert it["platform"] == "twitch" and it["kind"] == "live" and it["url"] == "https://www.twitch.tv/gaules"
    assert it["viewers"] == 41000 and it["game"] == "Grand Theft Auto V"
    assert it["thumb"].endswith("gaules-480x270.jpg")


def test_twitch_without_credentials_says_configure(monkeypatch):
    monkeypatch.setattr(twitch, "credentials", lambda: None)
    out = twitch.safe_top("gta", "BR")
    assert out["items"] == [] and out["configured"] is False and "Configure a Twitch" in out["error"]


def test_twitch_top_streams_uses_helix(monkeypatch):
    calls = []
    monkeypatch.setattr(twitch, "_headers", lambda client: {"Client-Id": "x"})

    def fake_get(client, path, params):
        calls.append((path, params))
        return {"data": []} if path in ("/games", "/search/categories") else TWITCH
    monkeypatch.setattr(twitch, "_get", fake_get)
    items = twitch.top_streams("", "BR", client=object())
    assert items[0]["handle"] == "gaules"
    assert ("language", "pt") in calls[0][1]
    # a topic that is no category filters the titles
    assert twitch.top_streams("futebol", "BR", client=object()) == []


def test_kick_parsers():
    items = kick.parse_livestreams(KICK_LIST)
    assert [i["handle"] for i in items] == ["sxb", "gaules"]  # offline dropped, ranked by viewers
    assert items[0]["platform"] == "kick" and items[0]["url"] == "https://kick.com/sxb"
    assert [i["handle"] for i in kick.parse_livestreams(KICK_LIST, region="BR")] == ["gaules"]
    assert [i["handle"] for i in kick.parse_livestreams(KICK_LIST, topic="gta 6")] == ["gaules"]
    live = kick.parse_channel(KICK_CHANNEL)
    assert live["title"] == "KINGS CUP" and live["viewers"] == 69286 and live["thumb"] == "https://k/t.webp"
    assert kick.parse_channel({"slug": "x", "livestream": None}) is None
    assert kick.parse_livestreams({"weird": True}) == []


def test_kick_fails_soft(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cloudflare")
    monkeypatch.setattr(kick, "top_streams", boom)
    out = kick.safe_top()
    assert out["items"] == [] and out["experimental"] and out["error"]


def yt(i, status=None, dur=None, views=None, viewers=None):
    return {"id": f"{i:011d}", "title": f"t{i}", "channel": "c", "live_status": status, "duration": dur,
            "view_count": views, "concurrent_view_count": viewers}


def test_discover_global_without_topic(monkeypatch):
    queries = []

    def fake_search(topic, sp, region, n=25):
        queries.append((topic, sp, region))
        return [yt(1, "is_live", viewers=5)] if sp == discover.SP["live"] else [yt(2, None, 600, views=9)]
    monkeypatch.setattr(discover, "_search", fake_search)
    monkeypatch.setattr(twitch, "safe_top", lambda topic, region, n=20: {"items": [], "error": "Configure", "configured": False})
    monkeypatch.setattr(kick, "safe_top", lambda topic, region, n=20: {"items": [{"id": "kick:1"}], "error": None,
                                                                 "experimental": True})
    d = discover.discover_global("", "BR")
    assert d["topic"] == "" and d["region"] == "BR"
    assert ("ao vivo", discover.SP["live"], "BR") in queries and ("brasil", discover.SP["week"], "BR") in queries
    assert d["live"][0]["platform"] == "youtube" and d["videos"][0]["views"] == 9
    assert d["twitch"]["configured"] is False and d["kick"]["items"]


def test_discover_global_survives_youtube_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("yt-dlp down")
    monkeypatch.setattr(discover, "discover", boom)
    monkeypatch.setattr(twitch, "safe_top", lambda topic, region, n=20: {"items": [{"id": "twitch:1"}], "error": None})
    monkeypatch.setattr(kick, "safe_top", lambda topic, region, n=20: {"items": [], "error": "x"})
    d = discover.discover_global("gta 6", "nowhere")
    assert d["region"] == "" and d["youtube_error"] and d["live"] == [] and d["twitch"]["items"]


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def test_follow_clean():
    v = follow.clean({"keywords": [" GTA  6 ", "gta 6", "x", "Copa"],
                      "channels": [{"platform": "twitch", "handle": "https://www.twitch.tv/Gaules/"},
                                   {"platform": "twitch", "handle": "@gaules"},
                                   {"platform": "kick", "handle": "kick.com/sxb"},
                                   {"platform": "youtube", "handle": "@CozinhaDaVila"},
                                   {"platform": "myspace", "handle": "x"}], "region": "123"})
    assert v["keywords"] == ["GTA 6", "Copa"]
    assert v["channels"] == [{"platform": "twitch", "handle": "gaules"}, {"platform": "kick", "handle": "sxb"},
                             {"platform": "youtube", "handle": "CozinhaDaVila"}]
    assert v["region"] == "BR"


def test_follow_dedupes_against_existing_trends(db):
    with Session(db) as s:
        s.add(Trend(source="google", region="BR", key="gta 6 trailer 3", title="GTA 6 Trailer 3"))
        s.add(Trend(source="youtube", region="BR", key="outro", title="Outro", url="https://www.youtube.com/watch?v=aaaaaaaaaaa"))
        s.commit()
        follow.put(s, {"keywords": ["gta 6"], "channels": [{"platform": "kick", "handle": "gaules"}]})
        found = [("palavra: gta 6", {"title": "GTA 6 — Trailer 3!", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"}),
                 ("palavra: gta 6", {"title": "Mesmo link", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}),
                 ("canal Kick: gaules", {"title": "KINGS CUP", "url": "https://kick.com/gaules", "platform": "kick",
                                         "viewers": 69286})]
        assert follow.tick(s, fetcher=lambda cfg: found) == 1
        assert follow.tick(s, fetcher=lambda cfg: found) == 0  # same match again: refreshed, not duplicated
        rows = s.exec(select(Trend).where(Trend.source == "follow")).all()
        assert [r.title for r in rows] == ["KINGS CUP"]
        assert rows[0].meta["followed"] == "canal Kick: gaules" and rows[0].meta["platform"] == "kick"
        assert follow.get(s)["last_run"]


def test_follow_tick_does_nothing_when_nothing_followed(db):
    with Session(db) as s:
        assert follow.tick(s, fetcher=lambda cfg: pytest.fail("fetched with nothing followed")) == 0


def test_current_keeps_follow_trends_on_their_own_clock(db):
    now = datetime.now(UTC)
    with Session(db) as s:
        s.add(Trend(source="google", region="BR", key="a", title="A", last_seen=now - timedelta(minutes=50)))
        s.add(Trend(source="google", region="BR", key="b", title="B", last_seen=now - timedelta(hours=3)))
        s.add(Trend(source="follow", region="BR", key="c", title="C", last_seen=now))
        s.add(Trend(source="follow", region="BR", key="d", title="D", last_seen=now - timedelta(hours=5)))
        s.commit()
        titles = sorted(t.title for t in s.exec(current(s, select(Trend))).all())
    assert titles == ["A", "C"]  # the fresh follow match neither hides collection A nor keeps the stale D


@pytest.fixture
def client(db, monkeypatch):
    with Session(db) as s:
        s.add(Channel(id=4, name="Studio", platform="youtube", youtube_channel_id="UCabc"))
        s.commit()
    app = FastAPI()
    app.include_router(em_alta.router)

    def sess():
        with Session(db) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    started = []
    monkeypatch.setattr(em_alta, "_in_background", lambda name, *a: started.append((name, a)))
    monkeypatch.setattr(twitch, "credentials", lambda: None)
    return TestClient(app), db, started


def test_research_needs_credit_confirmation(client):
    c, db, started = client
    r = c.post("/trends/research", json={"topic": "GTA 6"})
    assert r.status_code == 428 and r.json()["detail"]["credits"] == em_alta.RESEARCH_CREDITS
    assert started == []  # nothing ran, no credits spent
    assert c.get("/trends/research").json()["credits"] == em_alta.RESEARCH_CREDITS


def test_research_without_channel_and_one_at_a_time(client):
    c, db, started = client
    assert c.post("/trends/research", json={"topic": "GTA 6", "confirm_credits": True}).status_code == 200
    assert started == [("_job_research", ("GTA 6", "pt", None))]
    assert c.post("/trends/research", json={"topic": "Copa", "confirm_credits": True}).status_code == 409
    assert c.get("/trends/research").json()["running"] is True
    assert c.post("/trends/research", json={"topic": "x y", "channel_id": 99, "confirm_credits": True}).status_code == 404


def test_research_job_global_and_with_channel(db, monkeypatch):
    from cvm.grow import research
    seen = []
    monkeypatch.setattr(research, "report", lambda topic, lang, yt_id, audience=None: seen.append((yt_id, audience))
                        or {"topic": topic, "at": "now"})
    with Session(db) as s:
        s.add(Channel(id=4, name="Studio", platform="youtube", youtube_channel_id="UCabc"))
        s.commit()
        em_alta._job_research(s, "GTA 6", "pt", None)
        em_alta._job_research(s, "Copa", "pt", 4)
        v = s.get(Setting, "research:global").value
    assert seen[0][0] is None and "qualquer canal" in seen[0][1] and seen[1] == ("UCabc", "o canal “Studio”")
    assert [r["topic"] for r in v["reports"]] == ["Copa", "GTA 6"] and v["reports"][0]["channel_name"] == "Studio"
    assert v["running_since"] is None


def test_research_prompt_without_channel_skips_competitors():
    from cvm.grow import research
    seen = {}
    research.report("GTA 6", "pt", None, run=lambda p, **kw: seen.update(prompt=p) or {"result": '{"used_vidiq": false}'},
                    audience="um criador")
    assert "vidiq_list_competitors" not in seen["prompt"] and "um criador" in seen["prompt"]
    research.report("GTA 6", "pt", "UCabc", run=lambda p, **kw: seen.update(prompt=p) or {"result": "{}"})
    assert 'vidiq_list_competitors (channelId "UCabc")' in seen["prompt"]


def test_discover_endpoints(client):
    c, db, started = client
    g = c.get("/trends/discover").json()
    assert g["results"] == [] and g["twitch_configured"] is False
    assert c.post("/trends/discover", json={"region": "BR"}).json() == {"started": True}
    assert started == [("_job_discover", ("", "BR"))]
    assert c.post("/trends/discover", json={"topic": "gta"}).status_code == 409


def test_discover_job_keeps_last_results(db, monkeypatch):
    monkeypatch.setattr(discover, "discover_global", lambda topic, region: {"topic": topic, "region": region})
    with Session(db) as s:
        for t in ("a", "b", "a"):
            em_alta._job_discover(s, t, "BR")
        v = s.get(Setting, "discover:global").value
    assert [r["topic"] for r in v["results"]] == ["a", "b"] and v["running_since"] is None


def test_follow_endpoints(client):
    c, db, started = client
    assert c.get("/trends/follow").json()["keywords"] == []
    body = {"keywords": ["gta 6"], "channels": [{"platform": "twitch", "handle": "@Gaules"}], "region": "BR"}
    out = c.put("/trends/follow", json=body).json()
    assert out["channels"] == [{"platform": "twitch", "handle": "gaules"}]
    assert c.put("/trends/follow", json={"channels": [{"platform": "orkut", "handle": "x"}]}).status_code == 422
    assert c.post("/trends/follow/run").json() == {"started": True} and started[-1][0] == "_job_follow"
