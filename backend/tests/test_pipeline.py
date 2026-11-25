import pytest

from cvm.models import Job
from cvm.pipeline import HUMAN_GATES, STATES, TRANSITIONS, InvalidTransition, advance, retry

HAPPY_PATH = [
    "scripting",
    "awaiting_approval",
    "voicing",
    "sourcing_visuals",
    "lipsyncing",
    "composing",
    "rendering",
    "seo",
    "awaiting_publish_review",
    "scheduled",
    "ready_for_manual_publish",
    "published",
]


def test_happy_path_manual_publish():
    job = Job(project_id=1)
    for state in HAPPY_PATH:
        advance(job, state)
    assert job.state == "published"
    assert len(job.history) == len(HAPPY_PATH)


def test_api_mode_skips_manual_gate():
    job = Job(project_id=1, state="scheduled")
    advance(job, "published")
    assert job.state == "published"


def test_cannot_skip_approval():
    job = Job(project_id=1, state="scripting")
    with pytest.raises(InvalidTransition):
        advance(job, "voicing")


def test_reject_sends_back_to_scripting():
    job = Job(project_id=1, state="awaiting_approval")
    advance(job, "scripting", "make the hook stronger")
    assert job.state == "scripting"


def test_fail_and_retry():
    job = Job(project_id=1, state="lipsyncing")
    advance(job, "failed", "OOM")
    assert job.error == "OOM"
    retry(job)
    assert job.state == "lipsyncing" and job.error is None


def test_published_is_terminal():
    job = Job(project_id=1, state="published")
    with pytest.raises(InvalidTransition):
        advance(job, "failed")


def test_state_tables_consistent():
    assert set(TRANSITIONS) == set(STATES)
    assert HUMAN_GATES <= set(STATES)


def test_worked_time_recorded_and_used_for_estimates():
    from datetime import timedelta

    from cvm import progress
    from cvm.models import now
    from cvm.pipeline import DEFAULT_SECONDS, typical_durations

    job = Job(project_id=1, state="voicing")
    progress.start(job)
    job.progress["started"] = (now() - timedelta(seconds=90)).isoformat()
    advance(job, "sourcing_visuals")
    assert 89 <= job.history[-1]["worked_s"] <= 95 and job.progress == {}
    est = typical_durations([job])
    assert 89 <= est["voicing"] <= 95 and est["seo"] == DEFAULT_SECONDS["seo"]


def test_progress_parts_split_fraction():
    from cvm import progress

    class FakeSession:
        def add(self, _): ...
        def commit(self): ...

    job = Job(project_id=1, state="voicing")
    progress.start(job)
    with progress.tracking(job, FakeSession()):
        for lang in progress.parts(["pt-BR", "en-US"]):
            if lang == "en-US":
                progress.report(1, 2, "en-US: frase 2/2")
    assert job.progress["fraction"] == 0.75 and job.progress["label"] == "en-US: frase 2/2"


def test_queue_info_positions_and_waiting_for():
    from cvm.queueing import queue_info

    jobs = [
        Job(id=1, project_id=1, state="voicing", progress={"started": "2026-01-01T00:00:00+00:00", "pid": 1}),
        Job(id=2, project_id=2, state="voicing", progress={"waiting": "gpu", "since": "2026-01-01T00:02:00+00:00", "checked": "x"}),
        Job(id=3, project_id=3, state="voicing", progress={"waiting": "gpu", "since": "2026-01-01T00:01:00+00:00", "checked": "x"}),
        Job(id=4, project_id=4, state="scripting", progress={"waiting": "claude", "since": "2026-01-01T00:00:00+00:00", "checked": "x"}),
        Job(id=5, project_id=5, state="awaiting_approval"),
    ]
    d = {"voicing": 100, "sourcing_visuals": 100, "lipsyncing": 100, "composing": 100, "scripting": 300}
    q = queue_info(jobs, d, claude_slots=2)
    assert q[3]["queue_position"] == 1 and q[2]["queue_position"] == 2 and q[2]["eta_s"] - q[3]["eta_s"] == 200  # GPU and prep steps of two videos overlap
    assert q[4] == {"waiting_for": "claude", "queue_position": 1, "eta_s": 300}
    assert q[5]["waiting_for"] == "you" and q[1]["waiting_for"] is None


def test_run_job_request_during_active_run_is_not_lost(monkeypatch):
    from cvm.workers import tasks

    calls = []

    def fake_drive(job_id, held):
        calls.append(job_id)
        if len(calls) == 1:
            tasks.run_job.call_local(job_id)  # arrives while the first run is still active

    monkeypatch.setattr(tasks, "_drive", fake_drive)
    tasks.run_job.call_local(7)
    assert calls == [7, 7] and 7 not in tasks._active and 7 not in tasks._rerun


def test_videos_further_along_go_first_in_production_line():
    from cvm.workers.tasks import line_key

    early = Job(id=1, project_id=1, state="voicing", progress={"waiting": "gpu", "since": "2026-01-01T00:00:00+00:00"})
    later = Job(id=2, project_id=2, state="lipsyncing", progress={"waiting": "gpu", "since": "2026-01-01T00:05:00+00:00"})
    assert line_key(later) < line_key(early)


def test_line_order_survives_a_worker_restart():
    """A video interrupted mid-visuals starts a new wait on restart; it still goes before videos that entered
    visuals after it."""
    from cvm.workers.tasks import line_key

    old = Job(id=32, project_id=32, state="sourcing_visuals",
              history=[{"from": "voicing", "to": "sourcing_visuals", "at": "2026-01-01T08:00:00+00:00"}],
              progress={"waiting": "prep", "since": "2026-01-01T12:00:00+00:00"})
    new = Job(id=36, project_id=36, state="sourcing_visuals",
              history=[{"from": "voicing", "to": "sourcing_visuals", "at": "2026-01-01T11:00:00+00:00"}],
              progress={"waiting": "prep", "since": "2026-01-01T11:00:00+00:00"})
    assert line_key(old) < line_key(new)


def test_gpu_lock_is_exclusive_across_threads_and_reentrant_in_one(monkeypatch):
    import threading

    from huey import MemoryHuey

    from cvm import gpu

    monkeypatch.setattr(gpu, "lock", MemoryHuey("test").lock_task("gpu"))  # never touch the live queue's lock
    assert gpu.try_acquire()
    with gpu.section():  # the holder can call helpers that also ask for it
        assert gpu.held()
    other = []
    t = threading.Thread(target=lambda: other.append(gpu.try_acquire()))
    t.start()
    t.join()
    assert other == [False]  # another thread (another job) must wait
    gpu.release()
    assert not gpu.held()
    t = threading.Thread(target=lambda: (other.append(gpu.try_acquire()), gpu.release()))
    t.start()
    t.join()
    assert other[-1] is True


def test_typical_durations_skips_hand_made_history_entries():
    """A job inserted by hand with {"state", "at"} entries (no from/to) must not break GET /projects."""
    from types import SimpleNamespace

    from cvm.pipeline import DEFAULT_SECONDS, typical_durations

    job = SimpleNamespace(history=[{"state": "trend_selected", "at": "2026-11-23 22:46:48"},
                                   {"state": "seo", "at": "2026-11-23 22:46:48"},
                                   {"from": "seo", "to": "awaiting_publish_review", "at": "2026-11-23T22:47:18+00:00",
                                    "worked_s": 17.0}])
    assert typical_durations([job])["voicing"] == DEFAULT_SECONDS["voicing"]
