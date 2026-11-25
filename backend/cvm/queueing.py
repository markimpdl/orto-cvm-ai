"""Where each job stands in the shared queues (GPU, Claude), for the UI: position, what it waits for, ETA."""

from math import ceil

from cvm import pipeline
from cvm.models import Job

PRODUCTION = ["voicing", "sourcing_visuals", "lipsyncing", "composing"]  # production steps, in order
GPU_STEPS, PREP_STEPS = ("voicing", "lipsyncing"), ("sourcing_visuals", "composing")
# "terminar é mais importante que começar" (user, 2026-11-22): above this many unfinished videos nothing new is
# created on its own (daily auto cuts, autopilot); the user's own picks still go in
WIP_MAX = 15
DONE_STATES = {"published", "scheduled", "awaiting_publish_review", "ready_for_manual_publish", "failed"}


def unfinished(session) -> int:
    """Videos started and not yet handed to the user for publishing (paused ones count: they are still open)."""
    from sqlmodel import col, func, select

    return session.exec(select(func.count()).select_from(Job).where(col(Job.state).not_in(DONE_STATES))).one()


def too_busy(session) -> bool:
    return unfinished(session) >= WIP_MAX


def _elapsed_s(job: Job) -> float:
    started = (job.progress or {}).get("started")
    return pipeline.seconds_since(started) if started else 0.0


def queue_info(jobs: list[Job], durations: dict[str, float], claude_slots: int) -> dict[int, dict]:
    """{job_id: {waiting_for: "you"|"gpu"|"prep"|"claude"|None, queue_position, eta_s}} for every job."""
    info = {j.id: {"waiting_for": "you" if j.state in pipeline.HUMAN_GATES else None, "queue_position": None, "eta_s": None}
            for j in jobs}
    # GPU steps of one video overlap prep steps of another, so a video leaves the line about every `pace` s.
    pace = max(sum(durations.get(s, 0) for s in GPU_STEPS), sum(durations.get(s, 0) for s in PREP_STEPS))
    busy = [j for j in jobs if j.state in PRODUCTION and (j.progress or {}).get("started")]
    holder_left = min((max(0.0, sum(durations.get(s, 0) for s in PRODUCTION[PRODUCTION.index(j.state):]) - _elapsed_s(j))
                       for j in busy), default=0.0)

    from cvm.workers.tasks import line_key  # same order as the worker

    production = sorted((j for j in jobs if (j.progress or {}).get("waiting") in ("gpu", "prep")), key=line_key)
    for pos, j in enumerate(production, 1):
        info[j.id] = {"waiting_for": j.progress["waiting"], "queue_position": pos, "eta_s": round(holder_left + (pos - 1) * pace)}
    claude = sorted((j for j in jobs if (j.progress or {}).get("waiting") == "claude"), key=lambda j: (j.progress["since"], j.id))
    for pos, j in enumerate(claude, 1):  # all slots are busy; each wave of `claude_slots` steps takes about one run
        info[j.id] = {"waiting_for": "claude", "queue_position": pos, "eta_s": round(ceil(pos / claude_slots) * durations.get(j.state, 300))}
    return info
