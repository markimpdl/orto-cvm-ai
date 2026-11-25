"""3-panel Shorts of a streamer's video (owner, 2026-11-25): camera + header + gameplay (cvm/render/stack.py), the
final pass that puts the editorial header in its band (compose.header_graph) and the cover rule (no avatar on the
cover of a video without avatar; the streamer's reaction instead)."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from cvm.config import settings
from cvm.render import compose, stack, thumbnail

FC = {"x": 0.75, "y": 0.0, "w": 0.25, "h": 1 / 3}  # the blue box of _source


def _source(path, d=2):
    """640x360 red "game" with a blue "camera" in the top-right quarter (x 480–640, y 0–120)."""
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s=640x360:r=25:d={d}",
                    "-vf", "drawbox=x=480:y=0:w=160:h=120:color=blue:t=fill", "-pix_fmt", "yuv420p", str(path)],
                   check=True)
    return path


def _frame(video, W, H, at=0.0):
    raw = subprocess.run([settings.ffmpeg_bin, "-v", "error", "-ss", f"{at}", "-i", str(video), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    assert len(raw) == W * H * 3
    return np.frombuffer(raw, dtype=np.uint8).reshape(H, W, 3)


@pytest.mark.parametrize("order", ["youtuber_top", "header_top"])
def test_layout3_is_10_40_50(order):
    lay = stack.layout3(stack.validate(FC), order, 1080, 1920)
    assert lay["order"] == order
    assert sum(h for _, h in (lay["cam"], lay["header"], lay["game"])) == 1920
    assert lay["header"][1] == 192 and lay["cam"][1] == 768 and lay["game"] == (960, 960)
    first = "cam" if order == "youtuber_top" else "header"
    assert lay[first][0] == 0
    assert stack.layout3(stack.validate(FC), "nonsense", 1080, 1920)["order"] == "youtuber_top"


def test_three_panels_only_for_editorial_vertical_shorts_with_a_box():
    assert stack.three_wanted(True, True, FC)
    assert not stack.three_wanted(False, True, FC)  # the avatar's own layouts stay
    assert not stack.three_wanted(True, False, FC)  # 16:9 untouched
    assert not stack.three_wanted(True, True, None)
    assert stack.source_facecam({"facecam": {**FC, "auto": True}}) == stack.validate(FC)
    assert stack.source_facecam({"facecam": None}) is None and stack.source_facecam(None) is None


def test_live_stack_prefers_the_recordings_own_box():
    ch = SimpleNamespace(prefs={"facecam": {"x": 0, "y": 0, "w": 0.2, "h": 0.2}})
    got = stack.wanted(SimpleNamespace(style="live"), ch, True, False, source_fc=stack.validate(FC))
    assert got == stack.validate(FC)


@pytest.mark.parametrize("order", ["youtuber_top", "header_top"])
def test_clip3_stacks_camera_header_and_game(tmp_path, order):
    src = _source(tmp_path / "live.mp4")
    W, H = 270, 480
    lay = stack.layout3(stack.validate(FC), order, W, H, 640, 360)
    out = stack.clip3(src, 1.0, W, H, 25, tmp_path / "out.mp4", stack.validate(FC), lay)
    f = _frame(out, W, H, 0.2).astype(int)
    cy = lay["cam"][0] + lay["cam"][1] // 2
    r, g, b = f[cy, W // 2]
    assert b > 150 and r < 100  # the streamer's camera fills its panel
    gy = lay["game"][0] + lay["game"][1] // 2
    r, g, b = f[gy, 20]
    assert r > 150 and b < 100  # the game fills its panel (red)
    r, g, b = f[lay["game"][0] + 4, W - 6]
    assert r > 150 and b < 100  # cropped away from the camera corner (top-right): the camera is not shown twice
    hy = lay["header"][0] + lay["header"][1] // 2
    assert f[hy, W // 2].max() < 150  # the band behind the header is darkened


def test_header_goes_in_its_band(tmp_path):
    lay = stack.layout3(stack.validate(FC), "youtuber_top", 1080, 1920)
    scale, x, y = stack.header_place(lay, 1080)
    hy, hh = lay["header"]
    if hh >= 560:
        assert scale == "" and x == 0 and y == hy + (hh - 560) // 2
    else:
        assert scale.startswith("scale=") and y == hy and x > 0
    small = {"header": (100, 280)}
    assert stack.header_place(small, 1080) == ("scale=540:280,", 270, 100)
    g = compose.header_graph("c.ass", True, 10.0, 1080, 1920, 30, ("scale=540:280,", 270, 100))
    assert "[2:v]scale=540:280,format=rgba" in g and "overlay=270:100" in g and g.endswith("[v]")
    assert "overlay=0:0:eof_action=pass[hd]" in compose.header_graph("c.ass", False, 10.0, 1080, 1920, 30)


def test_final_pass_with_header_cover_and_captions_runs(tmp_path):
    """The whole vertical chain at a small size: 3-panel scene → captions + header in its band + cover."""
    from PIL import Image

    from cvm.render.captions import write_ass

    W, H = 360, 640
    src = _source(tmp_path / "live.mp4")
    lay = stack.layout3(stack.validate(FC), "youtuber_top", W, H, 640, 360)
    scene = stack.clip3(src, 2.0, W, H, 25, tmp_path / "scene.mp4", stack.validate(FC), lay)
    header = tmp_path / "header.png"
    Image.new("RGBA", (W, 187), (255, 210, 30, 255)).save(header)  # opaque yellow so the test can find it
    cover = stack.reaction_cover(src, stack.validate(FC), 0.5, W, H, tmp_path / "cover.jpg")
    ass = write_ass([{"w": "OLHA", "s": 0.1, "e": 1.5}], tmp_path / "c.ass", W, H, stack.caption_margin3(H), [], big=True)
    place = stack.header_place(lay, W, png_h=187)
    out = tmp_path / "final.mp4"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-i", str(scene), "-f", "lavfi", "-t", "2", "-i",
                    "anullsrc=r=48000:cl=stereo", "-i", str(header), "-i", str(cover), "-filter_complex",
                    compose.header_graph(compose._filter_path(ass), True, 2.0, W, H, 25, place),
                    "-map", "[v]", "-map", "1:a", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    late = _frame(out, W, H, 1.0).astype(int)
    hy = lay["header"][0] + min(lay["header"][1], 187) // 2
    r, g, b = late[hy, W // 2]
    assert r > 200 and g > 150 and b < 100  # the header sits in its band, between camera and game
    r, g, b = late[lay["cam"][1] // 2, W // 2]
    assert b > 150  # the camera above it is uncovered
    first = _frame(out, W, H, 0.0).astype(int)
    r, g, b = first[int(H * 0.8), W // 2]
    assert b > 150  # the cover (first 0.5 s): the streamer's camera big at the bottom


def test_most_expressive_picks_the_reaction_not_an_empty_camera():
    rng = np.random.default_rng(0)
    calm = [np.full((36, 64), 100.0) + rng.normal(0, 1, (36, 64)) for _ in range(8)]
    calm[3][10:26, 20:44] += 40  # mouth open, arms up
    calm[6][:] = 0  # he left / the scene cut: far too different
    assert stack.most_expressive(calm) == 3
    assert stack.most_expressive(calm[:2]) == 0


def test_reaction_time_lands_inside_the_cut(tmp_path):
    src = _source(tmp_path / "live.mp4", d=3)
    t = stack.reaction_time(src, stack.validate(FC), 0.5, 2.5)
    assert 0.5 <= t <= 2.5


# ---------- the cover rule ----------

def test_cover_presenter_only_when_the_video_shows_him():
    assert compose.cover_presenter("scenes", True)
    assert compose.cover_presenter("overlay", True)
    assert not compose.cover_presenter("editorial", True)
    assert not compose.cover_presenter("scenes", False)  # no avatar scene was rendered: no avatar on the cover


def test_thumbnail_without_presenter_never_cuts_him_out_and_uses_the_video(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(thumbnail, "presenter_cutout", lambda a: pytest.fail("no presenter on this cover"))
    monkeypatch.setattr(thumbnail, "_sticker_style", lambda p: None)
    seen = {}

    def bg(project_id, prompt, landscape, seed, side, fallback, kids=False, prefer=None):
        seen.update(prompt=prompt, fallback=fallback)
        return fallback

    monkeypatch.setattr(thumbnail, "background", bg)
    img = tmp_path / "reaction.jpg"
    Image.new("RGB", (1080, 1920), (0, 0, 255)).save(img)
    project = SimpleNamespace(id=1, avatar_id=3, channel_id=None)
    content = {"packaging": {"title": "Ele surtou", "thumbnail_text": "SURTOU", "thumbnail_prompt": "a man screaming"}}
    out = thumbnail.make_thumbnails(project, content, "pt-BR", tmp_path / "clip.mp4", tmp_path, False, variants=False,
                                    presenter=False, background_image=img)
    assert seen == {"prompt": None, "fallback": img}  # the video's own picture, never an AI drawing
    assert Path(out[0]).exists()


def test_thumbnail_with_presenter_still_cuts_him_out(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(thumbnail, "presenter_cutout", lambda a: calls.append(a))
    monkeypatch.setattr(thumbnail, "_sticker_style", lambda p: None)
    monkeypatch.setattr(thumbnail, "background", lambda *a, **k: k["fallback"])
    monkeypatch.setattr(thumbnail, "build", lambda bg, t, out, *a, **k: out)
    thumbnail.make_thumbnails(SimpleNamespace(id=1, avatar_id=3, channel_id=None), {}, "pt-BR", tmp_path / "c.mp4",
                              tmp_path, False, variants=False, presenter=True)
    assert calls == [3]


def test_game_crop_side_and_kicker_never_cuts_a_word():
    from cvm.render import editorial

    assert stack.game_crop_x({"x": 0.0, "y": 0, "w": 0.26, "h": 0.26}) == "iw-ow"
    assert stack.game_crop_x(FC) == "0"
    assert editorial.short_kicker("GTA 6 · SPOILER · VIA PAULINHO O LOKO") == "GTA 6 · VIA PAULINHO O LOKO"
    assert editorial.short_kicker("UM DOIS TRES QUATRO CINCO SEIS SETE OITO") == "UM DOIS TRES QUATRO CINCO"
