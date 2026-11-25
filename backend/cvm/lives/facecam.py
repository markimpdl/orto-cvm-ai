"""Find the streamer's webcam overlay in a recording (owner, 2026-11-25: "Mostrar a reação do youtuber é o essencial
nesse formato"), for the 3-panel Short layout (cvm/render/stack.py).

Runs in the worker during the cut analysis (cvm/lives/cuts.py), on the editor's 480p proxy:
1. FRAMES frames spread over the video (one fast seek each), as grey pictures WIDTH px wide;
2. faces in each frame by OpenCV's Haar cascades (frontal + profile; opencv is already in the venv, nothing new is
   downloaded). Game footage gives faces too, but they move: only a face found at the same place in at least
   STABLE_SHARE of the frames is the streamer;
3. the face is grown to the overlay rectangle: the overlay's border (or its edge against the game) is a line that
   stays put while the game behind it changes, so it shows in the per-pixel MEDIAN of the image gradients over the
   frames. The strongest such column left and right of the face, then row above and below, are the box; a side
   without a clear line takes a default margin around the face (or the frame edge when it is that close).
A face that fills most of the frame (a "just chatting" camera) is no overlay: None. None also when opencv is missing
or nothing stable is found; the owner can still draw the box in the editor.
"""

import logging
import subprocess
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
FRAMES = 20
WIDTH = 640
STABLE_SHARE = 0.35  # the face must be at the same place in this share of the frames
EDGE_MIN = 10.0  # mean median-gradient (0–255) of a border line
EDGE_RATIO = 2.5  # ... and this many times the typical value around it
MAX_AREA = 0.6  # a "camera" bigger than this share of the frame is the whole picture, not an overlay


# ---------- faces ----------

