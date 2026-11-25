"""Queue tasks. Three kinds of slot:
- "gpu": narration and lipsync hold the heavy-model lock (cvm.gpu) for the whole step;
- "prep": visuals and composing (mostly downloads/ffmpeg), PREP_SLOTS at once, beside a GPU step; they borrow
  the GPU lock only around FLUX/Wan/lipsync/matting calls, so two big models never run together;
- "claude": script and SEO on the user's subscription, at most CLAUDE_SLOTS at once.
Jobs waiting for a slot are re-checked every RETRY_S seconds; videos further along go first, then arrival order."""

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from huey import crontab
from sqlmodel import Session, select

from cvm import claude_limit, engines, gpu, pipeline, progress
from cvm.config import settings
from cvm.db import engine
from cvm.models import Job
from cvm.workers.queue import huey

log = logging.getLogger(__name__)

# state -> handler(job, session) that does the work and returns the next state.
# Handlers for later phases (voice, visuals, lipsync, render) are registered as they are built.
HANDLERS: dict = {}
GPU_STATES = {"voicing", "lipsyncing"}
PREP_STATES = {"sourcing_visuals", "composing"}
CLAUDE_STATES = {"scripting", "seo"}
# states whose jobs may wait for the Claude limit: the Claude steps plus the visuals step, which writes the news-desk
# scene list of older editorial scripts with a light Claude call (cvm/scripting/editorial_backfill.py)
LIMIT_WAIT_STATES = CLAUDE_STATES | {"sourcing_visuals"}
LIMIT_STEP = {"sourcing_visuals": "editorial"}  # the writer step a waiting state asks for
WORK_STATES = GPU_STATES | PREP_STATES | {"rendering", "validating"}  # automatic steps whose duration the UI estimates
PRODUCTION = ["voicing", "sourcing_visuals", "lipsyncing", "composing"]  # in pipeline order
CLAUDE_SLOTS = 3
PREP_SLOTS = 4  # a kids video can spend hours in visuals (Wan per scene); keep another lane open
RETRY_S = 10
HOLD_RETRY_S = 60  # previous-episode hold; under the 90 s after which a waiting job looks orphaned

_claude_slots = threading.BoundedSemaphore(CLAUDE_SLOTS)
_prep_slots = threading.BoundedSemaphore(PREP_SLOTS)
KIND_STATES = {"gpu": GPU_STATES, "prep": PREP_STATES, "claude": CLAUDE_STATES}
_active: set[int] = set()  # jobs a run_job call is driving right now (guards against duplicate tasks)
_rerun: set[int] = set()  # run_job requests that arrived while the job was active
_active_lock = threading.Lock()


def running_states() -> set[str]:
    """States where some process should be working on the job (used to spot interrupted jobs)."""
    return WORK_STATES | (CLAUDE_STATES if engines.auto_run() else set())


def slot_kind(state: str) -> str | None:
    if state in GPU_STATES:
        return "gpu"
    if state in PREP_STATES:
        return "prep"
    if state in CLAUDE_STATES and engines.auto_run():
        return "claude"
    return None


_prio_cache: dict = {"at": 0.0, "ids": set()}


def priority_projects() -> set[int]:
    """Projects the user put ahead of the line (Setting "priority_projects" {"projects": [...]}, e.g. a trend with
    a short window); re-read every 30 s."""
    import time as _t

    if _t.monotonic() - _prio_cache["at"] > 30:
        try:
            from cvm.db import engine as _engine
            from cvm.models import Setting

            with Session(_engine) as s:
                row = s.get(Setting, "priority_projects")
                _prio_cache["ids"] = set((row.value or {}).get("projects") or []) if row else set()
        except Exception:  # noqa: BLE001
            pass
        _prio_cache["at"] = _t.monotonic()
    return _prio_cache["ids"]


def line_key(job: Job) -> tuple:
    """Order in a slot's line: videos the user put ahead first, then videos further along (so started videos
    finish), then arrival in the state — not the start of the current wait, which a worker restart resets for the
    videos it interrupted."""
    stage = PRODUCTION.index(job.state) if job.state in PRODUCTION else 0
    return (0 if job.project_id in priority_projects() else 1, -stage, entered_state(job), job.id)


def entered_state(job: Job) -> str:
    # .get: a history entry written by hand without "at" must not crash the line order for the whole queue
    return next((h["at"] for h in reversed(job.history or []) if h.get("to") == job.state and h.get("at")),
                (job.progress or {}).get("since", ""))


def _first_in_line(session: Session, job: Job, kind: str) -> bool:
    mine = line_key(job)
    return not any(
        (j.progress or {}).get("waiting") == kind and line_key(j) < mine
        for j in session.exec(select(Job).where(Job.state.in_(KIND_STATES[kind]), Job.id != job.id)).all()
    )


