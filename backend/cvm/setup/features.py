"""Install wizard features step: what the user wants to see and collect. Setting "features":
{"ai_ideas", "regions": [ISO2...] | ["GLOBAL"], "lives": ["youtube","twitch","kick"], "vidiq", "follow", "avatars",
"elevenlabs"}. No row (the owner's machine) = everything on and `settings.regions` untouched. Turning a feature off
only changes this Setting: nothing on disk is deleted. API keys go to the keystore, never to a Setting."""

from sqlmodel import Session

from cvm import keystore
from cvm.config import settings
from cvm.models import Setting

KEY = "features"
LIVES = ("youtube", "twitch", "kick")
BOOLS = ("ai_ideas", "vidiq", "follow", "avatars", "elevenlabs")
# request field -> keystore name (the config fields of the same name read the keystore after .env)
KEYS = {"pexels": "pexels_api_key", "pixabay": "pixabay_api_key", "youtube": "youtube_api_key",
        "twitch_id": "twitch_client_id", "twitch_secret": "twitch_client_secret",
        "elevenlabs": keystore.ELEVENLABS}


def defaults() -> dict:
    return {"ai_ideas": True, "regions": list(settings.regions), "lives": list(LIVES), "vidiq": True,
            "follow": True, "avatars": True, "elevenlabs": True}


def get(session: Session) -> dict:
    row = session.get(Setting, KEY)
    return {**defaults(), **(dict(row.value) if row is not None else {})}


def _clean(data: dict) -> dict:
    out: dict = {}
    for k in BOOLS:
        if k in data:
            out[k] = bool(data[k])
    if "regions" in data:
        regs = [str(r).strip().upper() for r in data["regions"] or [] if str(r).strip()]
        if not regs:
            raise ValueError("escolha ao menos uma região")
        out["regions"] = ["GLOBAL"] if "GLOBAL" in regs else regs
    if "lives" in data:
        out["lives"] = [p for p in data["lives"] or [] if p in LIVES]
    return out


def put(session: Session, data: dict) -> dict:
    new = {**get(session), **_clean(data)}
    row = session.get(Setting, KEY)
    if row is None:
        session.add(Setting(key=KEY, value=new))
    else:
        row.value = new
        session.add(row)
    session.commit()
    return new


def ai_ideas(session: Session) -> bool:
    """"Sugestões da IA": off = no trend analysis, channel ideas, autopilot or weekly ideas run (no subscription spent).
    No row = on; a null value or a broken/unreadable row also counts as on (never block runs by accident)."""
    try:
        return get(session).get("ai_ideas") is not False
    except Exception:  # noqa: BLE001
        return True


def regions(session: Session) -> list[str]:
    return list(get(session)["regions"])


def put_keys(data: dict) -> list[str]:
    """Stores the non-empty keys; returns the field names saved. Raises ValueError on a malformed key."""
    todo = {f: str(data.get(f) or "").strip() for f in KEYS}
    todo = {f: v for f, v in todo.items() if v}
    for v in todo.values():  # validate all first: a bad key must not leave a partial save
        if not keystore._VALID.match(v):
            raise ValueError("chave em formato inválido")
    for f, v in todo.items():
        keystore.put(KEYS[f], v)
    return list(todo)


def keys_set() -> dict[str, bool]:
    from cvm.config import key

    return {f: bool(key(n) if n != keystore.ELEVENLABS else keystore.elevenlabs_key()) for f, n in KEYS.items()}
