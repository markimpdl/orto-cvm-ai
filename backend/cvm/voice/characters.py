"""Voices for a kids series' characters. The first time a character speaks, VoxCPM2 designs a voice from the
script's description (`cast`); the reference is saved on the channel and every later line and episode clones it,
so Cacau sounds like Cacau in episode 1 and episode 20."""

import hashlib
import logging
import re
import unicodedata
from pathlib import Path

import numpy as np
import soundfile as sf
from sqlmodel import Session

from cvm.config import settings
from cvm.models import Channel, VoiceProfile

log = logging.getLogger(__name__)

SAMPLE = {
    "pt-BR": "Oi! Eu sou {name}. Hoje a gente vai viver uma aventura muito divertida. Vem comigo? Vamos descobrir juntos!",
    "en-US": "Hi! I'm {name}. Today we're going on a really fun adventure. Will you come with me? Let's find out together!",
    "ar-AE": "مرحبا! أنا {name}. اليوم سنعيش مغامرة ممتعة جدا. هل تأتي معي؟ هيا نكتشف معا!",
}


NARRATOR_SAMPLE = {
    "pt-BR": "Era uma vez, num lugar cheio de cores, uma turma de amigos que adorava descobrir coisas novas. E hoje, uma surpresa estava esperando por eles.",
    "en-US": "Once upon a time, in a place full of colours, a group of friends loved discovering new things. And today, a surprise was waiting for them.",
    "ar-AE": "كان يا ما كان، في مكان مليء بالألوان، مجموعة من الأصدقاء يحبون اكتشاف أشياء جديدة. واليوم كانت هناك مفاجأة تنتظرهم.",
}


def key(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def find(channel: Channel, name: str) -> dict | None:
    return {key(k): v for k, v in (channel.characters or {}).items()}.get(key(name))


def design_voice(channel: Channel, name: str, description: str, language: str) -> dict:
    """Create the character's reference voice (VoxCPM2 voice design, ~10 s) and return its cast entry."""
    from cvm import gpu
    from cvm.models_registry import MLX
    from cvm.voice import engine

    d = settings.data_dir / "channels" / str(channel.id) / "voices"
    d.mkdir(parents=True, exist_ok=True)
    # ASCII file names: an accented name ("Cleópatra") is stored NFD by macOS on exFAT disks and NFC in the DB,
    # so the other OS would not find it
    ascii_name = unicodedata.normalize("NFKD", key(name)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^\w]+", "_", ascii_name) or "personagem"
    wav = d / f"{slug}.wav"
    text = (NARRATOR_SAMPLE if key(name) in NARRATOR else SAMPLE).get(language, SAMPLE["pt-BR"]).format(name=name)
    seed = int(hashlib.sha1(f"{channel.id}|{slug}".encode()).hexdigest()[:6], 16)
    with gpu.section():
        if MLX:
            import mlx.core as mx

            m = engine._voxcpm2()
            mx.random.seed(seed)
            audio = np.concatenate([np.array(r.audio, dtype="float32") for r in m.generate(text=text, instruct=description)])
            sf.write(wav, audio, m.sample_rate)
        else:  # CUDA (Windows PC): VoxCPM2 designs the voice from the "(description)" prefix, no reference
            import torch

            m = engine._voxcpm2_cuda()
            torch.manual_seed(seed)
            sf.write(wav, m.generate(text=f"({description}){text}"), m.tts_model.sample_rate)
    log.info("designed voice for %s (%s): %s", name, description, wav)
    return {"description": description, "voice": str(wav), "ref_text": text, "language": language}


NARRATOR = {"narrador", "narradora", "narrator"}


LOOK_KEYS = ("look", "sheet", "sheet_seed", "sheet_note")  # the character's drawing (cvm/visuals/cast.py)


def _look(entry: dict | None) -> dict:
    """The drawing part of a cast entry, kept when its voice is (re)designed."""
    return {k: v for k, v in (entry or {}).items() if k in LOOK_KEYS}


def _has_voice(entry: dict | None) -> bool:
    """The entry has a saved reference wav (an empty path would point at the current folder and look present)."""
    v = (entry or {}).get("voice")
    return bool(v) and Path(v).is_file()


def _profile(channel: Channel, name: str, entry: dict, language: str) -> VoiceProfile:
    pid = -int(hashlib.sha1(f"{channel.id}|{key(name)}".encode()).hexdigest()[:7], 16)  # stable, never a real id
    return VoiceProfile(id=pid, avatar_id=0, language=entry.get("language", language), ref_audio_path=entry["voice"],
                        ref_text=entry.get("ref_text"), params={"engine": "voxcpm2"})


def narrator_for(channel: Channel | None, language: str) -> VoiceProfile | None:
    """The series' own narrator voice (a "Narradora"/"Narrador" entry in the cast), if it has one."""
    for name, entry in (channel.characters or {}).items() if channel else []:
        if key(name) in NARRATOR and _has_voice(entry):
            return _profile(channel, name, entry, language)
    return None


def profile_for(session: Session, channel: Channel | None, name: str | None, language: str,
                cast: dict[str, str]) -> VoiceProfile | None:
    """A (not stored) voice profile for a speaking character, designing and saving its voice the first time.
    The narrator resolves to the series' narrator voice when it has one; None when there is nothing to use."""
    if not channel or not name:
        return None
    if key(name) in NARRATOR and (own := narrator_for(channel, language)):
        return own
    entry = find(channel, name)
    if key(name) in NARRATOR and not entry:  # a new series: its narrator is designed like any character
        entry = next(({**v, "name": k} for k, v in (channel.characters or {}).items() if key(k) in NARRATOR), None)
        name = (entry or {}).pop("name", name)
    if not entry or not _has_voice(entry):
        description = next((v for k, v in cast.items() if key(k) == key(name)), None) or (entry or {}).get("description")
        if not description:
            return None
        from cvm.models_registry import is_ready

        if not is_ready("voxcpm2"):
            log.warning("voxcpm2 not cached: %s keeps the narrator's voice", name)
            return None
        entry = {**_look(entry), **design_voice(channel, name, description, language)}
        channel.characters = {**(channel.characters or {}), name: entry}  # new dict so the JSON column saves
        session.add(channel)
        session.commit()
    return _profile(channel, name, entry, language)


def save_voice(session: Session, channel: Channel, name: str, description: str, language: str) -> dict:
    """(Re)design one character's voice from a description and store it on the series."""
    entry = {**_look(find(channel, name)), **design_voice(channel, name, description, language)}
    chars = {k: v for k, v in (channel.characters or {}).items() if key(k) != key(name)}
    channel.characters = {**chars, name: entry}
    session.add(channel)
    session.commit()
    return entry