def _gpu_waiters() -> bool:
    """A whole-step GPU job is waiting and still polling (paused or abandoned ones don't count)."""
    with Session(engine) as session:
        return any(
            not j.paused and (p := j.progress or {}).get("waiting") == "gpu" and progress._age_s(p["checked"]) < 3 * RETRY_S
            for j in session.exec(select(Job).where(Job.state.in_(GPU_STATES))).all()
        )


gpu.waiters = _gpu_waiters


def _acquire(kind: str) -> bool:
    if kind == "claude":
        return _claude_slots.acquire(blocking=False)
    if kind == "prep":
        return _prep_slots.acquire(blocking=False)
    if gpu.section_starving():  # a composing/visuals step has waited its turn long enough: it goes first
        return False
    return gpu.try_acquire()


def _release(kind: str) -> None:
    if kind == "claude":
        _claude_slots.release()
    elif kind == "prep":
        _prep_slots.release()
    else:
        gpu.release()


def handler(state: str):
    def register(fn):
        HANDLERS[state] = fn
        return fn

    return register


@handler("trend_selected")
def _start(job: Job, session: Session) -> str:
    return "scripting"


def _claude_step(job: Job, session: Session) -> None:
    """Run the agent for the current step; it hands the job back itself via the cvm MCP tool."""
    from cvm.scripting.claude_runner import build_prompt

    state = job.state
    if state == "scripting":
        from cvm.models import Project
        from cvm.visuals.highlights import refresh_project

        p = session.get(Project, job.project_id)
        if p and p.media_cut == "highlights":
            refresh_project(p.id)  # best moments of the user's videos, before the script is written
    from cvm.scripting import agents

    since = _last_script_id(session, job.project_id) if state == "scripting" else 0
    result = agents.run_job_step(job, session, build_prompt(job))  # Claude; Codex at Claude's limit when allowed
    session.refresh(job)
    if job.state == state and state == "scripting" and _scripted_every_language(session, job.project_id, since):
        # the agent writes and reviews; handing the script to the user is the system's job, on both paths
        summary = str(result.get("result") or "").strip().splitlines()
        pipeline.advance(job, "awaiting_approval", (summary[-1] if summary else "roteiro pronto")[:500])
        session.add(job)
        session.commit()
    if job.state == state:
        raise RuntimeError(f"Claude terminou sem concluir a etapa: {str(result.get('result', ''))[:1500]}")


def _last_script_id(session: Session, project_id: int) -> int:
    from sqlalchemy import func

    from cvm.models import Script

    return session.exec(select(func.max(Script.id)).where(Script.project_id == project_id)).one() or 0


def _scripted_every_language(session: Session, project_id: int, since: int) -> bool:
    """Every language of the project got a script saved during this run."""
    from cvm.models import Project, Script

    project = session.get(Project, project_id)
    saved = {s.language for s in session.exec(select(Script).where(Script.project_id == project_id, Script.id > since))}
    return bool(project and project.languages) and set(project.languages) <= saved


@handler("scripting")
def _scripting(job: Job, session: Session) -> str | None:
    if engines.auto_run():
        _claude_step(job, session)
    return None  # manual mode: waits for "Rodar no Claude" in the UI


@handler("voicing")
def _voicing(job: Job, session: Session) -> str:
    from cvm.models import Project
    from cvm.voice.narrate import narrate

    from cvm import curation
    from cvm.voice import narrate as narrate_mod

    project = session.get(Project, job.project_id)
    token = narrate_mod.REDO.set(curation.voice_redo(job))  # sentences the validation step sent back: new seeds
    try:
        for lang in progress.parts(project.languages):
            narrate(session, project, lang)
    finally:
        narrate_mod.REDO.reset(token)
    return "sourcing_visuals"


EDITORIAL_NOTE = "Formato editorial:"  # history notes of the editorial step's send-backs (counted for the cap)
EDITORIAL_MAX_RETURNS = 2


AUTO_NOTES = {"aprovado automaticamente"}  # gates passed by the autopilot (cvm/autopilot.py): not a person


def _editorial_reset(h: dict) -> bool:
    """A history entry after which the automatic send-backs count from zero again: a failure, or a person moving
    the job (retry of a failed job, approval or send-back at a human gate, any other manual move)."""
    if h.get("to") == "failed" or h.get("from") == "failed" or h.get("note") == "retry":
        return True
    return h.get("from") in pipeline.HUMAN_GATES and h.get("note") not in AUTO_NOTES


