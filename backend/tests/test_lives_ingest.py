"""Download a live's recording with yt-dlp and attach it to a project as the user's own media."""
import subprocess

import pytest
from sqlmodel import Session, SQLModel, create_engine

from cvm.config import settings
from cvm.lives import ingest

LIVE = {"video_id": "a1", "title": "Live", "url": "https://www.youtube.com/watch?v=a1", "duration": 10.0}


def test_download_calls_ytdlp_once_and_reuses_file(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        (tmp_path / "live_a1.mp4").write_bytes(b"video")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    p1 = ingest.download(LIVE, tmp_path, run=run)
    p2 = ingest.download(LIVE, tmp_path, run=run)
    assert p1 == p2 == tmp_path / "live_a1.mp4"
    assert len(calls) == 1
    assert LIVE["url"] in calls[0]


def test_download_failure_raises_with_stderr(tmp_path):
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "ERROR: Private video")  # noqa: E731
    with pytest.raises(RuntimeError, match="privado"):
        ingest.download(LIVE, tmp_path, run=run)


def test_attach_file_keeps_audio_when_asked(tmp_path, monkeypatch):
    from cvm.api import media
    from cvm.models import Project

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    clip = tmp_path / "live.mp4"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2", "-shortest", str(clip)], check=True)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    p = Project(title="t", topic="t")
    s.add(p)
    s.commit()
    a = media.attach_file(s, p.id, clip, "live.mp4", "live do dia", keep_audio=True)
    assert a.kind == "user_media"
    assert a.meta["keep_audio"] is True
    assert abs(a.meta["duration"] - 2.0) < 0.3
