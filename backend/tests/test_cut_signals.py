"""Per-second signals of a long video for the cut editor (spec 2026-11-25 editor de cortes)."""
import json
import subprocess

import numpy as np
import pytest
from huey import MemoryHuey

from cvm import gpu
from cvm.config import settings
from cvm.lives import ingest, signals


@pytest.fixture(autouse=True)
def _lock(monkeypatch):
    monkeypatch.setattr(gpu, "lock", MemoryHuey("t").lock_task("gpu"))
    monkeypatch.setattr(gpu, "free_whisper", lambda: None)


def _clip(path, seconds=3):
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(seconds), "-shortest", str(path)], check=True)
    return path


def test_lines_split_at_sentence_end_and_long_gaps():
    words = [{"w": "Olha", "s": 0.0, "e": 0.3}, {"w": "isso.", "s": 0.4, "e": 0.8},
             {"w": "Caramba", "s": 0.9, "e": 1.3}, {"w": "mano", "s": 1.4, "e": 1.7},
             {"w": "depois", "s": 5.0, "e": 5.4}]
    lines = signals.lines_from_words(words)
    assert [l["text"] for l in lines] == ["Olha isso.", "Caramba mano", "depois"]
    assert lines[0]["s"] == 0.0 and lines[0]["e"] == 0.8


def test_transcript_is_cached_next_to_the_video(tmp_path):
    video = _clip(tmp_path / "v.mp4")
    calls = []

    def fake(audio, language, words=False):
        calls.append((audio.suffix, language, words))
        return {"text": "oi tudo bem", "words": [{"w": "oi", "s": 0.1, "e": 0.3}, {"w": "tudo", "s": 0.4, "e": 0.6},
                                                 {"w": "bem", "s": 0.7, "e": 0.9}]}

    first = signals.transcript(video, "pt-BR", transcribe=fake)
    again = signals.transcript(video, "pt-BR", transcribe=fake)
    assert first == again == [{"s": 0.1, "e": 0.9, "text": "oi tudo bem"}]
    assert calls == [(".wav", "pt-BR", True)]
    assert (tmp_path / "v.transcript.pt-BR.json").exists()


def test_chat_track_is_shifted_back(tmp_path):
    dens = {40: 10.0}
    tr = signals.chat_track(dens, 60.0, lag=15)
    assert len(tr) == 61  # one value per started second
    assert int(np.argmax(tr)) == 25  # the audience reacts after the moment


def test_heatmap_track_spreads_each_bucket():
    heat = [{"start": 0.0, "end": 10.0, "value": 0.2}, {"start": 10.0, "end": 20.0, "value": 1.0}]
    tr = signals.heatmap_track(heat, 20.0)
    assert tr[5] == pytest.approx(0.2) and tr[15] == pytest.approx(1.0)
    assert signals.heatmap_track([], 20.0) is None


def test_comment_times_reads_timestamps():
    class Resp:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    pages = [{"items": [
        {"snippet": {"topLevelComment": {"snippet": {"textOriginal": "1:05 kkkk e 1:02:03 também", "likeCount": 3}}}},
        {"snippet": {"topLevelComment": {"snippet": {"textOriginal": "sem tempo aqui", "likeCount": 9}}}}]}]

    class Client:
        def get(self, url, params=None, timeout=None):
            assert params["videoId"] == "abc" and params["key"] == "K"
            return Resp(pages.pop(0))

    out = signals.comment_times("abc", api_key="K", client=Client())
    assert out == [(65, 4.0), (3723, 4.0)]
    assert signals.comment_times("abc", api_key=None) == []


def test_fused_and_tags():
    n = 100
    loud = np.zeros(n)
    loud[50:55] = 10
    tracks = {"loudness": loud.tolist(), "chat": None}
    z = signals.zscores(tracks)
    assert set(z) == {"loudness"}
    f = signals.fused(z)
    assert int(np.argmax(f)) in range(50, 55)
    assert "volume" in signals.tag(z, 48, 58)
    assert signals.tag(z, 0, 30) == ""


def test_waveform_peaks(tmp_path):
    video = _clip(tmp_path / "v.mp4", 2)
    peaks = signals.waveform(video, step=0.1)
    assert 18 <= len(peaks) <= 21 and max(peaks) > 100 and all(0 <= p <= 255 for p in peaks)


def test_scene_cuts_from_motion():
    motion = [0.01] * 20
    motion[7] = 0.6
    assert signals.scene_cuts(motion) == [7.0]


def test_info_from_keeps_the_youtube_heatmap():
    info = {"id": "abcdefghijk", "title": "x", "duration": 30, "extractor_key": "Youtube",
            "heatmap": [{"start_time": 0.0, "end_time": 10.0, "value": 0.5}, {"bad": 1}]}
    out = ingest.info_from(info, "https://www.youtube.com/watch?v=abcdefghijk")
    assert out["heatmap"] == [{"start": 0.0, "end": 10.0, "value": 0.5}]


def test_reactions_stub_returns_none(tmp_path):
    assert signals.reactions_track(tmp_path / "v.mp4", 10.0) is None


def test_normalised_tracks_for_the_editor():
    out = signals.for_editor({"loudness": [-70.0, -20.0, -10.0], "chat": None, "replay": [0.0, 0.5, 1.0]})
    assert out["loudness"] == [0.0, pytest.approx(0.833, abs=0.01), 1.0]
    assert "chat" not in out
    json.dumps(out)