def faces_haar(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Faces (x, y, w, h) in one grey frame; [] when opencv is not available."""
    try:
        import cv2
    except ImportError:
        return []
    h, w = gray.shape
    out = []
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        cascade = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / name))
        if cascade.empty():
            continue
        found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                         minSize=(max(12, w // 40), max(12, w // 40)))
        out += [tuple(int(v) for v in f) for f in found]
    return out


def stable_face(boxes: list[list[tuple[int, int, int, int]]], min_share: float = STABLE_SHARE):
    """The face found at the same place in most frames: its median box (x, y, w, h), or None."""
    n = len(boxes)
    if not n:
        return None
    flat = [(i, b) for i, bs in enumerate(boxes) for b in bs]
    best, best_frames = None, 0
    for _, (x, y, w, h) in flat:
        cx, cy = x + w / 2, y + h / 2
        near = [(i, b) for i, b in flat
                if abs(b[0] + b[2] / 2 - cx) < 0.5 * w and abs(b[1] + b[3] / 2 - cy) < 0.5 * h and 0.6 < b[2] / w < 1.6]
        frames = len({i for i, _ in near})
        if frames > best_frames:
            best, best_frames = near, frames
    if not best or best_frames < max(3, min_share * n):
        return None
    arr = np.array([b for _, b in best], dtype=float)
    return tuple(int(round(v)) for v in np.median(arr, axis=0))


# ---------- the overlay rectangle ----------

def _pick(scores: np.ndarray, idx: np.ndarray, far_first: bool) -> int | None:
    """Index (from idx) of the border line among the scores, or None when no line stands out."""
    if not len(scores):
        return None
    base = float(np.median(scores))
    ok = scores >= max(EDGE_MIN, EDGE_RATIO * base)
    if not ok.any():
        return None
    # the strongest line; among near-equals (a border is two lines: outer and inner edge) the one farther from the face
    top = float(scores[ok].max())
    good = np.where(scores >= 0.7 * top)[0]
    return int(idx[good[0] if far_first else good[-1]])


def grow(frames: np.ndarray, face: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of the overlay around `face`, from the median gradients of the frames (n, h, w)."""
    f = frames.astype(np.float32)
    n, H, W = f.shape
    mx = np.median(np.abs(np.diff(f, axis=2)), axis=0)  # (H, W-1): line between columns x and x+1
    my = np.median(np.abs(np.diff(f, axis=1)), axis=0)  # (H-1, W)
    fx, fy, fw, fh = face
    ya, yb = max(0, fy), min(H - 1, fy + fh)

    def col(lo: int, hi: int, far_first: bool) -> int | None:
        lo, hi = max(0, lo), min(W - 2, hi)
        if hi <= lo:
            return None
        idx = np.arange(lo, hi + 1)
        return _pick(mx[ya:yb, lo:hi + 1].mean(axis=0), idx, far_first)

    left = col(int(fx - 3 * fw), int(fx - 0.15 * fw), True)
    x0 = left + 1 if left is not None else (0 if fx - 1.2 * fw <= 0 else int(fx - 0.9 * fw))
    right = col(int(fx + 1.15 * fw), int(fx + 4 * fw), False)
    x1 = right + 1 if right is not None else (W if fx + 2.2 * fw >= W else int(fx + 1.9 * fw))
    xa, xb = x0 + 2, max(x0 + 3, x1 - 2)  # inside the box, off its own border columns

    def row(lo: int, hi: int, far_first: bool) -> int | None:
        lo, hi = max(0, lo), min(H - 2, hi)
        if hi <= lo:
            return None
        idx = np.arange(lo, hi + 1)
        return _pick(my[lo:hi + 1, xa:xb].mean(axis=1), idx, far_first)

    top = row(int(fy - 3 * fh), int(fy - 0.15 * fh), True)
    y0 = top + 1 if top is not None else (0 if fy - 1.0 * fh <= 0 else int(fy - 0.8 * fh))
    bottom = row(int(fy + 1.2 * fh), int(fy + 5 * fh), False)
    y1 = bottom + 1 if bottom is not None else (H if fy + 2.6 * fh >= H else int(fy + 2.2 * fh))
    return max(0, x0), max(0, y0), min(W, x1), min(H, y1)


def find(frames: list[np.ndarray], faces=faces_haar) -> dict | None:
    """{x, y, w, h, auto: True} (fractions of the frame) of the webcam overlay in these grey frames, or None."""
    if not frames:
        return None
    face = stable_face([faces(g) for g in frames])
    if face is None:
        return None
    H, W = frames[0].shape
    x0, y0, x1, y1 = grow(np.stack(frames), face)
    w, h = (x1 - x0) / W, (y1 - y0) / H
    if w * h > MAX_AREA or w < 0.05 or h < 0.05:
        return None
    return {"x": round(x0 / W, 4), "y": round(y0 / H, 4), "w": round(w, 4), "h": round(h, 4), "auto": True}


# ---------- frames ----------

def sample(video: Path, duration: float, n: int = FRAMES, width: int = WIDTH) -> list[np.ndarray]:
    """n grey frames spread over the video (3%–97%), one fast seek each."""
    from cvm.config import settings

    out = []
    for k in range(n):
        t = duration * (0.03 + 0.94 * k / max(1, n - 1)) if duration > 0 else k
        r = subprocess.run([settings.ffmpeg_bin, "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                           capture_output=True, timeout=60)
        raw = r.stdout
        if r.returncode != 0 or not raw or len(raw) % width:
            continue
        out.append(np.frombuffer(raw, dtype=np.uint8).reshape(len(raw) // width, width))
    sizes = {f.shape for f in out}
    return [f for f in out if f.shape == max(sizes, key=lambda s: sum(g.shape == s for g in out))] if out else []


def detect(video: Path, duration: float) -> dict | None:
    """The webcam box of this recording, or None (never raises: the editor lets the owner draw it)."""
    try:
        return find(sample(video, duration))
    except Exception as e:  # noqa: BLE001
        log.warning("facecam detection on %s failed: %s", video.name, e)
        return None
