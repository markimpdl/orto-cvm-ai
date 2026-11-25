"""Webcam overlay detection for the 3-panel Shorts (cvm/lives/facecam.py), on synthetic frames."""
import numpy as np
import pytest

from cvm.lives import facecam

H, W = 360, 640
CAM = (460, 12, 630, 142)  # x0, y0, x1, y1 of the overlay
FACE = (525, 45, 40, 48)


def _game(rng, k):
    """Smooth "gameplay" that changes every frame (bilinear-upsampled noise)."""
    import cv2

    small = rng.integers(0, 256, (H // 20, W // 20)).astype(np.float32)
    return cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR)


def _frames(n=20, border=True, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    x0, y0, x1, y1 = CAM
    for k in range(n):
        f = _game(rng, k)
        f[y0:y1, x0:x1] = 70 + rng.normal(0, 2, (y1 - y0, x1 - x0))  # a wall behind the streamer
        fx, fy, fw, fh = FACE
        f[fy:fy + fh, fx + (k % 3):fx + fw + (k % 3)] = 160  # he moves a bit
        if border:
            f[y0:y0 + 3, x0:x1] = f[y1 - 3:y1, x0:x1] = 235
            f[y0:y1, x0:x0 + 3] = f[y0:y1, x1 - 3:x1] = 235
        out.append(np.clip(f, 0, 255).astype(np.uint8))
    return out


def _stub(boxes_by_frame):
    it = iter(boxes_by_frame)
    return lambda gray: next(it)


def _jittered(n, extra=None):
    rng = np.random.default_rng(3)
    fx, fy, fw, fh = FACE
    return [[(fx + int(rng.integers(-2, 3)), fy + int(rng.integers(-2, 3)), fw, fh)] + (extra(k) if extra else [])
            for k in range(n)]


@pytest.mark.parametrize("border", [True, False])
def test_grows_the_face_to_the_overlay_rectangle(border):
    fc = facecam.find(_frames(border=border), faces=_stub(_jittered(20)))
    assert fc and fc["auto"] is True
    x0, y0, x1, y1 = CAM
    assert abs(fc["x"] * W - x0) <= 4 and abs((fc["x"] + fc["w"]) * W - x1) <= 4
    assert abs(fc["y"] * H - y0) <= 4 and abs((fc["y"] + fc["h"]) * H - y1) <= 4


def test_moving_false_faces_in_the_game_are_ignored():
    rng = np.random.default_rng(5)

    def ghost(k):  # a "face" Haar finds in the game, somewhere else every frame
        return [(int(rng.integers(0, 380)), int(rng.integers(150, 300)), 40, 40)]

    fc = facecam.find(_frames(), faces=_stub(_jittered(20, ghost)))
    assert fc and abs(fc["x"] * W - CAM[0]) <= 4


def test_no_stable_face_means_no_camera():
    rng = np.random.default_rng(7)
    boxes = [[(int(rng.integers(0, 560)), int(rng.integers(0, 280)), 40, 40)] for _ in range(20)]
    assert facecam.find(_frames(), faces=_stub(boxes)) is None
    assert facecam.find(_frames(), faces=lambda g: []) is None
    assert facecam.find([], faces=lambda g: []) is None


def test_a_full_frame_camera_is_not_an_overlay():
    frames = [np.full((H, W), 90, np.uint8) for _ in range(10)]
    big = [[(200, 60, 240, 240)] for _ in range(10)]
    assert facecam.find(frames, faces=_stub(big)) is None


def test_stable_face_needs_enough_frames():
    box = (100, 100, 40, 40)
    assert facecam.stable_face([[box]] * 3 + [[]] * 17) is None  # 3 of 20 < 35%
    assert facecam.stable_face([[box]] * 8 + [[]] * 12) == box


def test_detect_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(facecam, "sample", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert facecam.detect(tmp_path / "x.mp4", 100) is None
