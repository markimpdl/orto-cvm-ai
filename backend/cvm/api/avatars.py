import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from cvm.api.crud import crud_router
from cvm.db import get_session
from cvm.models import Avatar, VoiceProfile
from cvm.paths import NO_CACHE

router = crud_router(Avatar, "/avatars")

LANG_FILE = {"pt-BR": "pt", "ar-AE": "ar", "en-US": "en"}


@router.get("/meta/voices")
def voice_choices(session: Session = Depends(get_session)):
    """Named voice overrides for avatars with multiple choices; list order is display-only, never the default."""
    from cvm.voice.narrate import voice_variants

    by: dict[int, list] = {}
    for p in session.exec(select(VoiceProfile)).all():
        by.setdefault(p.avatar_id, []).append(p)
    return {aid: _with_mix(v) for aid, ps in by.items() if len(v := voice_variants(ps)) > 1}


def _with_mix(voices: list[dict]) -> list[dict]:
    """"mix" (trained voice + ElevenLabs on acronyms, cvm/voice/narrate.py) is a choice of its own, not a profile:
    offered when the avatar has both, in the languages it works in (pt-BR, en-US)."""
    by = {v["key"]: v for v in voices}
    if "real" not in by or "eleven" not in by:
        return voices
    langs = [lang for lang in by["real"]["languages"] if lang in by["eleven"]["languages"] and lang in ("pt-BR", "en-US")]
    return [*voices, {"key": "mix", "label": "Treinada + ElevenLabs nas siglas", "languages": langs}] if langs else voices


def _tmp_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "upload").suffix or ".bin"
    tmp = Path(tempfile.mkstemp(suffix=suffix)[1])
    with tmp.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return tmp


@router.get("/{avatar_id}/detail")
def detail(avatar_id: int, session: Session = Depends(get_session)):
    avatar = session.get(Avatar, avatar_id)
    if not avatar:
        raise HTTPException(404)
    voices = session.exec(select(VoiceProfile).where(VoiceProfile.avatar_id == avatar_id)).all()
    return {**avatar.model_dump(), "voices": voices}


@router.get("/meta/reading-text/{kind}")
def reading_text(kind: str):
    """Guided script for a voice language (pt-BR, ar-AE, en-US) or for the base video ("video")."""
    from cvm.avatar import reading

    if kind not in reading.FILES:
        raise HTTPException(404)
    return {"text": reading.spoken_text(kind), "sections": reading.sections(kind)}


@router.post("/{avatar_id}/base-video")
def upload_base_video(avatar_id: int, file: UploadFile = File(...), session: Session = Depends(get_session)):
    from cvm.avatar.capture import save_base_video

    avatar = session.get(Avatar, avatar_id)
    if not avatar:
        raise HTTPException(404)
    tmp = _tmp_upload(file)
    try:
        out, problems = save_base_video(avatar_id, tmp)
    finally:
        tmp.unlink(missing_ok=True)
    avatar.base_video_dir = str(out.parent)
    avatar.status = "ready" if not problems else "needs_review"
    avatar.notes = "; ".join(problems) or None
    session.add(avatar)
    session.commit()
    return {"ok": True, "problems": problems}


@router.post("/{avatar_id}/photo")
def upload_photo(avatar_id: int, file: UploadFile = File(...), framing: str = Form("medium"),
                 session: Session = Depends(get_session)):
    """Create the avatar's base video from one photo (animated in the worker: ~2-3 min with LTX, ~15 with Wan)."""
    from cvm.paths import avatar_dir
    from cvm.workers.tasks import avatar_from_photo

    avatar = session.get(Avatar, avatar_id)
    if not avatar:
        raise HTTPException(404)
    suffix = Path(file.filename or "foto.jpg").suffix.lower() or ".jpg"
    if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
        raise HTTPException(415, "envie uma foto (jpg, png, webp, heic)")
    d = avatar_dir(avatar_id) / "photo"
    d.mkdir(parents=True, exist_ok=True)
    photo = d / f"original{suffix}"
    with photo.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    if suffix == ".heic":
        import subprocess

        jpg = photo.with_suffix(".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", str(photo), "--out", str(jpg)], capture_output=True)
        photo = jpg
    avatar.status, avatar.notes = "processing", "Animando a foto (olhar na câmera, piscando)…"
    session.add(avatar)
    session.commit()
    avatar_from_photo(avatar_id, str(photo), framing if framing in ("medium", "close") else "medium")
    return {"queued": True}


