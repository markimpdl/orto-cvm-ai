"""Visible steps before the cut editor (owner, 2026-11-25): download → light copy → transcript → signals → AI blocks →
ready, stored on the CutReview row / a Setting row per download (cvm/lives/progress.py), shown by the API."""
import subprocess
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from huey import MemoryHuey
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm import gpu
from cvm.config import settings
from cvm.db import get_session
from cvm.lives import cuts, progress, runs
from cvm.models import Channel, CutReview, Setting


# --- parsing --------------------------------------------------------------------------------------------------------

def test_ytdlp_and_ffmpeg_lines_parse():
    assert progress.ytdlp_pct("[download]  42.3% of ~1.20GiB at  5.10MiB/s ETA 02:11") == 42.3
    assert progress.ytdlp_pct("[download] 100% of 10.00MiB in 00:00:02") == 100.0
    assert progress.ytdlp_pct("[download] Destination: live_x.f137.mp4") is None
    assert progress.ytdlp_pct("[youtube] abc: Downloading webpage") is None
    assert progress.ffmpeg_seconds("out_time_us=62500000") == pytest.approx(62.5)
    assert progress.ffmpeg_seconds("out_time_ms=62500000") == pytest.approx(62.5)  # ffmpeg's misnamed key: µs too
    assert progress.ffmpeg_seconds("out_time=01:02:03.500000") == pytest.approx(3723.5)
    assert progress.ffmpeg_seconds("out_time_us=N/A") is None
    assert progress.hms(4332) == "1h12" and progress.hms(10920) == "3h02"
    assert progress.hms(754) == "12 min" and progress.hms(45) == "45 s"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_reporter_throttles_but_never_drops_a_step_change():
    clock, seen = Clock(), []
    rep = progress.Reporter([seen.append], min_interval=2.0, clock=clock)
    assert rep.update("download", "Baixando 1%", pct=1)
    clock.t += 0.5
    assert not rep.update("download", "Baixando 5%", pct=5)  # too soon: kept for later
    clock.t += 0.5
    assert rep.update("proxy", "Versão leve", pct=0)  # a new step is written at once
    clock.t += 1.0
    assert not rep.update("proxy", "Versão leve 10%", pct=10)
    clock.t += 1.5
    assert rep.update("proxy", "Versão leve 30%", pct=30)  # ≥ 2 s since the last write
    rep.update("proxy", "Versão leve 31%", pct=31)
    rep.flush()  # the pending state is written when the block ends
    assert [s["label"] for s in seen] == ["Baixando 1%", "Versão leve", "Versão leve 30%", "Versão leve 31%"]
    assert seen[1]["since"] == seen[2]["since"]  # same step: same start time
    assert set(seen[-1]) >= {"step", "label", "pct", "done", "total", "since", "at", "waiting"}
    assert rep.update("proxy", "Versão leve 150%", pct=150, force=True) and seen[-1]["pct"] == 100.0


def test_report_without_a_listener_does_nothing():
    assert progress.current() is None
    progress.report("download", "Baixando 3%", pct=3)  # no error, nothing to write to


def test_nested_use_reports_to_both_sinks():
    outer, inner = [], []
    with progress.use(outer.append, min_interval=0):
        progress.report("download", "Baixando 50%", pct=50)
        with progress.use(inner.append):
            progress.report("ai", "IA analisando bloco 1 de 3", pct=0, done=0, total=3)
        progress.report("ready", "Pronto para revisar", pct=100)
    assert [s["label"] for s in outer] == ["Baixando 50%", "IA analisando bloco 1 de 3", "Pronto para revisar"]
    assert [s["label"] for s in inner] == ["IA analisando bloco 1 de 3"]
    assert progress.current() is None


def test_a_failing_sink_never_breaks_the_work():
    def boom(state):
        raise RuntimeError("db locked")
    seen = []
    with progress.use(boom, min_interval=0):
        with progress.use(seen.append):
            progress.report("signals", "Sinais", done=1, total=4)
    assert seen and seen[0]["label"] == "Sinais"


class FakePopen:
    lines: list[str] = []
    code = 0
    cmd = None

    def __init__(self, cmd, **kw):
        FakePopen.cmd = cmd
        self.stdout = iter(self.lines)
        self.returncode = None

    def wait(self):
        self.returncode = self.code
        return self.code

    def poll(self):
        return self.returncode

    def kill(self):
        pass


