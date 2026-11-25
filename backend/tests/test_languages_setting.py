"""Video languages setting: the public copy hides Arabic, the owner's install keeps it."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm.api.crud import crud_router
from cvm.config import Settings, settings
from cvm.db import get_session
from cvm.main import app as main_app
from cvm.models import Channel


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(crud_router(Channel, "/channels"))

    def sess():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    return TestClient(app)


def test_default_includes_arabic(monkeypatch):
    monkeypatch.delenv("CVM_VIDEO_LANGUAGES", raising=False)
    assert Settings(_env_file=None).video_languages == ["pt-BR", "en-US", "ar-AE"]


def test_env_comma_list(monkeypatch):
    monkeypatch.setenv("CVM_VIDEO_LANGUAGES", "pt-BR,en-US")
    assert Settings(_env_file=None).video_languages == ["pt-BR", "en-US"]


@pytest.mark.parametrize("empty", ["", "  ", ",", "[]"])
def test_empty_env_means_the_default(monkeypatch, empty):
    monkeypatch.setenv("CVM_VIDEO_LANGUAGES", empty)
    assert Settings(_env_file=None).video_languages == ["pt-BR", "en-US", "ar-AE"]


def test_endpoint_default_and_public(monkeypatch):
    # the setting itself, not whatever this copy's .env says (a public install's .env drops ar-AE)
    monkeypatch.delenv("CVM_VIDEO_LANGUAGES", raising=False)
    monkeypatch.setattr(settings, "video_languages", Settings(_env_file=None).video_languages)
    c = TestClient(main_app)
    body = c.get("/system/languages").json()
    assert "ar-AE" in body["video"] and body["ui"] == ["pt", "en"]
    monkeypatch.setattr(settings, "video_languages", ["pt-BR", "en-US"])
    assert "ar-AE" not in c.get("/system/languages").json()["video"]


def test_public_refuses_arabic_channel(client, monkeypatch):
    monkeypatch.setattr(settings, "video_languages", ["pt-BR", "en-US"])
    r = client.post("/channels", json={"name": "x", "platform": "youtube", "language": "ar-AE"})
    assert r.status_code == 422 and "ar-AE" in r.json()["detail"]
    ok = client.post("/channels", json={"name": "x", "platform": "youtube", "language": "pt-BR"})
    assert ok.status_code == 200
    r = client.patch(f"/channels/{ok.json()['id']}", json={"language": "ar-AE"})
    assert r.status_code == 422


def test_owner_keeps_existing_arabic(client, monkeypatch):
    monkeypatch.setattr(settings, "video_languages", ["pt-BR", "en-US", "ar-AE"])  # the owner's channel, made before
    r = client.post("/channels", json={"name": "ar", "platform": "youtube", "language": "ar-AE"})
    assert r.status_code == 200
    monkeypatch.setattr(settings, "video_languages", ["pt-BR", "en-US"])
    # an existing ar-AE channel can still be edited without changing its language
    assert client.patch(f"/channels/{r.json()['id']}", json={"name": "ar2", "language": "ar-AE"}).status_code == 200
