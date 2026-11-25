"""Best moments of the user's own clips ("Melhores momentos", Project.media_cut = "highlights").

Each video gets keep-ranges in `Asset.meta["segments"]` ([{start, end}], seconds); the scriptwriter places
user_media scenes only inside them and selection clamps `media_start` into one. A file with no segments is left
out. Per second of footage:
- action = loudness (EBU R128 momentary) + motion (scene-change score), as z-scores within the clip;
- black/empty frames are dull;
- a second that looks like a second of an earlier file (game captures often overlap: "record that" twice) is a
  repeat and is cut — files are compared in name order, which for console captures is recording order.
Seconds below the clip's own bar are dropped, short gaps are bridged, pieces under MIN_LEN are discarded. When
almost all of a clip is kept, the whole clip is used (the user's rule: "se forem todos importantes usa o completo").
`meta["segments_auto"]` marks ranges found here, so a hand-edited list is never overwritten by a recount.
"""

import logging
import re
import subprocess
from pathlib import Path

import numpy as np

from cvm.config import settings

log = logging.getLogger(__name__)
MIN_LEN = 3.0  # shortest piece worth keeping (s)
GAP = 2.0  # dull stretches up to this long inside action are kept, so cuts don't stutter
WHOLE = 0.85  # keep the whole clip when this share of it is good
DUP_DIFF = 0.3  # mean abs difference of two contrast-normalised 32x18 thumbnails below which it is the same shot
# (measured on game captures: overlapping recordings 0.0-0.25, different shots 0.45+, even in dark cutscenes)
DARK = 14.0  # mean luma below which a second is black / fade


def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True).stderr


def _loudness(path: Path, n: int) -> np.ndarray:
    err = _run([str(settings.ffmpeg_bin), "-nostats", "-i", str(path), "-vn", "-af",
                "ebur128=metadata=1,ametadata=print:key=lavfi.r128.M", "-f", "null", "-"])
    t = [float(x) for x in re.findall(r"pts_time:([\d.]+)", err)]
    # silent stretches print "-inf" or "nan": one NaN would poison every z-score of the file (all seconds "dull")
    m = [v if np.isfinite(v := float(x)) else -70.0 for x in re.findall(r"lavfi\.r128\.M=(-?[\w.]+)", err)]
    out = np.full(n, -70.0)
    if not t:
        return out
    t, m = np.array(t[:len(m)]), np.clip(np.array(m[:len(t)]), -70, 0)
    for s in range(n):
        sel = (t >= s) & (t < s + 1)
        if sel.any():
            out[s] = m[sel].mean()
    return out


