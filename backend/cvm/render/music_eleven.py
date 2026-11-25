"""Background music by ElevenLabs (paid by the user, 2026-11-23), for videos whose `Project.music_source` is
"elevenlabs" (Phase 7 of docs/superpowers/plans/2026-11-24-painel-reestruturacao.md).

One instrumental track per project, made once and cached in data/projects/<id>/music/eleven.mp3 (a re-render
never pays twice). Credits are the user's monthly plan: /fabrica/novo shows `estimate()` before the video is
created, and `track()` checks again that the track fits above the reserve (settings.elevenlabs_reserve) before
calling. Not enough credits, no key or a failed call → None, and pick_music falls back to ACE-Step (local), so
spent credits never break the queue. The key comes from cvm.voice.eleven and is never logged.

The music API bills per generated minute; the rate is not in the subscription data, so CREDITS_PER_MIN is a first
guess, replaced by the rate measured on each real generation (credits before − after, Setting "eleven_music_rate").
"""

import json
import logging
from pathlib import Path

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from cvm.config import settings
from cvm.models import Project, Setting

log = logging.getLogger(__name__)
TRACK_S = 150  # like the ACE-Step beds: long enough for most videos, longer ones loop with a crossfade in the mix
CREDITS_PER_MIN = 2000  # first guess until a real generation measures it (Setting RATE_KEY)
RATE_KEY = "eleven_music_rate"
PROMPTS = {  # beds that sit under a voice: no lead melody fighting the narration
    "dark": "dark ambient cinematic underscore, slow pulsing synth pads, low drones, distant percussion, mysterious, tense",
    "explainer": "light modern background music for a news explainer, soft electronic beat, warm keys, curious, positive",
    "top_list": "energetic countdown background music, punchy drums, bright synth plucks, building momentum",
    "react": "upbeat playful background groove for a commentary video, funky bass, light drums",
    "kids": "cheerful children's cartoon music, xylophone, ukulele, gentle claps, happy and simple",
    "lesson": "calm lo-fi study background music, soft piano, mellow beat, focused and warm",
    "review": "upbeat modern pop electronic background music for a product review, confident",
    "live": "energetic gaming highlights background music, driving electronic beat, hype but not loud",
}
SUFFIX = ", instrumental background bed under a voice-over, steady energy, no vocals, no lead melody"


def rate(session: Session | None = None) -> float:
    """Credits per generated minute: the last measured one, else the first guess."""
    if session is not None and (row := session.get(Setting, RATE_KEY)) and (row.value or {}).get("per_min"):
        return float(row.value["per_min"])
    return float(CREDITS_PER_MIN)


def cost(seconds: float = TRACK_S, session: Session | None = None) -> int:
    return int(round(seconds / 60 * rate(session)))


def estimate(session: Session | None = None) -> dict:
    """For /fabrica/novo before creating: {available, seconds, credits, remaining, reserve, enough, measured}."""
    from cvm.voice import eleven

    credits = cost(TRACK_S, session)
    out = {"available": eleven.available(), "seconds": TRACK_S, "credits": credits, "remaining": None,
           "reserve": settings.elevenlabs_reserve, "enough": False,
           "measured": bool(session is not None and session.get(Setting, RATE_KEY))}
    if not out["available"]:
        return out
    try:
        out["remaining"] = eleven.remaining()
    except eleven.ElevenError as e:
        out["error"] = str(e)[:200]
        return out
    out["enough"] = out["remaining"] - settings.elevenlabs_reserve >= credits
    return out


def cached(project: Project) -> Path:
    from cvm.paths import project_dir

    return project_dir(project.id) / "music" / "eleven.mp3"


def _save_rate(session: Session, used: int, seconds: float) -> None:
    if used <= 0:
        return
    row = session.get(Setting, RATE_KEY) or Setting(key=RATE_KEY, value={})
    row.value = {"per_min": round(used / (seconds / 60), 1), "last_used": used, "seconds": seconds}
    flag_modified(row, "value")
    session.add(row)
    session.commit()


def track(project: Project, session: Session | None = None) -> Path | None:
    """The project's ElevenLabs track (made once), or None when it can't be made now (the caller falls back)."""
    from cvm.voice import eleven

    out = cached(project)
    if out.exists() and out.stat().st_size > 1000:
        return out
    if not eleven.available():
        log.info("ElevenLabs music: no key, falling back to ACE-Step")
        return None
    need = cost(TRACK_S, session)
    try:
        before = eleven.remaining()
    except eleven.ElevenError as e:
        log.warning("ElevenLabs music: credit check failed: %s", e)
        return None
    if before - settings.elevenlabs_reserve < need:
        log.info("ElevenLabs music: %s credits left, %s needed above the reserve: falling back", before, need)
        return None
    style = getattr(project.style, "value", str(project.style))
    body = {"prompt": PROMPTS.get(style, PROMPTS["explainer"]) + SUFFIX, "music_length_ms": TRACK_S * 1000,
            "force_instrumental": True}
    try:  # one try only: a retry after a timeout could pay twice
        audio = eleven._call_once("/v1/music?output_format=mp3_44100_192", body, "POST")
    except eleven.ElevenError as e:
        log.warning("ElevenLabs music failed: %s", e)
        return None
    if not isinstance(audio, bytes) or len(audio) < 1000:
        log.warning("ElevenLabs music: empty answer")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    out.with_suffix(".json").write_text(json.dumps({"source": "elevenlabs", "license": "own", "prompt": body["prompt"]},
                                                   ensure_ascii=False))
    if session is not None:
        try:
            _save_rate(session, before - eleven.remaining(), TRACK_S)
        except Exception as e:  # noqa: BLE001 — the track is made; the rate is only for the next estimate
            log.info("ElevenLabs music: rate not measured: %s", e)
    return out
