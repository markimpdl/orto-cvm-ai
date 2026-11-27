"""Channel page "Padrões de publicação" (owner, 2026-11-26): the YouTube upload choices, editable per channel."""
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm.db import get_session
from cvm.main import app
from cvm.models import Channel
from cvm.publish.platforms import youtube_rules


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Channel(id=4, name="Canal", platform="youtube", language="pt-BR"))
        s.commit()

    def sess():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    return TestClient(app), engine


def test_defaults_follow_the_owner_rules_and_save():
    c, engine = _client()
    try:
        d = c.get("/channels/4/publish-defaults").json()
        assert d["synthetic"] == "auto" and d["paid_promotion"] is False and d["auto_chapters"] is False
        assert d["notify_subscribers"] is False and d["made_for_kids"] is False and d["category"] == "auto"
        d = c.put("/channels/4/publish-defaults", json={"auto_chapters": True, "synthetic": "no", "category": "20",
                                                         "made_for_kids": False}).json()
        assert d["auto_chapters"] is True and d["synthetic"] == "no" and d["category"] == "20"
        with Session(engine) as s:
            ch = s.get(Channel, 4)
            names = [n for n, _ in youtube_rules(ch)["off_boxes"]]
            assert "Capítulos automáticos" not in names and len(names) == 2
            assert ch.prefs["youtube_category_fixed"] is True
        assert c.put("/channels/4/publish-defaults", json={"category": "auto"}).json()["category"] == "auto"
        assert c.put("/channels/4/publish-defaults", json={"synthetic": "x"}).status_code == 422
    finally:
        app.dependency_overrides.clear()
