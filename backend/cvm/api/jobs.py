import shlex

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from cvm import engines, pipeline
from cvm.config import settings
from cvm.db import get_session
from cvm.models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


class TransitionIn(BaseModel):
    to: str
    note: str | None = None


@router.get("")
def list_jobs(state: str | None = None, session: Session = Depends(get_session)):
    q = select(Job).order_by(col(Job.updated_at).desc())
    if state:
        q = q.where(Job.state == state)
    return session.exec(q).all()


@router.get("/meta/states")
def states():
    return {"states": pipeline.STATES, "human_gates": sorted(pipeline.HUMAN_GATES)}


@router.get("/meta/claude-limit")
def claude_limit_state(session: Session = Depends(get_session)):
    """{until, since, message} while the Claude subscription limit holds the Claude steps; null otherwise."""
    from cvm import claude_limit

    return claude_limit.state(session) if claude_limit.blocked_until(session) else None


@router.get("/meta/durations")
def durations(session: Session = Depends(get_session)):
    """Typical seconds per timed step, for the UI's progress estimates."""
    return pipeline.typical_durations(session.exec(select(Job)).all())


@router.post("/{job_id}/transition")
def transition(job_id: int, body: TransitionIn, session: Session = Depends(get_session)):
    """Human actions: approve script, send back, approve render, mark manually published."""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404)
    redo = job.state in {"scheduled", "ready_for_manual_publish"} and body.to in {"composing", "voicing", "scripting"}
    try:
        pipeline.advance(job, body.to, body.note)
    except pipeline.InvalidTransition as e:
        raise HTTPException(409, str(e)) from e
    if redo:  # the old kits point at the render being redone: drop the ones not posted yet
        from cvm.models import Publication, Render

        for pub in session.exec(select(Publication).join(Render, Publication.render_id == Render.id)
                                .where(Render.project_id == job.project_id, Publication.status != "published")).all():
            session.delete(pub)
    session.add(job)
    session.commit()
    session.refresh(job)

    from cvm.workers.tasks import run_job

    run_job(job.id)
    return job


@router.post("/{job_id}/retry")
def retry(job_id: int, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404)
    try:
        pipeline.retry(job)
    except pipeline.InvalidTransition as e:
        raise HTTPException(409, str(e)) from e
    session.add(job)
    session.commit()

    from cvm.workers.tasks import run_job

    run_job(job.id)
    return job


class PauseIn(BaseModel):
    paused: bool


@router.post("/pause-all")
def pause_all(body: PauseIn, session: Session = Depends(get_session)):
    """Pause (or resume) every video still being produced; each running one stops after its current step."""
    from cvm.workers.tasks import run_job, running_states

    jobs = session.exec(select(Job).where(col(Job.state).in_(running_states()))).all()
    changed = [j for j in jobs if bool(j.paused) != body.paused]
    for j in changed:
        j.paused = body.paused
        session.add(j)
    session.commit()
    if not body.paused:
        for j in changed:
            run_job(j.id)
    return {"changed": [j.project_id for j in changed]}


@router.post("/{job_id}/pause")
def pause(job_id: int, body: PauseIn, session: Session = Depends(get_session)):
    """Pause a queued video (it stops after the step it is running) or let it continue."""
    from cvm.workers.tasks import run_job

    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404)
    job.paused = body.paused
    session.add(job)
    session.commit()
    session.refresh(job)
    if not body.paused:
        run_job(job.id)
    return job


@router.post("/{job_id}/resume")
def resume(job_id: int, session: Session = Depends(get_session)):
    """Rerun an automatic step whose worker died mid-step."""
    from cvm.progress import orphaned
    from cvm.workers.tasks import run_job, running_states

    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404)
    if not orphaned(job, running_states()):
        raise HTTPException(409, "a etapa ainda está rodando")
    run_job(job.id)
    return {"queued": True}


@router.get("/{job_id}/claude-command")
def claude_command(job_id: int, session: Session = Depends(get_session)):
    """Command the user can paste in a terminal to run the current agent step manually."""
    from cvm.scripting.claude_runner import build_prompt

    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404)
    prompt = build_prompt(job)
    if not prompt:
        raise HTTPException(409, f"no Claude step for state {job.state}")
    return {"command": f"claude -p {shlex.quote(prompt)}", "prompt": prompt}


@router.post("/{job_id}/run-claude")
def run_claude_now(job_id: int, session: Session = Depends(get_session)):
    """Manual trigger: run the current agent step with Claude Code (subscription) in the background."""
    from cvm.scripting.claude_runner import build_prompt
    from cvm.workers.tasks import run_claude_step

    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404)
    if not build_prompt(job):
        raise HTTPException(409, f"no Claude step for state {job.state}")
    if engines.auto_run():  # normally already running/queued; this restarts a stuck step in its turn
        from cvm.progress import orphaned
        from cvm.workers.tasks import run_job, running_states

        if (job.progress or {}).get("started") and not orphaned(job, running_states()):
            raise HTTPException(409, "o Claude já está trabalhando nesta etapa")
        run_job(job_id)
        return {"queued": True}
    run_claude_step(job_id)
    return {"queued": True}
