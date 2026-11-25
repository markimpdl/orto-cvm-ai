"""Install wizard progress, kept in Setting "setup" and mirrored as {"done": bool} to data/setup.json
(the shell installer reads that file before Python exists)."""

import json

from sqlmodel import Session, select

from cvm.config import settings
from cvm.models import Channel, Setting

KEY = "setup"
STEPS = ("system", "language", "ai", "features", "models", "engines", "channel", "done")


def _mirror(done: bool) -> None:
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "setup.json").write_text(json.dumps({"done": done}))
    except OSError:
        pass


def get(session: Session) -> dict:
    row = session.get(Setting, KEY)
    if row is not None:
        return dict(row.value)
    if session.exec(select(Channel.id).limit(1)).first() is not None:
        _mirror(True)
        return {"done": True, "legacy": True}
    return {"done": False, "step": "system", "choices": {}}


def _put(session: Session, value: dict) -> dict:
    row = session.get(Setting, KEY)
    if row is None:
        session.add(Setting(key=KEY, value=value))
    else:
        row.value = value
    session.commit()
    _mirror(bool(value.get("done")))
    return value


def save(session: Session, step: str, choices: dict) -> dict:
    cur = get(session)
    merged = {**cur.get("choices", {}), **(choices or {})}
    return _put(session, {**cur, "done": cur.get("done", False), "step": step, "choices": merged})


def finish(session: Session) -> dict:
    cur = get(session)
    return _put(session, {**cur, "done": True, "step": "done", "choices": cur.get("choices", {})})