@router.get("/{avatar_id}/still")
def still(avatar_id: int, session: Session = Depends(get_session)):
    avatar = session.get(Avatar, avatar_id)
    if not avatar or not avatar.base_video_dir:
        raise HTTPException(404)
    return FileResponse(Path(avatar.base_video_dir) / "still.jpg", headers=NO_CACHE)


@router.post("/{avatar_id}/voice/{language}")
def upload_voice(
    avatar_id: int,
    language: str,
    file: UploadFile = File(...),
    ref_text: str | None = Form(None),
    session: Session = Depends(get_session),
):
    from cvm.avatar.capture import save_voice_sample

    if language not in LANG_FILE:
        raise HTTPException(400, "language must be pt-BR, ar-AE or en-US")
    from cvm.channels import check_language

    try:
        check_language(language)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    tmp = _tmp_upload(file)
    try:
        ref, heard = save_voice_sample(avatar_id, language, tmp)
    finally:
        tmp.unlink(missing_ok=True)
    profile = session.exec(
        select(VoiceProfile).where(VoiceProfile.avatar_id == avatar_id, VoiceProfile.language == language)
    ).first() or VoiceProfile(avatar_id=avatar_id, language=language)
    profile.ref_audio_path = str(ref)
    profile.ref_text = ref_text or heard
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


@router.post("/{avatar_id}/voice/{language}/preview")
def preview_voice(avatar_id: int, language: str, body: dict, session: Session = Depends(get_session)):
    """Synthesize a short sentence with the avatar's voice (cloned if a profile exists)."""
    from cvm.paths import avatar_dir
    from cvm.voice.engine import synthesize
    from cvm.voice.narrate import voice_profile

    profile = voice_profile(session, avatar_id, language)
    out = avatar_dir(avatar_id) / "voice" / f"preview_{language}.wav"
    out.parent.mkdir(exist_ok=True)
    _, engine = synthesize(body.get("text", "Teste de voz."), language, profile, out)
    return {"engine": engine, "url": f"/avatars/{avatar_id}/voice/{language}/preview.wav"}


@router.get("/{avatar_id}/voice/{language}/preview.wav")
def preview_file(avatar_id: int, language: str):
    from cvm.paths import avatar_dir

    return FileResponse(avatar_dir(avatar_id) / "voice" / f"preview_{language}.wav", media_type="audio/wav", headers=NO_CACHE)


# ---------- voice studio (~30 min, one sentence at a time, for fine-tuning: cvm/voice/studio.py) ----------

def _studio_lang(language: str) -> str:
    from cvm.voice import studio

    if language not in studio.LANGS:
        raise HTTPException(400, f"language must be one of {studio.LANGS}")
    return language


@router.get("/{avatar_id}/studio/{language}")
def studio_summary(avatar_id: int, language: str):
    from cvm.voice import studio

    return studio.summary(avatar_id, _studio_lang(language))


@router.post("/{avatar_id}/studio/{language}/noise")
def studio_noise(avatar_id: int, language: str, file: UploadFile = File(...)):
    from cvm.voice import studio

    tmp = _tmp_upload(file)
    try:
        return studio.save_noise(avatar_id, _studio_lang(language), tmp)
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/{avatar_id}/studio/{language}/{idx}")
def studio_take(avatar_id: int, language: str, idx: int, file: UploadFile = File(...)):
    from cvm.voice import studio

    tmp = _tmp_upload(file)
    try:
        return studio.save_take(avatar_id, _studio_lang(language), idx, tmp)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        tmp.unlink(missing_ok=True)


@router.delete("/{avatar_id}/studio/{language}/{idx}")
def studio_delete(avatar_id: int, language: str, idx: int):
    from cvm.voice import studio

    studio.delete_take(avatar_id, _studio_lang(language), idx)
    return {"deleted": idx}


@router.get("/{avatar_id}/studio/{language}/{idx}.wav")
def studio_take_file(avatar_id: int, language: str, idx: int):
    from cvm.voice import studio

    f = studio.folder(avatar_id, _studio_lang(language)) / f"{idx:04d}.wav"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="audio/wav", headers=NO_CACHE)


@router.get("/{avatar_id}/studio-export.zip")
def studio_export(avatar_id: int):
    """Dataset for scripts/voice_finetune.py (the CUDA PC downloads it from here over the network)."""
    from fastapi.responses import Response

    from cvm.voice import studio

    try:
        data = studio.export_zip(avatar_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="voz_avatar_{avatar_id}.zip"', **NO_CACHE})
