import sys

import pytest

from cvm import media


def test_a_hung_process_is_killed_and_retried(monkeypatch):
    monkeypatch.setattr(media, "STALL_S", 1)
    monkeypatch.setattr(media, "FFMPEG_TRIES", 2)
    monkeypatch.setattr(media.subprocess.Popen, "communicate", _short_waits(media.subprocess.Popen.communicate))
    calls = []
    real = media._run_watched
    monkeypatch.setattr(media, "_run_watched", lambda cmd: calls.append(1) or real([sys.executable, "-c", "import time; time.sleep(60)"]))
    with pytest.raises(RuntimeError, match="travou 2 vezes"):
        media.run_ffmpeg("-version")
    assert len(calls) == 2


def test_a_busy_process_is_left_alone(monkeypatch):
    monkeypatch.setattr(media, "STALL_S", 1)
    monkeypatch.setattr(media.subprocess.Popen, "communicate", _short_waits(media.subprocess.Popen.communicate))
    code, _ = media._run_watched([sys.executable, "-c", "import time\nt=time.time()\nwhile time.time()-t<3: pass"])
    assert code == 0


def _short_waits(communicate):
    def wrapped(self, input=None, timeout=None):
        return communicate(self, input, None if timeout is None else min(timeout, 0.3))
    return wrapped
