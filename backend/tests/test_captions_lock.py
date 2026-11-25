"""Two worker threads running mlx-whisper at once crashed the worker (numba JIT compile race → SIGABRT, 2026-11-22)."""

import sys
import threading
import time
import types
from pathlib import Path




def test_transcribe_never_runs_two_whispers_at_once(monkeypatch):
    from cvm import models_registry
    from cvm.render import captions

    monkeypatch.setattr(models_registry, "MLX", True)
    state = {"now": 0, "max": 0}
    guard = threading.Lock()

    def fake(*a, **k):
        with guard:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        with guard:
            state["now"] -= 1
        return {"text": "oi", "segments": []}

    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=fake))
    ts = [threading.Thread(target=captions.transcribe, args=(Path("a.wav"), "pt-BR")) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert state["max"] == 1