def _editorial_returns(history: list[dict]) -> int:
    """Automatic editorial send-backs since the last failure or human move (so a retry gets fresh attempts)."""
    last = max((i for i, h in enumerate(history) if _editorial_reset(h)), default=-1)
    return sum(1 for h in history[last + 1:] if str(h.get("note") or "").startswith(EDITORIAL_NOTE))


def _editorial_send_back(job: Job, session: Session, e) -> None:
    """An EditorialError (cvm/render/editorial/timeline.py) sends the job back to the step that fixes it
    (`e.fix_state`: scripting → revision, voicing, sourcing_visuals); after EDITORIAL_MAX_RETURNS automatic
    send-backs (any step) it fails so a person looks."""
    note = f"{EDITORIAL_NOTE} {e}"
    past = _editorial_returns(job.history or [])
    log.warning("job %s: editorial %s → %s (%d before): %s", job.id, job.state, e.fix_state, past, e)
    if past >= EDITORIAL_MAX_RETURNS:  # it came back twice already: a person has to look
        pipeline.advance(job, "failed", f"{EDITORIAL_NOTE} o vídeo voltou {past} vezes para correção automática e o "
                                        f"problema continua. Confira o roteiro e as imagens. Último erro: {e}")
    elif e.fix_state in ("voicing", "sourcing_visuals"):
        pipeline.advance(job, e.fix_state, note)
    else:
        pipeline.send_to_revision(job, note)
    session.add(job)
    session.commit()
    return None


@handler("sourcing_visuals")
def _visuals(job: Job, session: Session) -> str | None:
    from cvm.models import Project

    from cvm import curation
    from cvm.visuals import select as select_mod

    from cvm import editorial
    from cvm.render.editorial.timeline import EditorialError
    from cvm.visuals import editorial as editorial_visuals

    project = session.get(Project, job.project_id)
    token = select_mod.REDO_SHIFT.set(curation.visual_shift(job))  # sent back by validation: the judge redraws anew
    try:
        try:  # long news-desk videos: their own real footage, proofs and backgrounds (plan.json per language)
            from cvm.scripting import agents, editorial_backfill
            from cvm.voice.narrate import latest_script

            for lang in editorial.long_languages(session, project):
                sc = latest_script(session, project.id, lang)
                if sc is not None and not (sc.content or {}).get("editorial"):  # written before the format existed
                    try:
                        editorial_backfill.backfill(session, project, lang, claude_slot=_claude_slot_for_check)
                    except agents.ClaudeBusy:  # every Claude slot busy for minutes: the visuals step tries again later
                        progress.wait(job, "claude")
                        session.add(job)
                        session.commit()
                        run_job.schedule((job.id,), delay=RETRY_S)
                        return None
                editorial_visuals.source(session, project, lang)
        except EditorialError as e:  # the scenes don't fit the narration: back to the step that fixes it
            return _editorial_send_back(job, session, e)
        if editorial_visuals.needs_classic(session, project):  # Shorts, ar-AE, other formats: as before
            select_mod.source_visuals(session, project)
    finally:
        select_mod.REDO_SHIFT.reset(token)
    return "lipsyncing"


@handler("lipsyncing")
def _lipsync(job: Job, session: Session) -> str:
    from cvm.models import Project
    from cvm.render.compose import prepare_avatar

    from cvm.avatar import talking

    # the per-video refine switch must cover scene avatars too, not only the whole-narration presenter
    token = talking.NO_REFINE.set(talking.refine_off(session, job.project_id))
    try:
        prepare_avatar(session, session.get(Project, job.project_id))
    finally:
        talking.NO_REFINE.reset(token)
    return "composing"


@handler("composing")
def _composing(job: Job, session: Session) -> str:
    from cvm.models import Project
    from cvm.render import compose as compose_mod
    from cvm.render.compose import compose_cuts, platforms_for
    from cvm.render.editorial.timeline import EditorialError

    from cvm.avatar import talking

    project = session.get(Project, job.project_id)
    targets = [(lang, profile) for lang in project.languages for profile in platforms_for(session, project)]
    from cvm.shorts import split_targets

    targets = split_targets(session, project, targets)  # a long script is never rendered as a Short (cvm/shorts.py)
    token = talking.NO_REFINE.set(talking.refine_off(session, job.project_id))
    try:
        for i, (lang, profile) in enumerate(progress.parts(targets)):
            progress.report(0, 1, f"{lang} · {profile} ({i + 1}/{len(targets)})")
            if profile.endswith("_cuts"):  # e.g. "youtube_shorts_cuts" -> several ≤55 s shorts
                compose_cuts(session, project, lang, profile.removesuffix("_cuts"))
            else:
                compose_mod.compose(session, project, lang, profile)
    except EditorialError as e:  # the news-desk video can't be assembled: back to the step that fixes it
        return _editorial_send_back(job, session, e)
    finally:
        talking.NO_REFINE.reset(token)
    return "rendering"


