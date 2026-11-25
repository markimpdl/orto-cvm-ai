"""'Avaliar com vidIQ' before publishing: title score, scored alternative titles, tags/description words; never writes."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm.grow import vidiq_review as vr
from cvm.models import Channel, Project, Render

ANSWER = json.dumps({"title_score": 62, "titles": [{"title": "Fugi de um TUBARÃO no GTA 6", "score": 78},
                                                   {"title": "GTA 6: o susto", "score": 55}],
                     "thumb_score": None, "thumb_feedback": None, "tags": ["gta 6", "gta 6 tubarão"],
                     "description_keywords": ["gta 6 gameplay"]})


def _render(profile="youtube"):
    return Render(id=1, project_id=1, language="pt-BR", profile=profile, seo={"title": "Live GTA 6", "description": "d"})


def test_review_parses_answer_and_asks_for_titles():
    prompts = []
    out = vr.review(_render(), Channel(id=4, name="M", platform="youtube", youtube_channel_id="UCabc"),
                    run=lambda p, **kw: prompts.append((p, kw)) or {"result": "```json\n" + ANSWER + "\n```"})
    assert out["title_score"] == 62 and out["titles"][0]["score"] == 78 and out["tags"] == ["gta 6", "gta 6 tubarão"]
    p, kw = prompts[0]
    assert "vidiq_score_title" in p and "vidiq_generate_titles" in p and "Live GTA 6" in p
    assert all("update" not in t for t in kw["allowed_tools"])  # never writes to YouTube


def test_short_skips_thumbnail():
    prompts = []
    vr.review(_render("youtube_shorts"), Channel(id=4, name="M", platform="youtube"),
              run=lambda p, **kw: prompts.append(p) or {"result": ANSWER})
    assert "vidiq_score_thumbnail" not in prompts[0] and '"type": "short"' in prompts[0]


@pytest.fixture
def client(monkeypatch):
    from cvm.api import grow
    from cvm.db import get_session
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Channel(id=4, name="M", platform="youtube"))
        s.add(Project(id=1, title="t", topic="t", channel_id=4))
        s.add(_render())
        s.commit()
    app = FastAPI()
    app.include_router(grow.router)

    def sess():
        with Session(engine) as x:
            yield x
    app.dependency_overrides[get_session] = sess
    started = []
    monkeypatch.setattr(grow, "_in_background", lambda fn, *a: started.append(a))
    return TestClient(app), started


def test_endpoint_starts_and_refuses_while_running(client):
    c, started = client
    assert c.post("/grow/renders/1/vidiq").json() == {"started": True}
    assert started
    assert c.post("/grow/renders/1/vidiq").status_code == 409
    assert c.post("/grow/renders/99/vidiq").status_code == 404
