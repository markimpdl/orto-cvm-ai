import threading

from huey import MemoryHuey

from cvm import gpu


def _locks(monkeypatch, room: bool):
    h = MemoryHuey("test")
    monkeypatch.setattr(gpu, "lock", h.lock_task("gpu"))
    monkeypatch.setattr(gpu, "light_lock", h.lock_task("gpu-light"))
    monkeypatch.setattr(gpu, "_room_for_light", lambda: room)
    monkeypatch.setattr(gpu, "waiters", lambda: False)  # other tests may leave the worker hook set


def _holds_heavy():
    """Take the heavy lock in another thread (as an LTX lipsync would) and return a release event."""
    taken, done = threading.Event(), threading.Event()

    def run():
        with gpu.section():
            taken.set()
            done.wait(5)

    threading.Thread(target=run, daemon=True).start()
    taken.wait(2)
    return done


def test_light_runs_beside_a_heavy_model(monkeypatch):
    _locks(monkeypatch, room=True)
    done = _holds_heavy()
    ran = threading.Event()

    def light():
        with gpu.light(poll_s=0.01):
            ran.set()

    threading.Thread(target=light, daemon=True).start()
    assert ran.wait(1)  # did not wait for the heavy model
    done.set()


def test_light_waits_for_heavy_when_memory_is_short(monkeypatch):
    _locks(monkeypatch, room=False)
    done = _holds_heavy()
    ran = threading.Event()

    def light():
        with gpu.light(poll_s=0.01):
            ran.set()

    threading.Thread(target=light, daemon=True).start()
    assert not ran.wait(0.3)
    done.set()
    assert ran.wait(2)


def test_section_stops_yielding_to_waiters_after_a_while(monkeypatch):
    """A queue that always has a GPU waiter must not starve a visuals step forever."""
    _locks(monkeypatch, room=False)
    monkeypatch.setattr(gpu, "waiters", lambda: True)
    monkeypatch.setattr(gpu, "YIELD_MAX_S", 0.05)
    ran = threading.Event()

    def run():
        with gpu.section(poll_s=0.01):
            ran.set()

    threading.Thread(target=run, daemon=True).start()
    assert ran.wait(2)