@handler("rendering")
def _rendering(job: Job, session: Session) -> str:
    """Technical QA of every render against its platform profile."""
    from cvm.models import Render
    from pathlib import Path

    from cvm.render.qa import analyse, blocking_from, check_render, contact_sheet, warnings_from

    problems = []
    renders = session.exec(select(Render).where(Render.project_id == job.project_id)).all()
    for i, r in enumerate(renders):
        progress.report(i, len(renders), f"conferindo {r.language} · {r.profile}")
        issues = check_render(r)
        extra = {}
        if not issues:
            a = analyse(Path(r.path))
            issues = blocking_from(a)
            sheet = contact_sheet(Path(r.path), Path(r.path).with_suffix(".sheet.jpg"))
            extra = {"analysis": a, "qa_warnings": warnings_from(a), "contact_sheet": str(sheet)}
        r.meta = {**r.meta, "qa": issues, **extra}
        session.add(r)
        problems += [f"{r.language}/{r.profile}: {i}" for i in issues]
    session.commit()
    if problems:
        raise RuntimeError("; ".join(problems))
    return "validating"


@handler("validating")
def _validating(job: Job, session: Session) -> str | None:
    """Automatic validation of the finished video (cvm/curation.py): on to SEO with a report, or back by itself to
    the step that fixes what it found. Reads what the checkers stored (no heavy model here, so no GPU slot); the
    light Claude content check takes a Claude slot when one frees up soon, else it is skipped."""
    from cvm import curation

    return curation.validate(job, session, claude_slot=_claude_slot_for_check)


@contextmanager
def _claude_slot_for_check(timeout: float = 300):
    got = _claude_slots.acquire(timeout=timeout)
    try:
        yield got
    finally:
        if got:
            _claude_slots.release()


@handler("seo")
def _seo(job: Job, session: Session) -> str | None:
    if engines.auto_run():
        _claude_step(job, session)
    return None


@huey.task()
def run_job(job_id: int) -> None:
    """Advance a job through automatic states until it reaches a human gate or a missing handler.
    The GPU slot is kept across consecutive GPU steps, so one video is produced start to finish."""
    with _active_lock:
        if job_id in _active:  # e.g. approved while the Claude CLI is still exiting: drive again after it
            _rerun.add(job_id)
            return
        _active.add(job_id)
    held: set[str] = set()
    try:
        while True:
            _drive(job_id, held)
            with _active_lock:
                if job_id not in _rerun:
                    break
                _rerun.discard(job_id)
    finally:
        for kind in held:
            _release(kind)
        with _active_lock:
            _active.discard(job_id)
            _rerun.discard(job_id)


def export_ready(session: Session, job) -> None:
    """A video at publish review also goes to its readable folder (cvm/export_folder.py); never blocks the queue."""
    if job.state != "awaiting_publish_review":
        return
    from cvm import export_folder
    from cvm.models import Project

    try:
        project = session.get(Project, job.project_id)
        if project:
            export_folder.export_project(session, project)
    except Exception as e:  # noqa: BLE001
        log.warning("export folder of %s failed: %s", job.project_id, e)


def _drive(job_id: int, held: set[str]) -> None:
    while True:
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job and job.state in pipeline.HUMAN_GATES:  # before the pause check: an auto-approval passes the
                from cvm import autopilot                  # gate even when paused (like settings.release_waiting);
                                                           # the paused job then stops at the next step
                export_ready(session, job)
                if autopilot.auto_approve(session, job):
                    continue
                return
            if job and job.paused:  # leave the line (and any slot, released by run_job) until resumed
                job.progress = {}
                session.add(job)
                session.commit()
                return
            if not job or job.state not in HANDLERS:
                return
            if _hold_for_update(job, session):
                return
            state = job.state
            kind = slot_kind(state)
            for other in held - {kind}:  # a slot is kept only across consecutive steps of the same kind
                _release(other)
                held.discard(other)
            if kind == "claude" and (until := _writers_busy(session, job)):
                _wait_claude_limit(job, session, until)  # resumed by claude_limit_tick
                return
            if kind == "claude" and _hold_for_previous_episode(job, session):
                return
            if kind and kind not in held:
                progress.wait(job, kind)
                session.add(job)
                session.commit()
                if not (_first_in_line(session, job, kind) and _acquire(kind)):
                    if kind == "gpu" and not gpu.held() and (free := gpu.memory_short()) is not None:
                        job.progress = {**job.progress, "label": f"esperando memória livre ({free:.0f} GB livres, "
                                                                 f"precisa {gpu._min_free():.0f})"}
                        session.add(job)
                        session.commit()
                    run_job.schedule((job_id,), delay=RETRY_S)
                    return
                held.add(kind)
            try:
                with progress.tracking(job, session):
                    if state in WORK_STATES or kind == "claude":
                        progress.start(job)
                        session.commit()
                    nxt = HANDLERS[state](job, session)
            except claude_limit.ClaudeLimit as e:
                log.warning("job %s: %s", job_id, e)
                session.refresh(job)
                if job.state == state:
                    _wait_claude_limit(job, session, e.until)
                return
            except Exception as e:  # noqa: BLE001
                log.exception("job %s failed in %s", job_id, state)
                session.refresh(job)
                pipeline.advance(job, "failed", f"{state}: {e}")
                session.add(job)
                session.commit()
                return
            finally:
                if kind == "claude":
                    _release("claude")
                    held.discard("claude")
            session.refresh(job)
            if job.state != state:  # Claude handed the job back: maybe through an auto-approved gate
                session.commit()
                continue
            if nxt is None:
                session.commit()
                return
            pipeline.advance(job, nxt)
            session.add(job)
            session.commit()


