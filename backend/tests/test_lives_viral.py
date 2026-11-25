"""Viral potential of a recording's moments: transcript of each candidate + Claude's 0–10 score."""
import subprocess

import pytest
from huey import MemoryHuey

from cvm import gpu
from cvm.config import settings
from cvm.lives import viral


@pytest.fixture(autouse=True)
def _lock(monkeypatch):
    h = MemoryHuey("t")
    monkeypatch.setattr(gpu, "lock", h.lock_task("gpu"))
    monkeypatch.setattr(gpu, "light_lock", h.lock_task("gpu-light"))
    monkeypatch.setattr(gpu, "free_whisper", lambda: None)


def test_candidates_limit_and_order():
    meta = {"moments": [{"start": i * 100.0, "end": i * 100.0 + 30, "score": float(i)} for i in range(20)]}
    out = viral.candidates(meta, limit=12)
    assert len(out) == 12
    assert [c["start"] for c in out] == sorted(c["start"] for c in out)
    assert min(c["score"] for c in out) == 8.0  # the 12 strongest


def test_transcribe_marks_silence(tmp_path):
    clip = tmp_path / "v.mp4"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "4", "-shortest", str(clip)], check=True)
    seen = []

    def fake(audio, language, words=False):
        seen.append(audio.exists())
        return {"text": "" if len(seen) == 1 else "olha o tubarão!", "words": []}
    out = viral.transcribe_windows(clip, [{"start": 0.0, "end": 2.0}, {"start": 2.0, "end": 4.0}], "pt-BR", transcribe=fake)
    assert [w["text"] for w in out] == ["[sem fala]", "olha o tubarão!"] and all(seen)


def test_score_parses_fenced_json():
    ws = [{"start": 0.0, "end": 20.0, "text": "a"}, {"start": 50.0, "end": 80.0, "text": "b"}]
    answer = 'Aqui está:\n```json\n[{"i": 1, "score": 8, "hook": "ELE PULOU 😱", "why": "susto e reação"}, {"i": 0, "score": 3, "hook": "x", "why": "parado"}]\n```'
    out = viral.score(ws, "Live GTA 6", True, run=lambda prompt, **kw: {"result": answer})
    assert [o["viral"] for o in out] == [3, 8] and out[1]["hook"] == "ELE PULOU 😱"


def test_score_drops_bad_rows():
    ws = [{"start": 0.0, "end": 20.0, "text": "a"}]
    answer = '[{"i": 0, "score": 15}, {"i": 7, "score": 9}, {"i": "x"}, "lixo"]'
    assert viral.score(ws, "t", True, run=lambda prompt, **kw: {"result": answer}) == []


def test_choose_threshold_and_overlap():
    rows = [{"start": 0.0, "end": 30.0, "viral": 9}, {"start": 10.0, "end": 40.0, "viral": 8},
            {"start": 100.0, "end": 130.0, "viral": 7}, {"start": 200.0, "end": 230.0, "viral": 6}]
    out = viral.choose(rows, min_score=7, limit=8)
    assert [r["start"] for r in out] == [0.0, 100.0]


def test_transcription_uses_the_light_slot(tmp_path, monkeypatch):
    """Short whisper windows run beside a long presenter step (gpu.light), not behind it for hours."""
    import contextlib

    from cvm import gpu
    used = []

    @contextlib.contextmanager
    def light():
        used.append("light")
        yield
    monkeypatch.setattr(gpu, "light", light)
    monkeypatch.setattr(gpu, "section", lambda: (_ for _ in ()).throw(AssertionError("heavy lock used")))
    clip = tmp_path / "v.mp4"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                    "-f", "lavfi", "-i", "sine", "-t", "2", "-shortest", str(clip)], check=True)
    viral.transcribe_windows(clip, [{"start": 0.0, "end": 2.0}], "en-US", transcribe=lambda *a, **k: {"text": "hi"})
    assert used == ["light"]


def test_choose_keeps_the_best_when_asked_for_at_least_one():
    rows = [{"start": 0.0, "end": 20.0, "viral": 5}, {"start": 30.0, "end": 50.0, "viral": 6}]
    assert viral.choose(rows) == []
    assert [r["start"] for r in viral.choose(rows, min_count=1)] == [30.0]


def test_pick_moments_min_count_without_scores(monkeypatch, tmp_path):
    monkeypatch.setattr(viral, "transcribe_windows", lambda v, w, l, **k: w)
    monkeypatch.setattr(viral, "score", lambda *a, **k: [])
    meta = {"moments": [{"start": 0.0, "end": 20.0, "score": 0.2}]}  # below VIRAL_MIN too
    moments, method = viral.pick_moments(tmp_path / "v.mp4", meta, "t", False, "en-US", min_count=1)
    assert len(moments) == 1
