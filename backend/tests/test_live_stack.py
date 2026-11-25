"""Phase 6: "melhores momentos da live" layout — streamer camera on top, game below (cvm/render/stack.py)."""
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm.config import settings
from cvm.db import get_session
from cvm.models import Asset, Channel, Project
from cvm.render import stack

FC = {"x": 0.75, "y": 0.0, "w": 0.25, "h": 1 / 3}


def _source(path):
    """640x360 red "game" with a blue "camera" in the top-right quarter (x 480–640, y 0–120)."""
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x360:r=25:d=2",
                    "-vf", "drawbox=x=480:y=0:w=160:h=120:color=blue:t=fill", "-pix_fmt", "yuv420p", str(path)],
                   check=True)
    return path


def _pixel(video, x, y, W, H):
    raw = subprocess.run([settings.ffmpeg_bin, "-v", "error", "-i", str(video), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    assert len(raw) == W * H * 3
    i = (y * W + x) * 3
    return tuple(raw[i:i + 3])


def test_validate_facecam():
    assert stack.validate({"x": "0.1", "y": 0, "w": 0.3, "h": 0.25}) == {"x": 0.1, "y": 0.0, "w": 0.3, "h": 0.25}
    for bad in (None, {"x": 0.1}, {"x": -0.1, "y": 0, "w": 0.3, "h": 0.3}, {"x": 0.8, "y": 0, "w": 0.3, "h": 0.3},
                {"x": 0, "y": 0, "w": 0.01, "h": 0.3}, {"x": "a", "y": 0, "w": 0.3, "h": 0.3}):
        with pytest.raises(ValueError, match="facecam"):
            stack.validate(bad)


def test_only_live_projects_with_a_facecam_stack():
    ch = SimpleNamespace(prefs={"facecam": FC})
    live = SimpleNamespace(style="live")
    assert stack.wanted(live, ch, vertical=True, editorial=False) == stack.validate(FC)
    assert stack.wanted(live, SimpleNamespace(prefs={}), True, False) is None  # no camera: the current layout
    assert stack.wanted(SimpleNamespace(style="react"), ch, True, False) is None
    assert stack.wanted(live, ch, True, editorial=True) is None  # the editorial header logic stays as it is
    assert stack.wanted(live, ch, vertical=False, editorial=False) is None  # 16:9 only when asked
    assert stack.wanted(live, SimpleNamespace(prefs={"facecam": FC, "facecam_landscape": True}), False, False)
    assert stack.wanted(live, SimpleNamespace(prefs={"facecam": {"x": 2}}), True, False) is None


def test_vertical_stack_puts_the_camera_on_top_and_the_game_below(tmp_path):
    src = _source(tmp_path / "live.mp4")
    W, H = 270, 480
    out = stack.clip(src, 1.0, W, H, 25, tmp_path / "out.mp4", stack.validate(FC))
    top = stack.top_height(stack.validate(FC), W, H, 640, 360)
    assert 0.28 * H <= top <= 0.45 * H
    r, g, b = _pixel(out, W // 2, top // 2, W, H)
    assert b > 150 and r < 100  # the camera fills the top
    r, g, b = _pixel(out, W - 5, top + (H - top) // 2, W, H)
    assert r > 150 and b < 100  # the game window slid away from the camera: no blue in the game panel
    r, g, b = _pixel(out, W // 2, top, W, H)
    assert r > 150 and g > 150 and b < 100  # the accent line between them


def test_landscape_inset_keeps_the_whole_game(tmp_path):
    src = _source(tmp_path / "live.mp4")
    W, H = 320, 180
    out = stack.clip(src, 1.0, W, H, 25, tmp_path / "out.mp4", stack.validate(FC))
    assert _pixel(out, 20, H - 20, W, H)[0] > 150  # game
    r, g, b = _pixel(out, W - 40, 25, W, H)
    assert b > 150  # camera enlarged in the top-right corner


def test_titles_move_into_the_game_panel():
    fc = stack.validate(FC)
    top = stack.top_height(fc, 1080, 1920)
    assert stack.text_fx(fc, 1080, 1920, "Title") == f"{{\\an8\\pos(540,{top + int(1920 * 0.03)})}}"
    assert "\\an5" in stack.text_fx(fc, 1080, 1920, "Hook")
    assert stack.text_fx(fc, 1920, 1080, "Title") == ""


@pytest.fixture
def api(tmp_path, monkeypatch):
    from cvm.api import channels

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Channel(id=4, name="Studio", platform="youtube", language="pt-BR", prefs={"music": True}))
        s.add(Project(id=1, title="t", topic="t", channel_id=4, style="live", languages=["pt-BR"], platforms=["youtube"]))
        s.commit()
    app = FastAPI()
    app.include_router(channels.router)

    def sess():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    return TestClient(app), engine


def test_facecam_api(api, tmp_path):
    c, engine = api
    assert c.get("/channels/4/facecam").json() == {"facecam": None, "landscape": False, "sources": []}
    assert c.get("/channels/4/facecam/frame").status_code == 404
    assert c.put("/channels/4/facecam", json={"facecam": {"x": 0.9, "y": 0, "w": 0.3, "h": 0.2}}).status_code == 422
    r = c.put("/channels/4/facecam", json={"facecam": FC, "landscape": True})
    assert r.status_code == 200 and r.json()["facecam"]["x"] == 0.75 and r.json()["landscape"] is True
    with Session(engine) as s:
        prefs = s.get(Channel, 4).prefs
        assert prefs["music"] is True and prefs["facecam"]["w"] == 0.25  # other prefs kept
        s.add(Asset(project_id=1, kind="user_media", path=str(_source(tmp_path / "rec.mp4")), meta={"kind": "video"}))
        s.commit()
    got = c.get("/channels/4/facecam").json()
    assert got["sources"] == [{"index": 0, "name": "rec.mp4"}]
    frame = c.get("/channels/4/facecam/frame?source=0&at=0.5")
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg" and frame.content[:2] == b"\xff\xd8"
    assert c.put("/channels/4/facecam", json={"facecam": None}).json()["facecam"] is None