def _writers_busy(session: Session, job: Job):
    """Until when no writer (Claude, or Codex where allowed — cvm/scripting/agents.py) can take this step."""
    from cvm.models import Project
    from cvm.scripting import agents

    return agents.wait_until(session, LIMIT_STEP.get(job.state, job.state), session.get(Project, job.project_id))


def _wait_claude_limit(job: Job, session: Session, until) -> None:
    who = "Claude e do Codex" if claude_limit.blocked_until(session, provider="codex") else "Claude"
    job.progress = {"waiting": "claude_limit", "until": until.isoformat(timespec="seconds"),
                    "label": claude_limit.label(until, who), "since": progress.now().isoformat()}
    session.add(job)
    session.commit()


@huey.periodic_task(crontab(minute="*"))
def claude_limit_tick() -> None:
    """Once a writer's limit has reset (Claude, or Codex for the steps it may take), put the videos that were waiting
    for it back in line."""
    with Session(engine) as session:
        for provider in ("claude", "codex"):
            if claude_limit.state(session, provider) and not claude_limit.blocked_until(session, provider=provider):
                claude_limit.clear(session, provider)
        waiting = [j for j in session.exec(select(Job).where(Job.state.in_(LIMIT_WAIT_STATES))).all()
                   if not j.paused and (j.progress or {}).get("waiting") == "claude_limit"]
        ready = [j.id for j in waiting if not _writers_busy(session, j)]
    if ready:
        log.info("writer free again: resuming %s", ready)
    for job_id in ready:
        run_job(job_id)


def release_claude_limit() -> list[int]:
    """Clear the limit and put the videos waiting for it back in line (the tick after the reset, or the user's
    "Liberar agora")."""
    with Session(engine) as session:
        claude_limit.clear(session)
        waiting = [j.id for j in session.exec(select(Job).where(Job.state.in_(LIMIT_WAIT_STATES))).all()
                   if not j.paused and (j.progress or {}).get("waiting") == "claude_limit"]
    log.info("Claude limit reset: resuming %s", waiting)
    for job_id in waiting:
        run_job(job_id)
    return waiting


PRE_SCRIPT = ("trend_selected", "scripting")
EPISODE_WAIT_MAX = timedelta(hours=24)  # longest wait for the previous episode's script (it may be stuck or paused)


def previous_episode_pending(session: Session, job: Job) -> int | None:
    """A kids episode is scripted only after the one before it (same series and language, on this machine) has its
    script, so it can pick up that episode's teaser. Returns the number being waited for. A failed or deleted
    previous episode does not block the series. (Episodes from other PCs came through the online cluster, removed
    2026-11-24: only local episodes count now.)"""
    from cvm.channels import episode_lang, episode_number, series_episodes, series_of
    from cvm.models import Project, Script

    project = session.get(Project, job.project_id)
    if job.state != "scripting" or not project or project.style != "kids" or not (mine := episode_number(project.title)):
        return None
    series, lang = series_of(session, project), episode_lang(session, project)
    if not series:
        return None
    local = [(n, e) for e in series_episodes(session, series.id)
             if e.id != project.id and episode_lang(session, e) == lang and 0 < (n := episode_number(e.title) or 0) < mine]
    if not local:
        return None
    n = max(k for k, _ in local)
    prev = [e for k, e in local if k == n]
    if any(session.exec(select(Script).where(Script.project_id == e.id)).first() for e in prev):
        return None
    if not any((j := session.exec(select(Job).where(Job.project_id == e.id)).first()) and j.state in PRE_SCRIPT
               for e in prev):
        return None
    p = job.progress or {}
    if p.get("waiting") == "episode" and p.get("since") and \
            datetime.now(timezone.utc) - datetime.fromisoformat(p["since"]) > EPISODE_WAIT_MAX:
        log.warning("job %s: Ep. %s has had no script for %s, going on without its teaser", job.id, n, EPISODE_WAIT_MAX)
        return None
    return n


