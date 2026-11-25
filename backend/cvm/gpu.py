"""The one heavy-model lock (48 GB unified memory: never two big models at once).

The queue holds it for whole GPU states (narration, lipsync). Steps that are mostly downloads or ffmpeg
(visuals, composing) run beside them and borrow it only around the heavy calls (FLUX, Wan, LTX, lipsync, voice)
with `section()`. Small models (matting) use `light()`: on the Mac they run beside the heavy one when memory
allows, one at a time. The lock lives in Huey's storage, so it is shared across processes; it is reentrant per
thread so a holder can call helpers that also ask for it.
"""

import logging
import threading
import time
from contextlib import contextmanager

from huey.exceptions import TaskLockedException

from cvm.workers.queue import huey

lock = huey.lock_task("gpu")
_local = threading.local()
# set by the worker: True while a whole-step GPU job (narration, lipsync) waits in line. A section lets it go
# first, otherwise a long run of back-to-back FLUX calls (minutes each on an 8 GB card) starves it for hours.
waiters = lambda: False  # noqa: E731


# Thread of this process that holds the lock. Checked before the shared key, so two threads of one worker never
# both run a heavy model even if the key is erased from outside (a consumer started with -f flushes every lock):
# on 2026-11-20 two LTX presenters (jobs 536, 538) ran at once in one worker and the Mac ran out of memory.
_owner: int | None = None
_owner_guard = threading.Lock()
_log = logging.getLogger(__name__)
_short_logged = 0.0


def held() -> bool:
    return getattr(_local, "depth", 0) > 0


def memory_short() -> float | None:
    """Free RAM in GB when it is below settings.gpu_min_free_gb on the Mac (a heavy model must not start), else
    None. CUDA cards have their own VRAM: no check there."""
    from cvm.config import settings
    from cvm.models_registry import MLX

    if not MLX or settings.gpu_min_free_gb <= 0:
        return None
    import psutil

    free = psutil.virtual_memory().available / 2**30
    return free if free < settings.gpu_min_free_gb else None


def try_acquire() -> bool:
    global _owner, _short_logged
    if held():
        _local.depth += 1
        return True
    me = threading.get_ident()
    with _owner_guard:
        if _owner is not None and _owner != me:
            if not lock_present():
                _log.error("GPU lock key vanished while thread %s holds it; refusing a second heavy model", _owner)
            return False
        if (free := memory_short()) is not None:
            if time.monotonic() - _short_logged > 300:
                _log.warning("heavy model waits for memory: %.1f GB free, needs %.0f", free, _min_free())
                _short_logged = time.monotonic()
            return False
        try:
            lock.__enter__()
        except TaskLockedException:
            return False
        _owner = me
    _local.depth = 1
    return True


def release() -> None:
    global _owner
    _local.depth -= 1
    if _local.depth == 0:
        free_inprocess()  # before the next heavy model can start
        with _owner_guard:
            _owner = None
            lock.__exit__(None, None, None)


def free_whisper() -> None:
    """Drop only mlx-whisper's model and MLX's free buffers (used after light work; never touches the voice models a
    heavy step may be using at the same time)."""
    try:
        import gc

        from mlx_whisper.transcribe import ModelHolder

        ModelHolder.model, ModelHolder.model_path = None, None
        gc.collect()
        import mlx.core as mx

        mx.clear_cache()
    except Exception:  # noqa: BLE001
        pass


def free_inprocess() -> None:
    """Drop the models the worker keeps inside its own process (VoxCPM2, Whisper) and MLX's buffer
    cache. On 2026-11-21 the worker held 36 GB after a day of narrations (models + freed buffers MLX keeps for
    reuse) while an LTX run took another 36 GB: swap 30 of 31 GB, the same road as the 2026-11-20 panic.
    Reloading a voice model costs seconds; the memory gate only sees what is really free."""
    from cvm.models_registry import MLX

    if not MLX:
        return  # CUDA: models_registry.cuda_loader evicts by group and idle time
    try:
        import gc

        from cvm.voice import engine

        engine._voxcpm2.cache_clear()
        try:
            from mlx_whisper.transcribe import ModelHolder

            ModelHolder.model, ModelHolder.model_path = None, None
        except Exception:  # noqa: BLE001
            pass
        gc.collect()
        import mlx.core as mx

        mx.clear_cache()
    except Exception:  # noqa: BLE001 — freeing memory must never break a step
        _log.exception("could not free in-process models")


def lock_present() -> bool:
    try:
        return lock.is_locked()
    except Exception:  # noqa: BLE001
        return True


def _min_free() -> float:
    from cvm.config import settings

    return settings.gpu_min_free_gb


light_lock = huey.lock_task("gpu-light")
LIGHT_FREE_GB = 6.0  # a light model starts beside a heavy one only with this much memory free


def _room_for_light() -> bool:
    """On the Mac (48 GB unified memory) a small model fits next to LTX/FLUX; on a CUDA card VRAM is tight, so
    light work there still takes the heavy lock."""
    from cvm.models_registry import MLX

    if not MLX:
        return False
    import psutil

    return psutil.virtual_memory().available / 2**30 >= LIGHT_FREE_GB


@contextmanager
def light(poll_s: float = 1.0):
    """A small model (matting: RobustVideoMatting, a few MB) may run beside the heavy one instead of waiting
    behind a half-hour LTX lipsync. One light job at a time; when memory is short (or on CUDA) it falls back to
    the heavy lock like section()."""
    if held():  # already ours (inside a GPU step): nothing to wait for
        yield
        return
    while True:
        if _room_for_light():
            try:
                light_lock.__enter__()
            except TaskLockedException:
                time.sleep(poll_s)
                continue
            try:
                yield
            finally:
                light_lock.__exit__(None, None, None)
            return
        if try_acquire():
            try:
                yield
            finally:
                release()
            return
        time.sleep(poll_s)


# how long a section lets waiting whole-step jobs go first. Without a limit a visuals step starved for hours once
# the queue was full (a GPU waiter was always in line), holding both prep slots and stalling every other video.
YIELD_MAX_S = 300.0


# sections waiting for the lock: thread -> since. Whole-step jobs leave the GPU to one waiting longer than
# YIELD_MAX_S: on 2026-11-21 a composing step (541, gaze check + one LTX chunk) lost every free moment to the next
# narration/lipsync job in line and waited ~1 h each time.
_section_waits: dict[int, float] = {}


def section_starving() -> bool:
    """True while a section has waited longer than YIELD_MAX_S: whole-step jobs must not take the lock now."""
    now = time.monotonic()
    return any(now - t0 > YIELD_MAX_S for t0 in list(_section_waits.values()))


@contextmanager
def section(poll_s: float = 1.0):
    """Block until the GPU is free (or already ours), run the heavy call, give it back. Waiting whole-step jobs
    go first, for at most YIELD_MAX_S; after that the section takes the lock as soon as it is free, and new
    whole-step jobs wait for it (`section_starving`)."""
    t0 = time.monotonic()
    me = threading.get_ident()
    if not held():
        _section_waits[me] = t0
    try:
        while (not held() and waiters() and time.monotonic() - t0 < YIELD_MAX_S) or not try_acquire():
            time.sleep(poll_s)
    finally:
        _section_waits.pop(me, None)
    try:
        yield
    finally:
        release()
