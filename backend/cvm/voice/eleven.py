"""ElevenLabs voice (paid by the user, 2026-11-23; approved on the editorial example).

A VoiceProfile with params {"engine": "elevenlabs", "voice_id": ..., "model": "eleven_v4", "settings": {...}} is read
here. Credits are the user's monthly plan: narrate.py checks the whole video fits (`fits`) before the first call, so a
video never ends up half in one voice and half in another; below the reserve the trained voice reads instead.
The key comes from the Keychain (Configurações > Integrações), else .env (cvm/keystore.py); never logged.
"""

import json
import logging
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from cvm.config import settings
from cvm.media import to_wav

log = logging.getLogger(__name__)
API = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_v4"
# chosen by A/B on the editorial example's hook (2026-11-23): v4 read at ~180 wpm with natural pauses; v3 and
# multilingual v2 rushed (~205 wpm) and ignored a lower speed
DEFAULT_SETTINGS = {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0, "speed": 1.0}


class ElevenError(RuntimeError):
    pass


def available() -> bool:
    return bool(api_key())


def api_key() -> str | None:
    from cvm.keystore import elevenlabs_key

    return elevenlabs_key()


def _call(path: str, body: dict | None = None, method: str = "GET", tries: int = 3):
    """One API call; network errors (644: "[Errno 49] Can't assign requested address") are retried with a short wait."""
    import time

    for attempt in range(tries):
        try:
            return _call_once(path, body, method)
        except ElevenError as e:
            if attempt + 1 == tries or "HTTP 4" in str(e):  # a 4xx (key, credits, format) does not fix itself
                raise
            time.sleep(2 * (attempt + 1))


def _call_once(path: str, body: dict | None = None, method: str = "GET"):
    if not (key := api_key()):
        raise ElevenError("chave da ElevenLabs não configurada (Configurações > Integrações ou .env)")
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
            return json.loads(raw) if r.headers.get_content_type() == "application/json" else raw
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        raise ElevenError(f"ElevenLabs HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise ElevenError(f"ElevenLabs inacessível: {e.reason}") from e


def remaining() -> int:
    """Credits left this month (character_limit - character_count)."""
    sub = _call("/v1/user/subscription")
    return int(sub.get("character_limit", 0)) - int(sub.get("character_count", 0))


def fits(chars: int) -> bool:
    """The whole narration (with room for a retake) fits in this month's credits above the reserve."""
    try:
        return remaining() - settings.elevenlabs_reserve >= chars
    except ElevenError as e:
        log.warning("ElevenLabs credit check failed: %s", e)
        return False


def synthesize(text: str, params: dict, out: Path, seed: int | None = None,
               context: tuple[str | None, str | None] | None = None) -> Path:
    """Speech for `text` with the profile's ElevenLabs voice → wav at `out`. `context` = (previous, next) text so the
    intonation carries across takes."""
    voice = params.get("voice_id")
    if not voice:
        raise ElevenError("perfil ElevenLabs sem voice_id")
    body = {"text": text, "model_id": params.get("model") or DEFAULT_MODEL,
            "voice_settings": {**DEFAULT_SETTINGS, **(params.get("settings") or {})}}
    if seed is not None:
        body["seed"] = int(seed) % 4294967295
    prev, nxt = context or (None, None)
    if prev:
        body["previous_text"] = prev[-600:]
    if nxt:
        body["next_text"] = nxt[:600]
    audio = _call(f"/v1/text-to-speech/{voice}?output_format=mp3_44100_128", body, "POST")
    if not isinstance(audio, bytes) or len(audio) < 1000:
        raise ElevenError("ElevenLabs devolveu um áudio vazio")
    with tempfile.TemporaryDirectory() as tmp:
        mp3 = Path(tmp) / "eleven.mp3"
        mp3.write_bytes(audio)
        return to_wav(mp3, out)