def _hold_for_update(job: Job, session: Session) -> bool:
    """"Atualizar" (cvm/setup/update.py) is moving the code: no new step starts until it is done."""
    from cvm.setup import update

    if not update.hold_active(session):
        return False
    progress.wait(job, "update")
    job.progress = {**job.progress, "label": "esperando a atualização do CVM"}
    session.add(job)
    session.commit()
    run_job.schedule((job.id,), delay=HOLD_RETRY_S)
    return True


def _hold_for_previous_episode(job: Job, session: Session) -> bool:
    if not (n := previous_episode_pending(session, job)):
        return False
    progress.wait(job, "episode")  # its own line: it does not hold back other videos waiting for Claude
    job.progress = {**job.progress, "after_episode": n}
    session.add(job)
    session.commit()
    run_job.schedule((job.id,), delay=HOLD_RETRY_S)
    return True


@huey.task()
def run_claude_step(job_id: int) -> None:
    from cvm.scripting.claude_runner import build_prompt, run_claude

    with Session(engine) as session:
        job = session.get(Job, job_id)
        prompt = build_prompt(job) if job else None
        if not prompt:
            return
        state = job.state
        progress.start(job)
        session.add(job)
        session.commit()
    error = None
    try:
        from cvm.models import Project
        from cvm.scripting import agents

        with Session(engine) as session:
            result = agents.run(prompt, state, session, session.get(Project, job.project_id))
        error = f"Claude terminou sem concluir a etapa: {str(result.get('result', ''))[:1500]}"
    except claude_limit.ClaudeLimit as e:
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job.state == state:
                _wait_claude_limit(job, session, e.until)
        return
    except Exception as e:  # noqa: BLE001
        log.exception("claude step failed for job %s", job_id)
        error = f"claude: {e}"
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job.state == state:  # the agent did not hand the job back via the MCP tool
            pipeline.advance(job, "failed", error)
            session.add(job)
            session.commit()
            return
    run_job(job_id)


@huey.task()
def run_trend_analysis(analysis_id: int) -> None:
    from cvm.models import Channel, Project, TrendAnalysis
    from cvm.scripting.claude_runner import channel_ideas_prompt, run_claude, trend_analysis_prompt

    from cvm.setup import features

    with Session(engine) as session:
        a = session.get(TrendAnalysis, analysis_id)
        if not features.ai_ideas(session):  # "Sugestões da IA" off in the wizard: no AI run at all
            a.status, a.error = "failed", "Sugestões da IA desligadas (Configurações / assistente de instalação)"
            session.add(a)
            session.commit()
            return
        if a.channel_id:
            channel = session.get(Channel, a.channel_id)
            recent = [p.title for p in session.exec(select(Project).where(Project.channel_id == a.channel_id)
                                                    .order_by(Project.id.desc())).all()]
            prompt = channel_ideas_prompt(analysis_id, channel, recent)
        else:
            seen = session.exec(select(TrendAnalysis).where(TrendAnalysis.channel_id.is_(None), TrendAnalysis.id != a.id,
                                                            TrendAnalysis.status == "done")
                                .order_by(TrendAnalysis.id.desc()).limit(3)).all()
            shown = [i.get("topic") for x in seen for i in x.items if i.get("topic")]
            prompt = trend_analysis_prompt(analysis_id, a.region, shown)
    error = None
    try:
        from cvm.scripting import agents

        result = agents.run(prompt, "ideas", timeout=1200)
        error = f"Claude terminou sem salvar a análise: {str(result.get('result', ''))[:1500]}"
    except claude_limit.ClaudeLimit as e:
        log.warning("trend analysis %s: %s — runs again after the reset", analysis_id, e)
        run_trend_analysis.schedule((analysis_id,), eta=e.until)
        return
    except Exception as e:  # noqa: BLE001
        log.exception("trend analysis %s failed", analysis_id)
        error = f"claude: {e}"
    with Session(engine) as session:
        a = session.get(TrendAnalysis, analysis_id)
        if a.status == "running":  # the agent did not call save_trend_analysis
            a.status, a.error = "failed", error
            session.add(a)
            session.commit()
            return
        from cvm import autopilot

        autopilot.enqueue(session)  # no-op unless the autopilot is on


