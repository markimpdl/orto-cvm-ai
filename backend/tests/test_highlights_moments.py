"""Scores for best moments and the pick of the top ones for Shorts (lives are long: 2–3 h)."""
import time

import numpy as np

from cvm.visuals import highlights as h


def test_segments_have_scores():
    keep = np.zeros(60, bool)
    keep[5:15] = True
    keep[30:50] = True
    action = np.zeros(60)
    action[30:50] = 3.0
    segs = h._ranges(keep, 60.0, action)
    assert [s["start"] for s in segs] == [5.0, 30.0]
    assert segs[1]["score"] > segs[0]["score"]


def test_moment_windows_follow_peaks():
    action = np.zeros(600)
    action[100:110] = 5.0
    action[400:405] = 4.0
    ws = h._windows(action, 600.0, size=30.0)
    assert ws[0]["start"] <= 100 and ws[0]["end"] >= 110
    assert ws[0]["end"] - ws[0]["start"] <= 30.0
    assert any(w["start"] <= 400 <= w["end"] for w in ws[1:])


def test_top_moments_respects_length_and_overlap():
    segs = [{"start": 0.0, "end": 3.0, "score": 5.0}, {"start": 10.0, "end": 30.0, "score": 2.0},
            {"start": 100.0, "end": 220.0, "score": 4.0}, {"start": 105.0, "end": 140.0, "score": 3.0}]
    out = h.top_moments(segs, 3, min_s=7.0, max_s=45.0)
    assert [m["start"] for m in out] == sorted(m["start"] for m in out)
    assert len(out) == 2  # the 3 s piece is too short, the 105–140 one overlaps the chosen 100–220 window
    assert all(7.0 <= m["end"] - m["start"] <= 45.0 for m in out)


def test_top_moments_quiet_live():
    assert h.top_moments([{"start": 0.0, "end": 5.0, "score": 1.0}], 3) == []


def test_long_recording_is_fast():
    rng = np.random.default_rng(1)
    action = rng.normal(size=10_800)
    t = time.time()
    segs = h._ranges(action > -0.45, 10_800.0, action)
    ws = h._windows(action, 10_800.0)
    assert time.time() - t < 2.0
    assert segs and all(s["end"] > s["start"] for s in segs)
    assert ws and all(w["end"] > w["start"] for w in ws)


def test_auto_count_keeps_only_strong_moments():
    ms = [{"start": i * 100.0, "end": i * 100.0 + 30, "score": sc} for i, sc in enumerate([2.5, 1.8, 1.2, 0.4, 0.2, -0.3])]
    out = h.top_moments(ms, None)
    assert [m["score"] for m in out] == [2.5, 1.8, 1.2]  # ≥ VIRAL_MIN only, time order


def test_auto_count_is_capped():
    ms = [{"start": i * 100.0, "end": i * 100.0 + 30, "score": 3.0} for i in range(20)]
    assert len(h.top_moments(ms, None)) == h.MAX_SHORTS


def test_auto_count_dull_video_gives_none():
    ms = [{"start": 0.0, "end": 30.0, "score": 0.3}]
    assert h.top_moments(ms, None) == []


def test_window_scores_are_finite():
    action = np.full(100, -np.inf)
    action[40:60] = 1.0
    ws = h._windows(action, 100.0, size=30.0)
    assert ws and all(np.isfinite(w["score"]) for w in ws)


def test_long_recordings_are_analysed_on_a_light_proxy(tmp_path):
    import subprocess

    from cvm.config import settings
    from cvm.lives import package
    clip = tmp_path / "live.mp4"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
                    "-f", "lavfi", "-i", "sine", "-t", "3", "-shortest", str(clip)], check=True)
    src = package.analysis_source(clip, 3.0, min_s=1)
    assert src != clip and src.exists() and src.stat().st_size < clip.stat().st_size * 2
    assert package.analysis_source(clip, 3.0, min_s=3600) == clip
