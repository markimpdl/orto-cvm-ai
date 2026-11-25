"""Installs the ready-made avatar "Lucas" (assets/avatars/lucas.zip, built by scripts/pack_lucas.py)."""
import json
import shutil
import uuid
import zipfile
from pathlib import Path

from sqlmodel import Session, select

from cvm.config import REPO_ROOT, settings
from cvm.models import Avatar, VoiceProfile

ZIP = REPO_ROOT / "assets" / "avatars" / "lucas.zip"
NAME = "Lucas"


class LucasError(Exception):
    """Pacote do Lucas inválido ou instalação que falhou (mensagem em pt-BR)."""


def _extract(z: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for info in z.infolist():
        target = (dest / info.filename).resolve()
        if root not in target.parents and target != root:
            raise ValueError(f"unsafe path in zip: {info.filename}")
    z.extractall(dest)


def _voices(session: Session, av: Avatar) -> list[VoiceProfile]:
    return list(session.exec(select(VoiceProfile).where(VoiceProfile.avatar_id == av.id)))


def _healthy(session: Session, av: Avatar) -> bool:
    return bool(av.base_video_dir and Path(av.base_video_dir).is_dir()
                and any(v.ref_audio_path and Path(v.ref_audio_path).exists() for v in _voices(session, av)))


def install(session: Session, zip_path: Path | None = None) -> Avatar:
    rows = list(session.exec(select(Avatar).where(Avatar.name == NAME).order_by(Avatar.id)))
    for a in rows:
        if _healthy(session, a):
            return a
    av = next((a for a in rows if a.base_video_dir), rows[0] if rows else None)  # a broken one is repaired
    zp = zip_path or ZIP
    if not zp.exists():
        raise FileNotFoundError("O pacote do Lucas não está no projeto (assets/avatars/lucas.zip).")
    avatars = settings.data_dir / "avatars"
    tmp = avatars / f".tmp-lucas-{uuid.uuid4().hex}"
    dest: Path | None = None
    created = False
    try:
        tmp.mkdir(parents=True)
        with zipfile.ZipFile(zp) as z:
            man = json.loads(z.read("avatar.json"))
            _extract(z, tmp)
        voices = man["voices"]
        for v in voices:
            if not (tmp / v["ref_file"]).is_file():
                raise ValueError(f"missing file in zip: {v['ref_file']}")
        (tmp / "avatar.json").unlink(missing_ok=True)
        if av is None:
            av = Avatar(name=NAME)
            created = True
        av.status = man["avatar"].get("status") or "ready"
        av.notes = man["avatar"].get("notes")
        session.add(av)
        session.flush()
        dest = avatars / str(av.id)
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
        for old in _voices(session, av):
            session.delete(old)
        av.base_video_dir = str(dest / "base")
        av.podcast_video_dir = str(dest / "podcast") if (dest / "podcast").exists() else None
        session.add(av)
        for v in voices:
            session.add(VoiceProfile(avatar_id=av.id, language=v["language"], engine=v.get("engine") or "voxcpm2",
                                     ref_audio_path=str(dest / v["ref_file"]), ref_text=v.get("ref_text"),
                                     params=v.get("params") or {}))
        session.commit()
    except Exception as e:
        session.rollback()
        shutil.rmtree(tmp, ignore_errors=True)
        if created and dest is not None:
            shutil.rmtree(dest, ignore_errors=True)
        raise LucasError("Não consegui instalar o Lucas: o pacote está incompleto ou o disco encheu. "
                         "Tente de novo.") from e
    session.refresh(av)
    return av
