"""/grow API: lives settings/status/check/upload and the monetization numbers with the current pace."""
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm.api import grow
from cvm.db import get_session
from cvm.models import Channel


@pytest.fixture
def client(tmp_path, monkeypatch):
    from cvm.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Channel(id=4, name="Studio", platform="youtube", youtube_channel_id="UCabc"))
        s.commit()
    app = FastAPI()
    app.include_router(grow.router)

    def sess():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    return TestClient(app), engine


def test_lives_listing(client):
    c, _ = client
    assert c.get("/grow/4/lives").json() == {"items": []}
    assert c.get("/grow/99/lives").status_code == 404


def test_link_starts_in_background_and_refuses_repeats(client, monkeypatch):
    c, _ = client
    started = []
    monkeypatch.setattr(grow, "_in_background", lambda fn, *a: started.append(a))
    url = "https://www.youtube.com/watch?v=VNrA2RUoRKw"
    assert c.post("/grow/4/lives/link", json={"url": url}).json() == {"started": True, "video_id": "VNrA2RUoRKw"}
    assert started and started[0][1] == url
    assert c.post("/grow/4/lives/link", json={"url": url}).status_code == 409  # already being processed
    assert c.post("/grow/4/lives/link", json={"url": "nada"}).status_code == 422


def test_link_refused_on_paused_channel(client, monkeypatch):
    c, engine = client
    with Session(engine) as s:
        ch = s.get(Channel, 4)
        ch.status = "paused"
        s.add(ch)
        s.commit()
    monkeypatch.setattr(grow, "_in_background", lambda fn, *a: None)
    assert c.post("/grow/4/lives/link", json={"url": "https://youtu.be/VNrA2RUoRKw"}).status_code == 409


def test_pace_math():
    p = grow.pace({"minutes_28d": 1680, "subs_gained_28d": 28},
                  {"hours": 18.0, "hours_target": 4000, "subs": 504, "subs_target": 1000})
    assert p == {"hours_per_day": 1.0, "subs_per_day": 1.0, "days_to_hours": 3982, "days_to_subs": 496}


def test_pace_without_numbers():
    assert grow.pace({}, {"hours": None, "hours_target": 4000, "subs": None, "subs_target": 1000}) == {
        "hours_per_day": None, "subs_per_day": None, "days_to_hours": None, "days_to_subs": None}


def test_monetization_without_analytics(client):
    c, _ = client
    r = c.get("/grow/4/monetization").json()
    assert r["connected"] is False and r["pace"]["days_to_hours"] is None and r["hours_target"] == 4000
