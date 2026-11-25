"""Install wizard state and /setup API."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm.api import setup as setup_api
from cvm.db import get_session
from cvm.models import Channel, Setting
from cvm.setup import state
from cvm.setup.requirements import Hardware

MAC32 = Hardware("mac", "Apple M4", 32, 0, 500)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from cvm.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(setup_api.router)

    def sess():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    return TestClient(app), engine, tmp_path


def test_fresh_db_not_done(env):
    _, engine, _ = env
    with Session(engine) as s:
        assert state.get(s) == {"done": False, "step": "system", "choices": {}}


def test_existing_channels_count_as_done(env):
    _, engine, tmp = env
    with Session(engine) as s:
        s.add(Channel(id=1, name="X", platform="youtube"))
        s.commit()
        assert state.get(s) == {"done": True, "legacy": True}
    assert json.loads((tmp / "setup.json").read_text()) == {"done": True}


def test_save_merges_choices(env):
    c, _, _ = env
    c.put("/setup", json={"step": "language", "choices": {"language": "pt-BR"}})
    r = c.put("/setup", json={"step": "ai", "choices": {"ai": "claude"}}).json()
    assert r["step"] == "ai" and r["choices"] == {"language": "pt-BR", "ai": "claude"}
    assert c.get("/setup").json()["state"]["choices"]["language"] == "pt-BR"


def test_get_reports_blockers(env, monkeypatch):
    c, _, _ = env
    monkeypatch.setattr(setup_api, "detect", lambda: MAC32)
    c.put("/setup", json={"step": "features", "choices": {"features": ["base", "avatar"]}})
    body = c.get("/setup").json()
    assert len(body["blockers"]) == 1 and body["blockers"][0]["feature"] == "avatar"
    assert body["hardware"]["ram_gb"] == 32 and "avatar" in body["features"]


def test_unknown_saved_feature_does_not_500(env, monkeypatch):
    c, _, _ = env
    monkeypatch.setattr(setup_api, "detect", lambda: MAC32)
    c.put("/setup", json={"step": "features", "choices": {"features": ["base", "nope"]}})
    assert c.get("/setup").status_code == 200


def test_finish_mirrors_to_file(env):
    c, engine, tmp = env
    assert json.loads((tmp / "setup.json").read_text() if (tmp / "setup.json").exists() else '{"done": false}') == {"done": False}
    r = c.post("/setup/finish").json()
    assert r["done"] is True
    assert json.loads((tmp / "setup.json").read_text()) == {"done": True}
    with Session(engine) as s:
        assert s.get(Setting, "setup").value["done"] is True


def test_save_mirrors_done_false(env):
    c, _, tmp = env
    c.put("/setup", json={"step": "ai", "choices": {}})
    assert json.loads((tmp / "setup.json").read_text()) == {"done": False}


def _item(state, size):
    from cvm.setup.install import Item
    return Item("model", f"m{size}{state}", size, state)


def test_disk_blocker_counts_only_missing_items(env, monkeypatch):
    from cvm.setup import install
    c, _, _ = env
    monkeypatch.setattr(setup_api, "detect", lambda: Hardware("mac", "M5", 48, 0, 51))
    c.put("/setup", json={"step": "models", "choices": {"features": ["base", "avatar", "animation", "kids"]}})
    # everything already on this machine: no disk blocker, need 0
    monkeypatch.setattr(install, "plan", lambda f, p: [_item("ready", 75), _item("ready", 9)])
    body = c.get("/setup").json()
    assert body["blockers"] == [] and body["disk"]["need_gb"] == 0
    # partial: 18 GB missing + 20 reserve fits in 51 GB
    monkeypatch.setattr(install, "plan", lambda f, p: [_item("ready", 75), _item("missing", 11), _item("failed", 7)])
    body = c.get("/setup").json()
    assert body["blockers"] == [] and body["disk"] == {"need_gb": 18.0, "free_gb": 51.0, "reserve_gb": 20}
    # 35 GB missing + 20 > 51: blocked, with the same number in the reason
    monkeypatch.setattr(install, "plan", lambda f, p: [_item("missing", 35)])
    body = c.get("/setup").json()
    assert [b["feature"] for b in body["blockers"]] == ["disk"] and "35 GB" in body["blockers"][0]["reason"]
    assert body["disk"]["need_gb"] == 35


def test_disk_falls_back_to_estimate_when_plan_breaks(env, monkeypatch):
    from cvm.setup import install
    c, _, _ = env
    monkeypatch.setattr(setup_api, "detect", lambda: Hardware("mac", "M5", 48, 0, 51))
    c.put("/setup", json={"step": "models", "choices": {"features": ["base", "avatar"]}})

    def boom(f, p):
        raise RuntimeError("probe broke")
    monkeypatch.setattr(install, "plan", boom)
    body = c.get("/setup").json()
    assert body["disk"]["need_gb"] is None and [b["feature"] for b in body["blockers"]] == ["disk"]