def test_ytdlp_run_reports_download_percent_per_part(monkeypatch):
    FakePopen.lines = ["[youtube] x: Downloading webpage\n", "[download] Destination: live_x.f137.mp4\n",
                       "[download]  10.0% of 1GiB\r[download]  55.5% of 1GiB\n", "[download] 100% of 1GiB\n",
                       "[download] Destination: live_x.f140.m4a\n", "[download]  20.0% of 50MiB\n",
                       "ERROR: nothing\n"]
    FakePopen.code = 0
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    seen = []
    with progress.use(seen.append, min_interval=0):
        r = progress.ytdlp_run(["yt-dlp", "-f", "b", "URL"], timeout=60)
    assert FakePopen.cmd[:2] == ["yt-dlp", "--newline"]
    assert r.returncode == 0 and "ERROR: nothing" in r.stderr and "ERROR: nothing" in r.stdout
    labels = [s["label"] for s in seen]
    assert labels == ["Baixando 10%", "Baixando 56%", "Baixando 100%", "Baixando 20% (parte 2)"]
    assert seen[1]["pct"] == 55.5 and all(s["step"] == "download" for s in seen)


def test_ingest_download_uses_the_reporting_runner(tmp_path, monkeypatch):
    from cvm.lives import ingest

    calls = []

    def fake(cmd, timeout=None, **k):
        calls.append(cmd)
        (tmp_path / "live_AAAAAAAAAA1.mp4").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(progress, "ytdlp_run", fake)
    live = {"video_id": "AAAAAAAAAA1", "url": "https://www.youtube.com/watch?v=AAAAAAAAAA1"}
    assert ingest.download(live, tmp_path).name == "live_AAAAAAAAAA1.mp4" and len(calls) == 1


def test_proxy_reporter_turns_ffmpeg_time_into_percent():
    seen = []
    with progress.use(seen.append, min_interval=0):
        on = cuts.proxy_reporter(200.0)
        for line in ("frame=10", "out_time_us=N/A", "out_time_us=50000000", "out_time=00:02:30.000000"):
            on(line)
        cuts.proxy_reporter(0.0)("out_time_us=90000000")  # length unknown: no percentage
    assert [s["label"] for s in seen] == ["Versão leve 25%", "Versão leve 75%", "Versão leve (1 min prontos)"]
    assert seen[1]["done"] == 150 and seen[1]["total"] == 200 and seen[2]["pct"] is None


def test_transcript_reporter_and_whisper_bar_swap(monkeypatch):
    from cvm.lives import signals
    from cvm.render import captions

    seen = []
    with progress.use(seen.append, min_interval=0):
        signals.transcript_reporter()(4332.0, 10920.0)
    assert seen[-1]["label"] == "Transcrevendo 1h12 de 3h02" and seen[-1]["pct"] == pytest.approx(39.7, abs=0.1)

    # mlx_whisper's tqdm bar (100 frames per second of audio) is swapped only while the call runs
    real = types.SimpleNamespace(tqdm=object)
    mod = types.ModuleType("mlx_whisper.transcribe")
    mod.tqdm = real
    monkeypatch.setitem(sys.modules, "mlx_whisper.transcribe", mod)
    got = []
    verbose, bar = captions._mlx_progress(lambda d, t: got.append((d, t)))
    assert verbose is False
    with bar:
        with mod.tqdm.tqdm(total=360000, unit="frames", disable=False) as pbar:
            pbar.update(3000)
            pbar.update(3000)
    assert mod.tqdm is real and got == [(30.0, 3600.0), (60.0, 3600.0)]
    assert captions._mlx_progress(None)[0] is None  # no callback: whisper as before


def test_signals_transcript_reports_gpu_wait_then_progress(tmp_path, monkeypatch):
    from cvm.lives import signals

    monkeypatch.setattr(gpu, "lock", MemoryHuey("t").lock_task("gpu"))
    video = tmp_path / "v.mp4"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2",
                    str(video)], check=True)

    def transcribe(wav, language, words=False, on_progress=None):
        on_progress(1.0, 2.0)
        return {"words": [{"w": "oi.", "s": 0.1, "e": 0.5}]}
    seen = []
    with progress.use(seen.append, min_interval=0):
        lines = signals.transcript(video, "pt-BR", transcribe=transcribe)
    assert lines and lines[0]["text"] == "oi."
    labels = [s["label"] for s in seen]
    assert "Transcrição esperando a vez da GPU" in labels and seen[labels.index("Transcrição esperando a vez da GPU")]["waiting"]
    assert labels[-1] == "Transcrevendo 1 s de 2 s"


