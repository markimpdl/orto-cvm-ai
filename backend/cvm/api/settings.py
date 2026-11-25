from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from cvm import autopilot
from cvm.db import get_session
from cvm.models import Job

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/autopilot")
def get_autopilot(session: Session = Depends(get_session)):
    return autopilot.get(session)


@router.put("/autopilot")
def put_autopilot(data: dict, session: Session = Depends(get_session)):
    """{enabled?, min_score?, max_per_day?, auto_approve_script?, auto_approve_render?}. Turning the autopilot on
    queues the current best ideas right away; turning an auto-approval on releases the jobs already waiting."""
    before = autopilot.get(session)
    cfg = autopilot.put(session, data)
    created = autopilot.enqueue(session) if cfg["enabled"] and not before["enabled"] else []
    released = release_waiting(session, cfg)
    return {**cfg, "created": created, "released": released}


def release_waiting(session: Session, cfg: dict) -> list[int]:
    """Pass every job already waiting at a gate whose auto-approval is on, then let the worker carry on."""
    from cvm.workers.tasks import run_job

    states = [s for k, s in (("auto_approve_script", "awaiting_approval"), ("auto_approve_render", "awaiting_publish_review")) if cfg[k]]
    released = []
    for job in session.exec(select(Job).where(Job.state.in_(states))).all() if states else []:
        try:
            moved = autopilot.auto_approve(session, job)
        except Exception:  # noqa: BLE001  (e.g. no channel to schedule on: leave it for the user)
            session.rollback()
            continue
        if moved:
            released.append(job.id)
            run_job(job.id)
    return released


@router.post("/autopilot/release")
def release(session: Session = Depends(get_session)):
    return {"released": release_waiting(session, autopilot.get(session))}


# ---------------- engines (Configurações > Integrações / Modelos) ----------------

def _engines_view() -> dict:
    from cvm import engines
    from cvm.scripting import codex_runner

    try:
        codex = codex_runner.installed()
    except Exception:  # noqa: BLE001
        codex = False
    return {**engines.get(), "options": engines.options(), "codex_installed": codex}


@router.get("/engines")
def get_engines():
    """Who writes (claude | codex | manual), Codex as the reserve, model per purpose and which models are ready."""
    return _engines_view()


@router.get("/models")
def get_models():
    """Configurações > Modelos: every job the local models do, the Mac and Windows (NVIDIA) model for each, and every
    model's state here (installed | partial | missing | incompatible) with its size on disk. Never downloads."""
    from cvm import model_catalog

    return model_catalog.view()


@router.put("/engines")
def put_engines(data: dict, session: Session = Depends(get_session)):
    """{writer?, codex_fallback?, video?: {kids|realistic|cartoon: ltx|wan}, image_engine?: z-image|flux}.
    A model that is not fully downloaded here → 409 (nothing is ever downloaded from here). Switching from "manual"
    back to a writer starts the steps that were waiting for the button."""
    from cvm import engines
    from cvm.workers.tasks import CLAUDE_STATES, run_job

    was_auto = engines.auto_run()
    try:
        engines.put(data)
    except engines.NotReady as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    started = []
    if engines.auto_run() and not was_auto:
        for job in session.exec(select(Job).where(Job.state.in_(CLAUDE_STATES))).all():
            if not job.paused:
                run_job(job.id)
                started.append(job.id)
    return {**_engines_view(), "started": started}


# ---------------- ElevenLabs key (Keychain; never in the DB, a log or a response) ----------------

def _eleven_state(check: bool = False) -> dict:
    from cvm import keystore

    out: dict = {"configured": bool(keystore.elevenlabs_key()), "source": keystore.elevenlabs_source()}
    if check and out["configured"]:
        from cvm.voice import eleven

        try:
            out["remaining"] = eleven.remaining()
        except Exception as e:  # noqa: BLE001
            out["remaining"] = None
            msg = str(e)
            out["error"] = "chave recusada pela ElevenLabs" if "HTTP 401" in msg or "HTTP 403" in msg \
                else "não consegui falar com a ElevenLabs"
    return out


@router.get("/secrets/elevenlabs")
def get_eleven():
    """{configured, source: keychain | env | null}. The key itself is never returned."""
    return _eleven_state()


@router.put("/secrets/elevenlabs")
def put_eleven(data: dict):
    """{key}: saved in the macOS Keychain. Returns only {configured, source}."""
    from cvm import keystore

    try:
        keystore.put(keystore.ELEVENLABS, str(data.get("key") or ""))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except keystore.SecretError as e:
        raise HTTPException(500, str(e)) from e
    return _eleven_state()


@router.delete("/secrets/elevenlabs")
def delete_eleven():
    """Removes the Keychain copy (a key in .env, if any, still works)."""
    from cvm import keystore

    keystore.delete(keystore.ELEVENLABS)
    return _eleven_state()


@router.post("/secrets/elevenlabs/test")
def test_eleven():
    """{configured, remaining}: credits left this month, asked to ElevenLabs with the saved key."""
    s = _eleven_state(check=True)
    return {k: s[k] for k in ("configured", "remaining", "error", "source") if k in s}


# ---------------- rules per video type (Configurações > Regras por tipo) ----------------

@router.get("/style-rules")
def get_style_rules(session: Session = Depends(get_session)):
    from cvm import style_rules

    return {"fields": list(style_rules.FIELDS), "rules": style_rules.get_all(session)}


@router.put("/style-rules")
def put_style_rules(data: dict, session: Session = Depends(get_session)):
    """{style: {thumbnail?, title?, hook?, ending?, bumper?}}; an empty text clears that rule."""
    from cvm import style_rules

    try:
        rules = style_rules.put(session, data.get("rules", data))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"fields": list(style_rules.FIELDS), "rules": rules}


# ---------------- automatic validation (Configurações > Automação) ----------------

@router.get("/curation")
def get_curation(session: Session = Depends(get_session)):
    """{enabled, max_attempts}: the "validating" step after the render (cvm/curation.py)."""
    from cvm import curation

    return curation.get(session)


@router.put("/curation")
def put_curation(data: dict, session: Session = Depends(get_session)):
    from cvm import curation

    try:
        return curation.put(session, data)
    except (TypeError, ValueError) as e:
        raise HTTPException(422, str(e)) from e
