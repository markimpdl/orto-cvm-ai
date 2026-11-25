"""Editorial Shorts (friend's format, no avatar): header text and mode detection."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from cvm.api.projects import create_project, update_project
from cvm.models import Project
from cvm.render import compose, editorial as e


def test_mode_only_for_vertical_editorial():
    assert e.wanted("editorial", True) and not e.wanted("editorial", False) and not e.wanted("scenes", True)


def test_editorial_suppresses_avatar_in_every_ratio_and_skips_lipsync(tmp_path):
    project = SimpleNamespace(presenter="editorial", channel_id=None, avatar_id=1, languages=["pt-BR"])
    avatar_scenes = [{"index": 1, "visual": {"type": "avatar_full"}},
                     {"index": 2, "visual": {"type": "avatar_pip"}}]

    assert compose.editorial_mode(None, project)
    assert not e.wanted("editorial", False)  # no special horizontal layout
    assert compose._avatar_clips(None, project, "pt-BR", avatar_scenes, Path(tmp_path)) == {}
    assert compose.prepare_avatar(None, project) == {"pt-BR": []}


def test_header_from_script():
    k, h = e.header_text({"packaging": {"header": {"kicker": "gta 6", "headline": "O SPEED CHAMOU O *YAMAL*"}}})
    assert (k, h) == ("GTA 6", "O SPEED CHAMOU O *YAMAL*")


def test_header_fallback_from_title():
    k, h = e.header_text({"packaging": {"title": "🟡 Por que o Speed chamou o Yamal?"}}, "live do GTA 6")
    assert k == "GTA 6" and "🟡" not in h and h.count("*") == 2 and "*Yamal*?" in h


def test_project_api_accepts_editorial_and_rejects_unknown_presenter():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Project(id=1, title="Editorial", topic="Editorial", avatar_id=1))
        s.commit()

        out = update_project(1, {"presenter": "editorial"}, s)
        assert out.presenter == "editorial" and out.avatar_id == 1

        with pytest.raises(HTTPException) as exc:
            update_project(1, {"presenter": "unknown"}, s)
        assert exc.value.status_code == 422


def test_project_post_rejects_unknown_presenter_before_creating_any_work():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s, pytest.raises(HTTPException) as exc:
        create_project({"title": "bad", "topic": "bad", "presenter": "unknown"}, s)
    assert exc.value.status_code == 422
