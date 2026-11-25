"""Install wizard features step: regions, menus, optional keys in the keystore."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from cvm import config, keystore
from cvm.api import setup as setup_api
from cvm.config import settings
from cvm.db import get_session
from cvm.models import Setting
from cvm.setup import features


@pytest.fixture
def s():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with Session(e) as session:
        yield session


@pytest.fixture
def client(s):
    app = FastAPI()
    app.include_router(setup_api.router)
    app.dependency_overrides[get_session] = lambda: s
    return TestClient(app)


def test_regions_default_unchanged_without_setting(s):
    assert features.regions(s) == ["BR", "AE", "US"]
    assert features.get(s)["avatars"] is True


def test_regions_brasil_only(s, client):
    assert client.put("/setup/features", json={"regions": ["BR"]}).status_code == 200
    assert features.regions(s) == ["BR"]
    client.put("/setup/features", json={"regions": ["us", "GLOBAL"]})
    assert features.regions(s) == ["GLOBAL"]
    assert client.put("/setup/features", json={"regions": []}).status_code == 422


def test_collect_uses_chosen_regions(s, monkeypatch):
    from cvm.trends import collect

    features.put(s, {"regions": ["BR"]})
    monkeypatch.setattr(collect, "engine", s.get_bind())
    seen = []
    monkeypatch.setattr(collect, "FETCHERS", {"x": lambda region, c: seen.append(region) or []})
    collect.fetch_all()
    assert seen == ["BR"]


def test_keys_go_to_keystore_not_db(s, client, monkeypatch):
    stored = {}
    monkeypatch.setattr(keystore, "_store_set", lambda n, v: stored.__setitem__(n, v))
    monkeypatch.setattr(keystore, "_store_get", lambda n: stored.get(n))
    secret = "pexels-secret-key-1234567890"
    r = client.put("/setup/keys", json={"pexels": secret, "twitch_id": "abcdefghijklmnopqrstuv"})
    assert r.status_code == 200 and r.json()["pexels"] is True and secret not in r.text
    assert stored["pexels_api_key"] == secret
    assert secret not in str(client.get("/setup/features").json())
    assert all(secret not in str(row.value) for row in s.exec(select(Setting)).all())
    monkeypatch.setattr(settings, "pexels_api_key", None)
    assert config.key("pexels_api_key") == secret
    monkeypatch.setattr(settings, "pexels_api_key", "from-env")
    assert config.key("pexels_api_key") == "from-env"
    assert client.put("/setup/keys", json={"pixabay": "short"}).status_code == 422


def test_twitch_credentials_read_keystore(monkeypatch):
    from cvm.trends import twitch

    vals = {"twitch_client_id": "id", "twitch_client_secret": "sec"}
    monkeypatch.setattr(settings, "twitch_client_id", None, raising=False)
    monkeypatch.setattr(settings, "twitch_client_secret", None, raising=False)
    monkeypatch.setattr(keystore, "_store_get", lambda n: vals.get(n))
    monkeypatch.setattr(keystore, "_cache", {})
    assert twitch.credentials() == ("id", "sec")


def test_uncheck_keeps_files(s, client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    f = tmp_path / "avatars" / "1" / "photo.png"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    assert client.put("/setup/features", json={"avatars": False}).json()["avatars"] is False
    assert f.read_bytes() == b"x"
    assert features.get(s)["avatars"] is False


def test_x_trends_unknown_country_uses_worldwide(monkeypatch):
    from cvm.trends import sources

    urls = []

    class R:
        text = ""
        def raise_for_status(self): pass

    class C:
        def get(self, url, **k):
            urls.append(url)
            return R()

    assert sources.x_trends("FR", C()) == []
    assert urls == ["https://trends24.in/"]
    sources.x_trends("BR", C())
    assert urls[-1] == "https://trends24.in/brazil/"


def test_other_sources_accept_unknown_region():
    from cvm.trends import sources

    urls = []

    class R:
        text = "<rss></rss>"
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"items": []}

    class C:
        def get(self, url, **k):
            urls.append(url)
            return R()

    for fn in (sources.google_trends, sources.google_news, sources.reddit_popular):
        assert fn("FR", C()) == []
    assert any("gl=FR" in u for u in urls)


def test_follow_keeps_global_and_other_regions():
    from cvm.trends import follow

    assert follow.clean({"region": "GLOBAL"})["region"] == "GLOBAL"
    assert follow.clean({"region": "fr"})["region"] == "FR"
    assert follow.clean({"region": "xx1"})["region"] == "BR"
    assert follow.clean({})["region"] == "BR"


def test_bad_key_saves_nothing(client, monkeypatch):
    stored = {}
    monkeypatch.setattr(keystore, "_store_set", lambda n, v: stored.__setitem__(n, v))
    r = client.put("/setup/keys", json={"pexels": "pexels-secret-key-1234567890", "pixabay": "short"})
    assert r.status_code == 422 and stored == {}


def test_ai_ideas_on_when_row_null_or_broken():
    """A null value or an unreadable features row never blocks idea runs."""
    from types import SimpleNamespace

    from cvm.setup import features

    null = SimpleNamespace(get=lambda model, key: SimpleNamespace(value={"ai_ideas": None}))
    assert features.ai_ideas(null) is True
    broken = SimpleNamespace(get=lambda model, key: SimpleNamespace(value=["not", "a", "dict", 1]))
    assert features.ai_ideas(broken) is True
    off = SimpleNamespace(get=lambda model, key: SimpleNamespace(value={"ai_ideas": False}))
    assert features.ai_ideas(off) is False
