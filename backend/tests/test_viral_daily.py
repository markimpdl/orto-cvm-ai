"""Daily viral research (07:00 Asia/Dubai) per channel with topics, within a credit budget; API to start/list."""
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm.grow import daily
from cvm.models import Channel, Setting


def _s(prefs):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    s.add(Channel(id=4, name="Studio", platform="youtube", language="pt-BR", prefs=prefs))
    s.commit()
    return s, engine


AT_8_DUBAI = datetime(2026, 11, 22, 4, 0, tzinfo=UTC)  # 08:00 in Dubai
AT_6_DUBAI = datetime(2026, 11, 22, 2, 0, tzinfo=UTC)  # 06:00 in Dubai


def test_daily_runs_once_per_day_and_respects_budget():
    s, _ = _s({"viral": {"topics": ["GTA 6", "GTA 6 PC", "Lisboa", "IA"], "daily_credits": 40}})
    c = s.get(Channel, 4)
    done = []
    fake = lambda topic, language: done.append(topic) or {"topic": topic, "items": []}  # noqa: E731
    assert daily.due(s, c, AT_6_DUBAI) is False
    assert daily.due(s, c, AT_8_DUBAI) is True
    daily.run_channel(s, c, search=fake, now=AT_8_DUBAI)
    assert done == ["GTA 6", "GTA 6 PC"]  # 40 credits ÷ ~15 per search
    assert daily.due(s, c, AT_8_DUBAI) is False  # once a day
    assert len(s.get(Setting, "viral:4").value["searches"]) == 2


def test_channels_without_topics_are_not_due():
    s, _ = _s({})
    assert daily.due(s, s.get(Channel, 4), AT_8_DUBAI) is False


@pytest.fixture
def client(monkeypatch):
    from cvm.api import grow
    from cvm.db import get_session
    s, engine = _s({})
    app = FastAPI()
    app.include_router(grow.router)

    def sess():
        with Session(engine) as x:
            yield x
    app.dependency_overrides[get_session] = sess
    started = []
    monkeypatch.setattr(grow, "_in_background", lambda fn, *a: started.append(a))
    return TestClient(app), started


def test_viral_api_settings_start_and_busy(client):
    c, started = client
    r = c.put("/grow/4/viral/settings", json={"topics": ["GTA 6"], "language": "pt"})
    assert r.json()["topics"] == ["GTA 6"]
    assert c.post("/grow/4/viral", json={"topic": "GTA 6"}).json() == {"started": True}
    assert started and started[0][1] == "GTA 6"
    assert c.post("/grow/4/viral", json={"topic": "GTA 6"}).status_code == 409  # one search at a time
    body = c.get("/grow/4/viral").json()
    assert body["running"] is True and body["searches"] == []


def test_topic_can_set_its_own_language():
    s, _ = _s({"viral": {"topics": ["GTA 6", "IShowSpeed GTA 6 | en"], "language": "pt", "daily_credits": 40}})
    calls = []
    daily.run_channel(s, s.get(Channel, 4), search=lambda t, l: calls.append((t, l)) or {"topic": t, "items": []}, now=AT_8_DUBAI)
    assert calls == [("GTA 6", "pt"), ("IShowSpeed GTA 6", "en")]


def test_daily_queues_cuts_of_the_best_suggestions(monkeypatch):
    s, _ = _s({"viral": {"topics": ["GTA 6"], "daily_credits": 40, "auto_cuts": 2}})
    s.get(Channel, 4).youtube_channel_id = "UCmine"
    s.commit()
    items = [{"video_id": f"v{i:010d}", "url": f"https://www.youtube.com/watch?v=v{i:010d}", "title": f"t{i}",
              "channel": "x", "vph": 1000.0 - i, "duration": 300.0} for i in range(4)]
    items.insert(0, {**items[0], "video_id": "longlonglon", "url": "https://www.youtube.com/watch?v=longlonglon",
                     "duration": 6 * 3600.0, "vph": 5000.0})  # too long to auto-cut
    queued = []
    monkeypatch.setattr(daily, "_queue_cut", lambda session, channel, item: queued.append(item["video_id"]) or True)
    daily.run_channel(s, s.get(Channel, 4), search=lambda t, l: {"topic": t, "items": items}, now=AT_8_DUBAI)
    assert queued == ["v0000000000", "v0000000001"]


def test_auto_cut_skips_already_done(monkeypatch):
    from cvm.lives import runs
    s, _ = _s({"viral": {"topics": ["GTA 6"], "auto_cuts": 1}})
    c = s.get(Channel, 4)
    runs.start_item(s, c, "v0000000000")
    item = {"video_id": "v0000000000", "url": "https://www.youtube.com/watch?v=v0000000000", "title": "t", "duration": 60.0}
    started = []
    import cvm.api.grow as grow
    monkeypatch.setattr(grow, "_in_background", lambda fn, *a: started.append(a))
    assert daily._queue_cut(s, c, item) is False and started == []


def test_research_api_start_and_busy(client):
    c, started = client
    assert c.post("/grow/4/research", json={"topic": "GTA 6"}).json() == {"started": True}
    assert started and started[0][1] == "GTA 6"
    assert c.post("/grow/4/research", json={"topic": "GTA 6"}).status_code == 409
    body = c.get("/grow/4/research").json()
    assert body["running"] is True and body["reports"] == []


def test_topics_rotate_when_the_budget_is_short():
    from datetime import timedelta
    topics = ["A", "B", "C", "D"]
    s, _ = _s({"viral": {"topics": topics, "daily_credits": 45, "auto_cuts": 0}})
    c = s.get(Channel, 4)
    days = []
    for d in range(2):
        done = []
        daily.run_channel(s, c, search=lambda t, l: done.append(t) or {"topic": t, "items": []}, now=AT_8_DUBAI + timedelta(days=d))
        days.append(done)
    assert days == [["A", "B", "C"], ["D", "A", "B"]]


def test_grow_jobs_wait_for_a_slot(monkeypatch):
    """At most GROW_SLOTS link/upload jobs run at once; the rest are rescheduled instead of holding worker threads."""
    import cvm.workers.tasks as tasks
    later = []
    monkeypatch.setattr(tasks.grow_job, "schedule", lambda args, delay: later.append((args, delay)))
    for _ in range(tasks.GROW_SLOTS):
        assert tasks._grow_slots.acquire(blocking=False)
    try:
        tasks.grow_job.call_local("_job_link", [4, "https://youtu.be/x", None])
        assert later and later[0][1] > 0
    finally:
        for _ in range(tasks.GROW_SLOTS):
            tasks._grow_slots.release()
