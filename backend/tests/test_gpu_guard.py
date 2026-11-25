import threading

from huey import MemoryHuey

from cvm import gpu

REAL_FREE = gpu.free_inprocess  # conftest stubs it for every test


def _lock(monkeypatch):
    h = MemoryHuey("test")
    monkeypatch.setattr(gpu, "lock", h.lock_task("gpu"))
    monkeypatch.setattr(gpu, "waiters", lambda: False)
    return h


def _hold_in_thread():
    taken, done = threading.Event(), threading.Event()

    def run():
        assert gpu.try_acquire()
        taken.set()
        done.wait(5)
        gpu.release()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    taken.wait(2)
    return done, t


def test_second_thread_refused_even_when_the_lock_key_is_flushed(monkeypatch):
    """2026-11-20: two LTX presenters ran at once in one worker; a flushed key must not let a second one start."""
    h = _lock(monkeypatch)
    done, t = _hold_in_thread()
    h.flush_locks()  # e.g. another consumer started with -f
    assert not gpu.lock.is_locked()
    assert not gpu.try_acquire()
    done.set()
    t.join(2)
    assert gpu.try_acquire()  # free again once the holder releases
    gpu.release()


def test_heavy_model_waits_for_free_memory(monkeypatch):
    _lock(monkeypatch)
    free = {"gb": 5.0}
    monkeypatch.setattr(gpu, "memory_short", lambda: free["gb"] if free["gb"] < 16 else None)
    assert not gpu.try_acquire()
    assert not gpu.lock.is_locked()
    free["gb"] = 30.0
    assert gpu.try_acquire()
    gpu.release()
    assert not gpu.lock.is_locked()


def test_reentrant_hold_skips_the_memory_check(monkeypatch):
    """Inside a step that already holds the GPU, a section must not wait for memory its own model is using."""
    _lock(monkeypatch)
    assert gpu.try_acquire()
    monkeypatch.setattr(gpu, "memory_short", lambda: 2.0)
    with gpu.section():
        pass
    gpu.release()
    assert not gpu.lock.is_locked()


def test_memory_short_off_on_zero(monkeypatch):
    from cvm.config import settings

    monkeypatch.setattr(settings, "gpu_min_free_gb", 0.0)
    assert gpu.memory_short() is None


def test_whole_step_jobs_leave_the_gpu_to_a_section_that_waited_too_long(monkeypatch):
    """2026-11-21: 541's composing section lost every free moment to the next narration/lipsync job (~1 h)."""
    from cvm.workers import tasks

    _lock(monkeypatch)
    monkeypatch.setattr(gpu, "YIELD_MAX_S", 0.2)
    monkeypatch.setattr(gpu, "memory_short", lambda: None)
    done, t = _hold_in_thread()  # a whole-step job holds the GPU
    entered = threading.Event()

    def sec():
        with gpu.section(poll_s=0.05):
            entered.set()

    s = threading.Thread(target=sec, daemon=True)
    s.start()
    import time

    time.sleep(0.4)
    assert gpu.section_starving()
    done.set()
    t.join(2)
    assert not tasks._acquire("gpu") or (gpu.release() and False)  # the next job in line must not jump ahead
    assert entered.wait(2)
    s.join(2)
    assert not gpu.section_starving()
    assert tasks._acquire("gpu")
    gpu.release()


def test_release_drops_in_process_voice_models(monkeypatch):
    """2026-11-21: the worker held 36 GB of voice models + MLX cache between steps, beside a 36 GB LTX run."""
    import cvm.models_registry as reg
    from cvm.voice import engine

    _lock(monkeypatch)
    monkeypatch.setattr(gpu, "free_inprocess", REAL_FREE)
    monkeypatch.setattr(reg, "MLX", True)
    calls = []
    monkeypatch.setattr(engine, "_voxcpm2", type("F", (), {"cache_clear": lambda self=None: calls.append("vx")})())
    assert gpu.try_acquire()
    assert gpu.try_acquire()  # reentrant: nothing dropped until the outer release
    gpu.release()
    assert calls == []
    gpu.release()
    assert calls == ["vx"]