@huey.task()
def collect_trends(analysis_id: int | None = None) -> None:
    """Collect trends now; with an analysis id, have Claude refresh the ideas from the new trends afterwards."""
    from cvm.trends.collect import collect_all

    try:
        collect_all()
    finally:
        if analysis_id:
            run_trend_analysis.call_local(analysis_id)


@huey.task()
def engine_test_job(features: list[str]) -> None:
    """The install wizard's engine test (cvm/setup/engine_test.py): each tier runs in a child process that takes the
    heavy-model lock like the pipeline does, so it waits for (and never runs beside) a video's GPU step."""
    from cvm.setup import engine_test

    try:
        engine_test.run(features)
    except Exception:  # noqa: BLE001
        log.exception("engine test failed")


@huey.task()
def design_character_voice(channel_id: int, name: str, description: str) -> None:
    """Create or redo one series character's voice (VoxCPM2 voice design, under the GPU lock)."""
    from cvm.models import Channel
    from cvm.voice import characters

    with Session(engine) as session:
        channel = session.get(Channel, channel_id)
        try:
            characters.save_voice(session, channel, name, description, channel.language)
        except Exception as e:  # noqa: BLE001
            log.exception("voice design failed for %s", name)
            session.rollback()
            channel = session.get(Channel, channel_id)
            chars = dict(channel.characters or {})
            chars[name] = {**chars.get(name, {}), "description": description, "pending": False, "error": str(e)[:300]}
            channel.characters = chars
            session.add(channel)
            session.commit()


@huey.task()
def draw_character_sheet(channel_id: int, name: str, look: str) -> None:
    """Redraw one series character's reference sheet with the next seed (FLUX.2 Klein, under the GPU lock)."""
    from cvm.models import Channel
    from cvm.visuals import cast

    with Session(engine) as session:
        channel = session.get(Channel, channel_id)
        old = cast._entry(channel, name)[1]
        try:
            seed = int(old["sheet_seed"]) + cast.SHEET_TRIES if old.get("sheet_seed") is not None else None
            cast.save_sheet(session, channel, name, look, seed)
        except Exception as e:  # noqa: BLE001
            log.exception("character sheet failed for %s", name)
            session.rollback()
            channel = session.get(Channel, channel_id)
            chars = dict(channel.characters or {})
            chars[name] = {**{k: v for k, v in chars.get(name, {}).items() if k != "sheet_pending"}, "sheet_error": str(e)[:300]}
            channel.characters = chars
            session.add(channel)
            session.commit()


_avatar_lock = threading.Lock()


@huey.task()
def avatar_from_photo(avatar_id: int, photo: str, framing: str = "medium") -> None:
    """Animate an uploaded photo into the avatar's base video (see cvm/avatar/from_photo.py). One avatar at a time;
    others retry later instead of holding worker threads (the video queue keeps its threads)."""
    if not _avatar_lock.acquire(blocking=False):
        avatar_from_photo.schedule((avatar_id, photo, framing), delay=60)
        return
    try:
        _avatar_from_photo(avatar_id, photo, framing)
    finally:
        _avatar_lock.release()


def _avatar_from_photo(avatar_id: int, photo: str, framing: str) -> None:
    from pathlib import Path

    from cvm.avatar.capture import save_base_video
    from cvm.avatar.from_photo import make_base, make_base_free
    from cvm.paths import avatar_dir
    from cvm.models import Avatar
    from cvm.visuals.sources import ltx_ready

    try:
        build = make_base_free if framing == "close" and ltx_ready() else make_base  # real close-up photos: free takes
        base = build(Path(photo), avatar_dir(avatar_id) / "photo", framing)
        _, problems = save_base_video(avatar_id, base)
        problems = [p for p in problems if "curto" not in p]  # a generated 10 s loop is enough
        status, notes = ("needs_review", "; ".join(problems)) if problems else ("ready", "criado a partir de foto (animado por IA)")
    except Exception as e:  # noqa: BLE001
        log.exception("avatar %s from photo failed", avatar_id)
        status, notes = "needs_review", f"não consegui animar a foto: {e}"
    with Session(engine) as session:
        a = session.get(Avatar, avatar_id)
        if a:
            a.base_video_dir = str(avatar_dir(avatar_id) / "base") if status == "ready" else a.base_video_dir
            a.status, a.notes = status, notes
            session.add(a)
            session.commit()


@huey.periodic_task(crontab(minute="*/30"))
def collect_trends_periodic() -> None:
    from cvm.trends.collect import collect_all

    collect_all()


