"""Live progress of the pipeline step a job is running, stored on Job.progress for the UI's estimates.

The worker wraps each handler in `tracking(job, session)`; stage code calls `report(done, total, label)`
(a no-op outside a job) and `parts(items)` to split the step between languages/renders.
"""

import os
import time
from datetime import UTC, datetime
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TypeVar

from sqlmodel import Session

from cvm.models import Job, now

T = TypeVar("T")
MIN_INTERVAL_S = 2.0  # don't commit more often than this

_current: ContextVar[dict | None] = ContextVar("progress", default=None)


def start(job: Job) -> None:
    """Mark the moment the current step started working (not when it began waiting), and which process runs it."""
    t = now().isoformat()
    job.progress = {"started": t, "at": t, "fraction": 0.0, "pid": os.getpid()}


def wait(job: Job, kind: str) -> None:
    """Mark the job as waiting for a slot ("gpu" | "claude"); `since` keeps its place in line across retries."""
    p, t = job.progress or {}, now().isoformat()
    job.progress = {"waiting": kind, "since": p["since"] if p.get("waiting") == kind else t, "checked": t}


def _age_s(iso: str) -> float:
    return (now() - datetime.fromisoformat(iso)).total_seconds()


def orphaned(job: Job, work_states: set[str]) -> bool:
    """True when an automatic step has no live process behind it (worker restarted or crashed mid-step)."""
    if job.state not in work_states or job.paused:
        return False
    p = job.progress or {}
    if p.get("waiting") == "claude_limit":  # parked until the reset; claude_limit_tick puts it back in line
        return False
    if p.get("waiting"):  # a waiting job re-checks its turn every few seconds
        return _age_s(p["checked"]) > 90
    pid = p.get("pid")
    if not pid:  # never started: just queued, unless it has sat like that for a while
        return (now() - job.updated_at.replace(tzinfo=UTC)).total_seconds() > 120
    import psutil  # not os.kill(pid, 0): on Windows signal 0 is CTRL_C_EVENT, not a liveness probe

    return not psutil.pid_exists(pid)


@contextmanager
def tracking(job: Job, session: Session) -> Iterator[None]:
    token = _current.set({"job": job, "session": session, "part": 0, "parts": 1, "last": 0.0})
    try:
        yield
    finally:
        _current.reset(token)


def parts(items: Iterable[T]) -> Iterator[T]:
    """Iterate items, treating each as an equal share of the step's progress."""
    items = list(items)
    ctx = _current.get()
    for i, item in enumerate(items):
        if ctx:
            ctx["part"], ctx["parts"] = i, len(items)
        yield item
    if ctx:
        ctx["part"], ctx["parts"] = 0, 1


def report(done: int, total: int, label: str | None = None) -> None:
    ctx = _current.get()
    if not ctx or total <= 0:
        return
    t = time.monotonic()
    if done < total and t - ctx["last"] < MIN_INTERVAL_S:
        return
    ctx["last"] = t
    fraction = (ctx["part"] + min(done, total) / total) / ctx["parts"]
    job = ctx["job"]
    job.progress = {**(job.progress or {}), "fraction": round(fraction, 3), "label": label, "at": now().isoformat()}
    ctx["session"].add(job)
    ctx["session"].commit()