# --- storage and API -----------------------------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Channel(id=4, name="Studio", platform="youtube", language="pt-BR", youtube_channel_id="UCabc"))
        s.commit()
    return engine


def test_progress_column_is_added_to_an_old_database(tmp_path, monkeypatch):
    """The project's forward-only migration (cvm/db._add_missing_columns) adds CutReview.progress."""
    from sqlalchemy import inspect, text

    from cvm import db as cvm_db

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE cutreview (id INTEGER PRIMARY KEY, title VARCHAR)'))
        conn.execute(text("INSERT INTO cutreview (id, title) VALUES (1, 'x')"))
    monkeypatch.setattr(cvm_db, "engine", engine)
    cvm_db._add_missing_columns()
    assert "progress" in {c["name"] for c in inspect(engine).get_columns("cutreview")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT progress FROM cutreview WHERE id = 1")).scalar() == "{}"


def test_sinks_write_the_review_row_and_a_download_row(db):
    with Session(db) as s:
        r = CutReview(channel_id=4, item_key="AAAAAAAAAA1", title="Live", status="analysing")
        s.add(r)
        s.commit()
        rid = r.id
    with progress.use(progress.review_sink(db, rid), min_interval=0):
        with progress.use(progress.setting_sink(db, progress.lives_key(4, "AAAAAAAAAA1"))):
            progress.report("ai", "IA analisando bloco 4 de 15", pct=20, done=3, total=15)
    with Session(db) as s:
        assert s.get(CutReview, rid).progress["label"] == "IA analisando bloco 4 de 15"
        assert progress.get(s, "lives:4:AAAAAAAAAA1")["done"] == 3
        progress.clear(s, "lives:4:AAAAAAAAAA1")
        assert progress.get(s, "lives:4:AAAAAAAAAA1") is None


def test_mark_keeps_since_while_waiting(db):
    progress.mark(db, "fabrica:k1", progress.QUEUED, progress.SLOT_WAIT, waiting=True)
    with Session(db) as s:
        first = progress.get(s, "fabrica:k1")
    progress.mark(db, "fabrica:k1", progress.QUEUED, progress.SLOT_WAIT, waiting=True)
    with Session(db) as s:
        again = progress.get(s, "fabrica:k1")
    assert first["since"] == again["since"] and again["waiting"] and again["label"] == "esperando outro vídeo terminar de baixar"


def test_process_link_shows_download_progress_then_clears_it(db, tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "MIN_INTERVAL_S", 0.0)
    seen = []

    def fetch(live, dest):
        progress.report("download", "Baixando 40%", pct=40)
        with Session(db) as s2:  # what the page reads while the download runs
            seen.append(runs.status(s2, s2.get(Channel, 4))["items"][0].get("progress"))
        raise RuntimeError("o download falhou")
    with Session(db) as s:
        ch = s.get(Channel, 4)
        probe = lambda url: {"video_id": "AAAAAAAAAA1", "title": "Live", "url": url, "duration": 60.0,  # noqa: E731
                             "uploader": "Studio", "channel_id": "UCabc"}
        item = runs.process_link(s, ch, "https://www.youtube.com/watch?v=AAAAAAAAAA1", probe=probe, fetch=fetch)
        assert item["status"] == "error"
        assert progress.get(s, "lives:4:AAAAAAAAAA1") is None  # no longer working: its row is gone
    assert seen[0]["step"] == "download" and seen[0]["label"] == "Baixando 40%"


def _client(db):
    from cvm.api import cuts as cuts_api
    from cvm.api import projects as projects_api

    app = FastAPI()
    app.include_router(cuts_api.router)
    app.include_router(projects_api.router)

    def sess():
        with Session(db) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    return TestClient(app)


def test_api_lists_progress_for_cuts_prep_and_links(db):
    from cvm.models import now

    with Session(db) as s:
        a = CutReview(channel_id=4, item_key="AAAAAAAAAA1", title="Live 1", status="analysing",
                      progress={"step": "transcript", "label": "Transcrevendo 1h12 de 3h02", "pct": 39.7, "done": 4332,
                                "total": 10920, "since": now().isoformat(), "at": now().isoformat(), "waiting": False})
        b = CutReview(channel_id=4, item_key="BBBBBBBBBB2", title="Live 2", status="review",
                      progress={"step": "ready", "label": "Pronto para revisar", "pct": 100.0, "at": now().isoformat()})
        old = CutReview(channel_id=4, item_key="CCCCCCCCCC3", title="Old", status="review", progress={})
        s.add_all([a, b, old])
        s.add(Setting(key="lives:4", value={"items": [
            {"video_id": "DDDDDDDDDD4", "title": "Baixando agora", "status": "working"},
            {"video_id": "AAAAAAAAAA1", "title": "Live 1", "status": "working"},  # its review is analysing
            {"video_id": "EEEEEEEEEE5", "title": "Pronto", "status": "ok"}]}))
        s.add(Setting(key="fabrica_links", value={"items": [
            {"key": "k1", "title": "Novo", "project_ids": [1], "links": [{}], "status": "working", "error": None}]}))
        s.commit()
    progress.setting_sink(db, "lives:4:DDDDDDDDDD4")({"step": "download", "label": "Baixando 12%", "pct": 12.0})
    progress.setting_sink(db, "fabrica:k1")({"step": "proxy", "label": "Versão leve 50%", "pct": 50.0})
    cl = _client(db)

    items = cl.get("/cuts/prep").json()["items"]
    by = {i["title"]: i for i in items}
    assert set(by) == {"Baixando agora", "Live 1", "Live 2"}  # the old review and the finished item are left out
    assert by["Baixando agora"]["kind"] == "download" and by["Baixando agora"]["progress"]["pct"] == 12.0
    assert by["Live 1"]["kind"] == "review" and by["Live 1"]["review_id"] and by["Live 1"]["status"] == "analysing"
    assert by["Live 1"]["progress"]["label"] == "Transcrevendo 1h12 de 3h02"
    assert by["Live 2"]["status"] == "review" and by["Live 2"]["progress"]["step"] == "ready"
    assert set(by["Live 1"]) >= {"key", "kind", "title", "channel_id", "status", "progress", "review_id", "error"}

    listed = cl.get("/cuts", params={"status": "analysing"}).json()["items"]
    assert listed[0]["progress"]["step"] == "transcript"

    links = cl.get("/projects/links").json()
    assert links[0]["progress"]["label"] == "Versão leve 50%" and "links" not in links[0]


def test_analyse_reports_each_step_and_ends_ready(db, tmp_path, monkeypatch):
    """The real analyse() with the heavy parts faked: the row moves through the steps and ends "ready"."""
    monkeypatch.setattr(gpu, "lock", MemoryHuey("t").lock_task("gpu"))
    monkeypatch.setattr("cvm.lives.signals.comment_times", lambda vid, **k: [])
    from cvm.visuals import highlights
    monkeypatch.setattr(highlights, "analyse", lambda paths, durs: [
        {"segments": [{"start": 0.0, "end": 3.0, "score": 1.0}], "whole": True,
         "moments": [{"start": 0.0, "end": 3.0, "score": 2.0}], "dropped": {}, "tracks": {}}])
    video = tmp_path / "lives" / "4" / "live_AAAAAAAAAA1.mp4"
    video.parent.mkdir(parents=True)
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "3", "-shortest", str(video)], check=True)
    steps = []

    def detect(video, meta, title, own, language, duration, **k):
        progress.report("ai", "IA analisando bloco 1 de 2", pct=0, done=0, total=2)
        with Session(db) as s2:
            steps.append(s2.get(CutReview, 1).progress)
        return [{"start": 0.5, "end": 2.5, "score": 8.0, "suggested": True}], "viral"
    with Session(db) as s:
        ch = s.get(Channel, 4)
        live = {"video_id": "AAAAAAAAAA1", "title": "Live", "url": "https://www.youtube.com/watch?v=AAAAAAAAAA1",
                "duration": 3.0}
        r = cuts.analyse(s, ch, live, video, item_key="AAAAAAAAAA1", detect=detect)
        assert r.status == "review"
    with Session(db) as s:
        p = s.get(CutReview, r.id).progress
    assert p["step"] == "ready" and p["label"] == "Pronto para revisar" and p["pct"] == 100.0
    assert steps and steps[0]["label"] == "IA analisando bloco 1 de 2"
    assert (video.parent / "live_AAAAAAAAAA1_proxy480.mp4").exists()  # the proxy still runs through ffmpeg
