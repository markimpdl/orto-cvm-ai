"""The whole-video presenter leaves the screen while the source's own audio plays (another creator talking): the user
appears only when he is the one speaking (2026-11-22, Short 580)."""
from pathlib import Path

from cvm.render import compose


def test_presenter_hidden_while_original_audio_plays(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(compose, "run_ffmpeg", lambda *a: seen.append(" ".join(map(str, a))))
    compose._overlay_presenter(Path("v.mp4"), Path("a.mov"), 0.0, 46.0, 1080, 1920, 30, tmp_path / "o.mp4",
                               hide=[(4.17, 10.17), (24.22, 37.22)])
    assert "enable='not(between(t,4.170,10.170)+between(t,24.220,37.220))'" in seen[0]


def test_presenter_always_on_without_hidden_ranges(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(compose, "run_ffmpeg", lambda *a: seen.append(" ".join(map(str, a))))
    compose._overlay_presenter(Path("v.mp4"), Path("a.mov"), 0.0, 46.0, 1080, 1920, 30, tmp_path / "o.mp4")
    assert "enable=" not in seen[0]


def test_no_captions_while_the_presenter_is_off_screen():
    words = [{"w": "GTA", "s": 1.0, "e": 1.4}, {"w": "base", "s": 5.0, "e": 5.4}, {"w": "inscreve", "s": 40.0, "e": 40.5}]
    kept = compose.captions_outside(words, [(4.17, 10.17), (24.22, 37.22)])
    assert [w["w"] for w in kept] == ["GTA", "inscreve"]
    assert compose.captions_outside(words, []) == words


def test_presenter_off_in_scenes_without_his_lines():
    scenes = [{"index": 1, "narration": "Invadiu a base"}, {"index": 2, "narration": ""},
              {"index": 3, "narration": "Se inscreve"}, {"index": 4, "narration": "  "}]
    timings = [{"index": 1, "start": 0.0, "end": 3.8}, {"index": 2, "start": 4.17, "end": 10.17, "original_audio": True},
               {"index": 3, "start": 10.5, "end": 14.0}, {"index": 4, "start": 14.3, "end": 20.0}]
    assert compose.silent_ranges(scenes, timings, 0.0) == [(4.17, 10.17), (14.3, 20.0)]


def test_short_presenter_is_small_at_the_side(tmp_path, monkeypatch):
    """Shorts: never big, never centred over the game (user, 2026-11-24)."""
    seen = []
    monkeypatch.setattr(compose, "run_ffmpeg", lambda *a: seen.append(" ".join(map(str, a))))
    _, side = compose._overlay_presenter(Path("v.mp4"), Path("a.mov"), 0.0, 30.0, 1080, 1920, 30, tmp_path / "o.mp4",
                                         margin_bottom=300)
    size = int(1920 * compose.SHORT_PRESENTER) // 2 * 2
    assert f"scale={size}:{size}" in seen[0] and size < 1080 * 0.35
    assert f"overlay={1080 - size - 21}:" in seen[0] and side == 0
    compose._overlay_cutout(Path("bg.mp4"), Path("a.mov"), 5.0, 1080, 1920, 30, 300, tmp_path / "c.mp4", big=True)
    assert f"scale={size}:{size}" in seen[1]


def test_short_presenter_only_now_and_then():
    scenes = [{"index": i, "visual": {"type": t}} for i, t in enumerate(["broll", "footage", "avatar_pip", "broll"])]
    timings = {0: {"start": 0.0, "end": 3.0}, 1: {"start": 3.0, "end": 8.0}, 2: {"start": 8.0, "end": 11.0},
               3: {"start": 11.0, "end": 20.0}}
    assert compose.short_presenter_hide(scenes, timings, 0.0, 20.0) == [(3.0, 8.0), (11.0, 20.0)]
