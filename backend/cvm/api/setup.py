from dataclasses import asdict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session

from cvm.db import get_session
from cvm.setup import install, state
from cvm.setup.requirements import DISK_RESERVE_GB, FEATURES, check, detect

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("")
def get_setup(session: Session = Depends(get_session)):
    st = state.get(session)
    hw = detect()
    chosen = [f for f in st.get("choices", {}).get("features", []) if f in FEATURES]
    need = _missing(chosen)
    return {
        "state": st,
        "hardware": asdict(hw),
        "blockers": [asdict(b) for b in check(chosen, hw, need)],
        "features": {k: f.label for k, f in FEATURES.items()},
        # the number the disk blocker used (None: the static estimate, nothing known about this machine)
        "disk": {"need_gb": need, "free_gb": round(hw.disk_free_gb, 1), "reserve_gb": DISK_RESERVE_GB},
    }


def _missing(features: list[str]) -> float | None:
    """GB still to download for the features on this machine; None when the plan cannot be made."""
    if not features:
        return 0.0
    try:
        return install.missing_gb(install.plan(features, install.PLATFORM))
    except Exception:  # noqa: BLE001 — a broken probe must not hide the wizard; check() falls back to the estimate
        return None


@router.put("")
def put_setup(data: dict, session: Session = Depends(get_session)):
    """{step, choices}: choices are merged into what is already saved. 422 on an unknown step or non-object choices."""
    step = data.get("step") or "system"
    if not isinstance(step, str) or step not in state.STEPS:
        raise HTTPException(422, f"step: {step!r}")
    choices = data.get("choices")
    if choices is None:
        choices = {}
    if not isinstance(choices, dict):
        raise HTTPException(422, "choices: precisa ser um objeto")
    return state.save(session, step, choices)


@router.post("/finish")
def finish_setup(session: Session = Depends(get_session)):
    return state.finish(session)


def _features(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.split(",")
    return [f.strip() for f in raw or [] if f and f.strip()]


@router.get("/models")
def plan_models(features: str = "base"):
    """What the features need (comma-separated) and what is already on this machine."""
    try:
        items = install.plan(_features(features), install.PLATFORM)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"items": [asdict(i) for i in items], "missing_gb": install.missing_gb(items)}


@router.post("/models")
def start_models(data: dict):
    """{features}: installs what is missing in the background; follow it on GET /setup/models/status."""
    try:
        install.start(_features(data.get("features") or ["base"]))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return install.status()


@router.get("/models/status")
def models_status():
    return install.status()


@router.post("/avatar/lucas")
def install_lucas(session: Session = Depends(get_session)):
    """Installs the ready-made avatar Lucas (idempotent)."""
    from cvm.setup import lucas

    try:
        av = lucas.install(session)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except lucas.LucasError as e:
        raise HTTPException(500, str(e)) from e
    return {"id": av.id, "name": av.name, "status": av.status}


@router.get("/ai")
def get_ai():
    from cvm.setup import ai

    return ai.status()


@router.post("/ai/test")
def test_ai(data: dict):
    """{which: claude | codex}: a fresh check (no prompt is sent)."""
    from cvm.setup import ai

    which = data.get("which")
    if which not in ("claude", "codex"):
        raise HTTPException(422, f"which: {which!r}")
    return ai.status(fresh=True)[which]


@router.put("/ai")
def put_ai(data: dict):
    """{choice: claude | codex | both}."""
    from cvm.setup import ai

    try:
        ai.apply(str(data.get("choice")))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    ai.invalidate()
    return ai.status()


@router.get("/features")
def get_features(keys: bool = False, session: Session = Depends(get_session)):
    """The features Setting. `?keys=1` (the wizard's Features step only) adds which API keys are set: reading the
    keystore spawns macOS `security` processes, so the menus' page loads never ask for it."""
    from cvm.setup import features

    out = features.get(session)
    return {**out, "keys": features.keys_set()} if keys else out