def _thumbs(path: Path, fps: int) -> np.ndarray:
    """32x18 grey thumbnails (rows of 576 values, 0-255) at `fps`."""
    raw = subprocess.run([str(settings.ffmpeg_bin), "-v", "error", "-i", str(path), "-an", "-vf",
                          f"fps={fps},scale=32:18,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    k = len(raw) // 576
    return np.frombuffer(raw[:k * 576], dtype=np.uint8).reshape(k, 576).astype(np.float32) if k else \
        np.zeros((0, 576), np.float32)


def _norm(a: np.ndarray) -> np.ndarray:
    """Contrast-normalised thumbnails, so two different dark shots don't look alike."""
    return (a - a.mean(axis=1, keepdims=True)) / (a.std(axis=1, keepdims=True) + 4)


def _frames(path: Path, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Motion per second (mean scene score at 5 fps) and one thumbnail per second."""
    err = _run([str(settings.ffmpeg_bin), "-i", str(path), "-an", "-vf",
                "fps=5,scale=160:-2,select='gte(scene,0)',metadata=print:key=lavfi.scene_score", "-f", "null", "-"])
    t = [float(x) for x in re.findall(r"pts_time:([\d.]+)", err)]
    sc = [float(x) for x in re.findall(r"scene_score=([\d.]+)", err)]
    motion = np.zeros(n)
    if t:
        t, sc = np.array(t[:len(sc)]), np.array(sc[:len(t)])
        for s in range(n):
            sel = (t >= s) & (t < s + 1)
            if sel.any():
                motion[s] = sc[sel].mean()
    thumbs = _thumbs(path, 1)
    if len(thumbs) < n:  # pad with the last frame (or black)
        pad = thumbs[-1:] if len(thumbs) else np.zeros((1, 18 * 32), np.float32)
        thumbs = np.concatenate([thumbs, np.repeat(pad, n - len(thumbs), axis=0)])
    return motion, thumbs[:n]


def _z(a: np.ndarray) -> np.ndarray:
    return (a - a.mean()) / (a.std() + 1e-6)


def _ranges(keep: np.ndarray, duration: float, action: np.ndarray | None = None) -> list[dict]:
    """Boolean per second → [{start, end}] with short gaps bridged and short pieces dropped; with `action` (score
    per second) each range also gets "score" = its mean action."""
    out: list[list[float]] = []
    for s, k in enumerate(keep):
        if not k:
            continue
        if out and s - out[-1][1] <= GAP:
            out[-1][1] = s + 1
        else:
            out.append([s, s + 1])
    res = []
    for a, b in out:
        b = min(b, duration)
        if b - a >= MIN_LEN:
            seg = {"start": round(float(a), 1), "end": round(float(b), 1)}
            if action is not None:
                seg["score"] = round(float(action[int(a):int(np.ceil(b))].mean()), 2)
            res.append(seg)
    return res


def _windows(action: np.ndarray, duration: float, size: float = 30.0, limit: int = 20) -> list[dict]:
    """Candidate moments for Shorts: windows of up to `size` s around the strongest seconds (peak a bit after the
    start, so the build-up is kept), best first, no two overlapping."""
    out: list[dict] = []
    for t in np.argsort(action)[::-1]:
        if len(out) >= limit or not np.isfinite(action[t]):
            break
        if any(w["start"] <= t < w["end"] for w in out):
            continue
        a = max(0.0, t - size * 0.6)
        b = min(float(duration), a + size)
        a = max(0.0, b - size)
        if any(a < w["end"] and b > w["start"] for w in out):
            continue
        part = action[int(a):int(np.ceil(b))]
        part = part[np.isfinite(part)]  # dark/repeated seconds are -inf: score the window on the rest
        out.append({"start": round(a, 1), "end": round(b, 1), "peak": float(t),
                    "score": round(float(part.mean()), 2) if len(part) else 0.0})
    return sorted(out, key=lambda w: -w["score"])


VIRAL_MIN = 1.0  # a moment's mean action (z-score mix of loudness and motion) must stand this far above the video's
MAX_SHORTS = 8  # average to become a Short on its own; never more Shorts than this from one video


def top_moments(segments: list[dict], n: int | None, min_s: float = 7.0, max_s: float = 45.0) -> list[dict]:
    """The best non-overlapping moments (each min_s–max_s long, clipped around its peak, else its middle), in time
    order. n=None: as many as are strong enough (score ≥ VIRAL_MIN), at most MAX_SHORTS — a dull video gives none.
    Segments or windows without a score count as 0."""
    chosen: list[dict] = []
    limit = MAX_SHORTS if n is None else n
    for s in sorted(segments, key=lambda x: -(x.get("score") or 0)):
        if len(chosen) >= limit or (n is None and (s.get("score") or 0) < VIRAL_MIN):
            break
        a, b = float(s["start"]), float(s["end"])
        if b - a > max_s:
            mid = float(s.get("peak", (a + b) / 2))
            a = max(a, min(mid - max_s * 0.6, b - max_s))
            b = a + max_s
        if b - a < min_s or any(a < c["end"] and b > c["start"] for c in chosen):
            continue
        chosen.append({"start": round(a, 1), "end": round(b, 1), "score": s.get("score") or 0,
                       **{k: s[k] for k in ("chat_rate", "chat_peak") if k in s}})
    return sorted(chosen, key=lambda c: c["start"])


def analyse(paths: list[Path], durations: list[float]) -> list[dict]:
    """One result per file, in the given order: {segments, whole, dropped: {dull, repeat} (seconds)}.
    Action is scored against all the files together, so a clip that is calm next to the others (walking,
    menus) loses its dull parts even if it is evenly calm."""
    feats = []
    for path, dur in zip(paths, durations):
        n = max(1, int(np.ceil(dur or 1)))
        motion, thumbs = _frames(path, n)
        feats.append((n, _loudness(path, n), motion, thumbs))
    loud_all = np.concatenate([f[1] for f in feats])
    mot_all = np.concatenate([f[2] for f in feats])
    lm, ls, mm, ms = loud_all.mean(), loud_all.std() + 1e-6, mot_all.mean(), mot_all.std() + 1e-6
    seen: list[np.ndarray] = []  # normalised thumbnails (4 per second) of every earlier file
    results = []
    for path, dur, (n, loud, motion, thumbs) in zip(paths, durations, feats):
        g = 0.6 * (loud - lm) / ls + 0.4 * (motion - mm) / ms  # against the whole set
        c = 0.6 * _z(loud) + 0.4 * _z(motion) if n > 2 else np.zeros(n)  # within the clip
        score = 0.5 * g + 0.5 * c
        dark = thumbs.mean(axis=1) < DARK
        norm = _norm(thumbs)
        repeat = np.zeros(n, bool)
        if seen:
            prev = np.concatenate(seen)
            for s in range(n):
                repeat[s] = bool(np.abs(prev - norm[s]).mean(axis=1).min() < DUP_DIFF) and not dark[s]
        seen.append(_norm(_thumbs(path, 4)))
        good = (score > -0.45) & ~dark & ~repeat
        if good.mean() >= WHOLE and not repeat.any():
            segs, whole = [{"start": 0.0, "end": round(float(dur), 1)}], True
        else:
            segs, whole = _ranges(good, dur, score), False
        if whole:
            segs[0]["score"] = round(float(score.mean()), 2)
        moments = _windows(np.where(good, score, -np.inf), dur)
        # per-second tracks for the cut editor and the viral detection (cvm/lives/signals.py): loudness in LUFS,
        # motion as the scene score, action as this file's mixed score
        tracks = {"loudness": [round(float(v), 1) for v in loud], "motion": [round(float(v), 3) for v in motion],
                  "action": [round(float(v), 2) for v in score]}
        results.append({"segments": segs, "whole": whole, "moments": moments, "tracks": tracks,
                        "dropped": {"dull": int((~good & ~repeat).sum()), "repeat": int(repeat.sum())}})
        log.info("highlights %s: %s", path.name, {k: v for k, v in results[-1].items() if k != "tracks"})
    return results


def refresh_project(project_id: int, force: bool = False) -> int:
    """Find the best moments of every video of a project that has none yet (or all, with force) and store them.
    Ranges set by hand (segments_auto False) are kept. Returns how many files were analysed."""
    from sqlmodel import Session, select

    from cvm.db import engine
    from cvm.models import Asset

    with Session(engine) as s:
        rows = sorted(s.exec(select(Asset).where(Asset.project_id == project_id, Asset.kind == "user_media")).all(),
                      key=lambda a: Path(a.path).name)
        vids = [a for a in rows if a.meta.get("kind") == "video" and Path(a.path).exists()]
        if not vids or not (force or any("segments" not in a.meta for a in vids)):
            return 0
        res = analyse([Path(a.path) for a in vids], [float(a.meta.get("duration") or 0) for a in vids])
        for a, r in zip(vids, res):
            if "segments" in a.meta and a.meta.get("segments_auto") is False:
                continue  # chosen by hand
            a.meta = {**a.meta, "segments": r["segments"], "segments_auto": True, "whole": r["whole"],
                      "dropped": r["dropped"], "moments": r.get("moments", [])}
            s.add(a)
        s.commit()
        return len(vids)


def ranges_apply(meta: dict, project) -> bool:
    """A video's `segments` limit what plays: under "Melhores momentos" (project media_cut "highlights"), or when they
    were set by hand (from–to ranges on /fabrica/novo, a Short pinned to one moment: segments_auto False)."""
    return bool(meta.get("segments")) and (getattr(project, "media_cut", None) == "highlights"
                                           or meta.get("segments_auto") is False)


def segment_start(meta: dict, start: float) -> float:
    """Clamp a scene's cut-in second into the nearest kept range of the file."""
    segs = meta.get("segments") or []
    if not segs:
        return start
    for g in segs:
        if g["start"] <= start < g["end"] - 1:
            return start
    return min(segs, key=lambda g: abs(g["start"] - start))["start"]


def segment_end(meta: dict, start: float) -> float | None:
    """End of the kept range a cut-in second falls in (after segment_start), so a scene never runs past it."""
    for g in meta.get("segments") or []:
        if g["start"] <= start < g["end"]:
            return float(g["end"])
    return None