GROW_SLOTS = 2  # long downloads/analyses of pasted videos at once: the rest wait without holding a worker thread
_grow_slots = threading.BoundedSemaphore(GROW_SLOTS)
HEAVY_GROW = {"_job_link", "_job_upload", "_job_make"}


@huey.task()
def grow_job(name: str, args: list) -> None:
    """Background work of the menu Crescer (cvm/api/grow.py `_job_*`): fn(session, channel, *rest)."""
    from cvm.api import grow
    from cvm.models import Channel

    heavy = name in HEAVY_GROW
    if heavy and not _grow_slots.acquire(blocking=False):
        grow_job.schedule((name, args), delay=60)
        return
    try:
        fn = getattr(grow, name)
        with Session(engine) as s:
            c = s.get(Channel, args[0])
            if c:
                fn(s, c, *args[1:])
    finally:
        if heavy:
            _grow_slots.release()


@huey.task()
def link_projects(key: str) -> None:
    """/fabrica/novo links (cvm/media_links.py): download and attach them, then start the project's job. Shares the
    Crescer download slots."""
    from cvm import media_links

    if not _grow_slots.acquire(blocking=False):
        link_projects.schedule((key,), delay=60)
        return
    try:
        with Session(engine) as s:
            media_links.run(s, key)
    finally:
        _grow_slots.release()


@huey.task()
def trends_job(name: str, args: list) -> None:
    """Background work of Em alta without a channel (cvm/api/em_alta.py `_job_*`): fn(session, *args)."""
    from cvm.api import em_alta

    with Session(engine) as s:
        getattr(em_alta, name)(s, *args)


@huey.periodic_task(crontab(minute="20,50"))
def follow_tick() -> None:
    """Every 30 min: followed keywords/channels (Setting "follow") come into the trend list as source="follow"."""
    from cvm.trends import follow

    with Session(engine) as s:
        follow.tick(s)


@huey.periodic_task(crontab(minute="40"))
def lives_retry_tick() -> None:
    """Hourly: pasted videos that wait (live still on air, YouTube's anti-bot pause) are sent again, 2 at a time."""
    from cvm.lives.runs import retry_waiting
    from cvm.models import Channel

    with Session(engine) as s:
        for c in s.exec(select(Channel)).all():
            if c.status == "active":
                retry_waiting(s, c)


@huey.periodic_task(crontab(minute="2"))
def viral_daily_tick() -> None:
    """Hourly gate: from 07:00 studio time (settings.default_tz), once a day, viral research for channels with topics (cvm/grow/daily.py)."""
    from cvm.grow.daily import run_all

    run_all()


@huey.periodic_task(crontab(minute="15"))
def autopilot_tick() -> None:
    """Hourly: refresh Claude's ideas when stale, and queue the best ones if the autopilot is on."""
    from cvm import autopilot
    from cvm.models import TrendAnalysis

    with Session(engine) as session:
        if not autopilot.get(session)["enabled"]:
            return
        autopilot.enqueue(session)
        from cvm.scripting import agents

        from cvm.setup import features

        if (features.ai_ideas(session) and autopilot.needs_analysis(session)
                and not agents.wait_until(session, "ideas")):  # none while limited or with AI ideas off
            a = TrendAnalysis(region=None)
            session.add(a)
            session.commit()
            run_trend_analysis(a.id)  # queues more once it is saved (see run_trend_analysis)


@huey.periodic_task(crontab(minute="*"))
def release_due_publications() -> None:
    """At the scheduled time: api mode uploads; manual mode shows the publish kit and notifies."""
    from cvm.publish.service import release_due

    with Session(engine) as session:
        release_due(session, _notify)


@huey.periodic_task(crontab(minute="7"))
def refresh_metrics() -> None:
    from cvm.publish.metrics import refresh_youtube

    with Session(engine) as session:
        refresh_youtube(session)


@huey.periodic_task(crontab(minute="5"))
def weekly_cycle_tick() -> None:
    """Hourly gate for the weekly data cycle: from Monday 08:00 studio time (settings.default_tz) until it
    has run this week (cvm/publish/weekly.py). Channels without YouTube Analytics connected are skipped."""
    from cvm.publish import weekly

    with Session(engine) as session:
        if not weekly.due(session):
            return
        weekly.mark_done(session)  # first, so a long Claude run is not started twice by the next tick
    weekly_cycle()


@huey.task()
def weekly_cycle() -> None:
    from cvm.publish import weekly

    with Session(engine) as session:
        done = weekly.run_all(session)
        weekly.mark_done(session, channels=done)


def _notify(title: str, message: str) -> None:
    import json
    import subprocess
    import sys

    if sys.platform != "darwin":  # desktop notifications are a macOS nicety; the UI shows the same states
        return
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=False)