@router.put("/features")
def put_features(data: dict, session: Session = Depends(get_session)):
    from cvm.setup import features

    try:
        return features.put(session, data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.put("/keys")
def put_keys(data: dict):
    """{pexels?, pixabay?, youtube?, twitch_id?, twitch_secret?, elevenlabs?}: into the keystore. Returns only which are set."""
    from cvm import keystore
    from cvm.setup import features

    try:
        features.put_keys(data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except keystore.SecretError as e:
        raise HTTPException(500, str(e)) from e
    return features.keys_set()


# ---------- first channel, brand, bumper preview ----------

def _channel_or_404(session: Session, channel_id: int):
    from cvm.models import Channel

    c = session.get(Channel, channel_id)
    if not c:
        raise HTTPException(404, "Canal não encontrado.")
    return c


@router.post("/channel")
def create_channel(data: dict, session: Session = Depends(get_session)):
    """{name, language: pt-BR | en-US, brief, avatar_id?, platforms?, made_for_kids?, bumper?, publish?}."""
    from cvm.setup import channel

    try:
        return channel.create(session, data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/channel/{channel_id}/brand")
async def upload_brand(channel_id: int, logo: UploadFile | None = File(None), banner: UploadFile | None = File(None),
                       session: Session = Depends(get_session)):
    """Multipart logo and/or banner → data/channels/<id>/brand/logo.png | banner.png."""
    from cvm.setup import channel

    _channel_or_404(session, channel_id)
    if logo is None and banner is None:
        raise HTTPException(422, "Envie o logo, o banner ou os dois.")

    async def read(f: UploadFile | None) -> bytes | None:  # one byte over the cap is enough to refuse it
        return None if f is None else await f.read(channel.MAX_UPLOAD + 1)

    try:
        return channel.save_brand(channel_id, logo=await read(logo), banner=await read(banner))
    except channel.BrandError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/channel/{channel_id}/bumper")
def start_bumper(channel_id: int, data: dict | None = None, session: Session = Depends(get_session)):
    """{prompt?}: renders a preview in the background; follow it on GET /setup/channel/{id}/bumper."""
    from cvm.setup import channel

    c = _channel_or_404(session, channel_id)
    try:
        return channel.start_bumper(session, c, (data or {}).get("prompt"))
    except channel.BrandError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/channel/{channel_id}/bumper")
def bumper_status(channel_id: int, session: Session = Depends(get_session)):
    from cvm.setup import channel

    _channel_or_404(session, channel_id)
    return channel.status(session, channel_id)


@router.get("/channel/{channel_id}/bumper.mp4")
def bumper_file(channel_id: int):
    from cvm.setup import channel

    path = channel.preview_path(channel_id)
    if not path.exists():
        raise HTTPException(404, "A vinheta ainda não está pronta.")
    return FileResponse(path, media_type="video/mp4")


@router.post("/channel/{channel_id}/bumper/approve")
def approve_bumper(channel_id: int, session: Session = Depends(get_session)):
    from cvm.setup import channel

    c = _channel_or_404(session, channel_id)
    try:
        return {"params": channel.approve_bumper(session, c)}
    except LookupError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/update")
def get_update():
    """'Atualizar': only installs cloned from the public repo see it ({"public_install": false} elsewhere)."""
    from cvm.setup import update

    if not update.public_install():
        return {"public_install": False}
    out: dict = {"public_install": True, "supported": update.sys.platform == "darwin", "run": update.state()}
    try:
        out.update(update.status())
        out["error"] = None
    except Exception as e:  # noqa: BLE001 — offline: still show the installed version and the last run
        out.update({"current": update.current(), "latest": None, "available": False,
                    "error": f"Não deu para ver se há versão nova (sem internet?): {e}"})
    out["running"] = update.running()
    return out


@router.post("/update")
def start_update():
    """Starts the update in the background; follow it on GET /setup/update (run.state / run.label)."""
    from cvm.setup import update

    if not update.public_install():
        raise HTTPException(404)
    try:
        update.start()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"started": True}
