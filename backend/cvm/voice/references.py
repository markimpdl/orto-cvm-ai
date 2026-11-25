"""Reference voices in every language for each avatar, cloned from the voice it already has.

Chatterbox drawls when it clones a voice into another language (an Arabic reference reading English). VoxCPM2
keeps the timbre of a reference while speaking a new language, so each avatar gets one ~12 s sample per missing
language, read by its own voice; that sample becomes the same-language reference for every later narration.
"""

import logging
from pathlib import Path

from sqlmodel import Session, select

from cvm.models import Avatar, VoiceProfile
from cvm.paths import avatar_dir

log = logging.getLogger(__name__)

LANGUAGES = ("pt-BR", "en-US", "ar-AE")
SAMPLE = {
    "pt-BR": "Olá, tudo bem? Hoje eu vou te contar uma história que pouca gente conhece, mas que muda o jeito como a "
             "gente enxerga o mundo. Fica comigo até o final, porque vale a pena.",
    "en-US": "Hi, how are you doing? Today I'm going to tell you a story that very few people know, but it changes the "
             "way we see the world. Stay with me until the end, because it's worth it.",
    "ar-AE": "مرحبا، كيف حالكم؟ اليوم سأحكي لكم قصة لا يعرفها الكثيرون، لكنها تغير طريقة رؤيتنا للعالم. ابقوا معي "
             "حتى النهاية، لأنها تستحق.",
}
STYLE = "natural, warm and confident presenter, conversational pace, clear diction"


def missing(session: Session, avatar_id: int) -> list[str]:
    have = {p.language for p in session.exec(select(VoiceProfile).where(VoiceProfile.avatar_id == avatar_id)).all()
            if p.ref_audio_path and Path(p.ref_audio_path).exists()}
    return [lang for lang in LANGUAGES if lang not in have] if have else []


def make(session: Session, avatar_id: int, language: str, seed: int = 11) -> VoiceProfile:
    """Read SAMPLE[language] with the avatar's existing voice (VoxCPM2, timbre only) and store it as the reference
    for that language. Whisper must hear the text back, else another seed is tried."""
    from cvm import gpu
    from cvm.voice import qa
    from cvm.voice.engine import _voxcpm
    from cvm.voice.narrate import _natural_pace
    from cvm.voice.text import spell_numbers

    import soundfile as sf

    # always clone the avatar's original recording/design, never a reference made here (no copy of a copy)
    profiles = session.exec(select(VoiceProfile).where(VoiceProfile.avatar_id == avatar_id).order_by(VoiceProfile.id)).all()
    source = next((p for p in profiles if p.ref_audio_path and Path(p.ref_audio_path).exists()
                   and not (p.params or {}).get("made_from")), None)
    if not source:
        raise RuntimeError(f"avatar {avatar_id} has no voice to clone")
    out = avatar_dir(avatar_id) / "voice" / f"ref_{language}.wav"
    text = SAMPLE[language]
    best = None
    for attempt in range(3):
        tmp = out.with_name(f"take_{language}_{attempt}.wav")
        with gpu.section():
            _voxcpm(text, language, source, tmp, seed=seed + attempt, instruct=STYLE)
            heard = qa.heard(tmp, language)
        errors, n = qa.word_errors(text, spell_numbers(heard, language), language)
        log.info("avatar %s %s take %d: %d/%d word errors", avatar_id, language, attempt, errors, n)
        if best is None or errors < best[0]:
            best = (errors, tmp, heard)
        if errors <= max(1, n // 10):
            break
    audio, sr = sf.read(best[1], dtype="float32")
    sf.write(out, _natural_pace(audio, text, language) if sr == 24000 else audio, sr)
    for f in out.parent.glob(f"take_{language}_*.wav"):
        f.unlink()
    profile = session.exec(select(VoiceProfile).where(VoiceProfile.avatar_id == avatar_id,
                                                      VoiceProfile.language == language)).first() \
        or VoiceProfile(avatar_id=avatar_id, language=language)
    profile.ref_audio_path, profile.ref_text = str(out), text
    profile.params = {**(profile.params or {}), "made_from": source.language, "word_errors": best[0]}
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


def make_all(session: Session) -> list[tuple[int, str, int]]:
    done = []
    for avatar in session.exec(select(Avatar)).all():
        for language in missing(session, avatar.id):
            p = make(session, avatar.id, language)
            done.append((avatar.id, language, p.params.get("word_errors")))
    return done
