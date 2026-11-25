"""Reference sheets for a kids series' characters (FLUX.2 Klein).

A series on `Channel.prefs.image_engine = "klein"` draws every scene from its characters' sheets, so Cacau is the
same turtle in scene 1 and in episode 20 (the old way, FLUX.1 with a fixed seed, changed faces, clothes and even the
number of characters between scenes: bakeoff/klein_episode, 2026-11-16).

The sheet is drawn once, the first time a script brings the character's look (`cast_looks`), checked by the vision
judge, and saved on the series next to the character's voice: `Channel.characters[name]` gains `look`, `sheet`
(data/channels/<id>/cast/<name>.png) and `sheet_seed`. The user sees it on the series page and can redraw it.
"""

import logging
import re
import unicodedata
from pathlib import Path

from sqlmodel import Session

from cvm.config import settings
from cvm.models import Channel

log = logging.getLogger(__name__)

ENGINE = "klein"
SHEET_SIZE = (1024, 1024)
SHEET_TRIES = 3  # seeds tried while the judge refuses the sheet; the best-scoring one is kept
# three-quarter view: facing the viewer, Klein mirrors a tail on both sides of the body (a fox with two tails)
SHEET_PROMPT = ("Character design sheet of {look}. Exactly one character, full body, standing in a three-quarter view "
                "turned slightly to the side, looking at the viewer, friendly smile, correct anatomy, "
                "plain light cream background, no text, no letters")
KEEP = ("Keep each character exactly as in its reference image: same face, same eye colour, same colours, "
        "same clothes and accessories. Only these characters, each one once.")


def uses_sheets(series: Channel | None) -> bool:
    """True when this series draws with Klein and the model is on this machine."""
    from cvm.visuals import sources

    return bool(series) and (series.prefs or {}).get("image_engine") == ENGINE \
        and sources.image_engine(True, ENGINE) == ENGINE


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", _key(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^\w]+", "_", ascii_name).strip("_") or "personagem"


def sheet_path(channel: Channel, name: str) -> Path:
    return settings.data_dir / "channels" / str(channel.id) / "cast" / f"{_slug(name)}.png"


def _entry(channel: Channel, name: str) -> tuple[str, dict]:
    """(stored name, entry) of a character, matched without case; (name, {}) when new."""
    for k, v in (channel.characters or {}).items():
        if _key(k) == _key(name):
            return k, v
    return name, {}


def sheet_of(channel: Channel | None, name: str) -> Path | None:
    if not channel:
        return None
    p = _entry(channel, name)[1].get("sheet")
    return Path(p) if p and Path(p).is_file() else None


def _base_seed(channel: Channel, name: str) -> int:
    return 7000 + (channel.id or 0) * 100 + sum(map(ord, _key(name))) % 100


def draw_sheet(channel: Channel, name: str, look: str, seed: int | None = None) -> dict:
    """Draw (or redraw) one character's sheet; returns {look, sheet, sheet_seed, sheet_note?}. With the vision
    judge on, a refused sheet is retried with the next seed and the best one stays."""
    from cvm.visuals import judge, sources

    out = sheet_path(channel, name)
    out.parent.mkdir(parents=True, exist_ok=True)
    first = seed if seed is not None else _base_seed(channel, name)
    check = settings.visual_judge and judge.ready()
    best = None  # (score, seed, file, problems)
    for n in range(SHEET_TRIES if check else 1):
        s = first + n
        take = out.with_name(f"{out.stem}_s{s}.png")
        sources.klein_image(SHEET_PROMPT.format(look=look), take, seed=s, size=SHEET_SIZE)
        if not check:
            best = (None, s, take, [])
            break
        v = judge.judge([{"image": str(take), "kind": "image",
                          "context": f"Reference drawing of exactly one cartoon character: {look}. "
                                     "One head, one tail at most, the right number of limbs."}], cartoon=True)[0]
        score = v.get("score") if v.get("score") is not None else 0
        log.info("cast sheet %s seed %d: score %s %s", name, s, v.get("score"), v.get("problems"))
        if best is None or score > (best[0] or 0):
            best = (score, s, take, v.get("problems") or [])
        if v.get("ok"):
            best = (score, s, take, [])
            break
    best[2].replace(out)
    for leftover in out.parent.glob(f"{out.stem}_s*.png"):
        leftover.unlink(missing_ok=True)
    entry = {"look": look, "sheet": str(out), "sheet_seed": best[1]}
    if best[3]:
        entry["sheet_note"] = "; ".join(str(p) for p in best[3])[:300]
    return entry


def save_sheet(session: Session, channel: Channel, name: str, look: str, seed: int | None = None) -> dict:
    """Draw the sheet and store it on the series, keeping the character's voice."""
    drawn = draw_sheet(channel, name, look, seed)
    stored, old = _entry(channel, name)
    entry = {k: v for k, v in old.items() if k not in ("sheet_note", "sheet_pending", "sheet_error")}
    channel.characters = {**(channel.characters or {}), stored: {**entry, **drawn}}  # new dict so the JSON column saves
    session.add(channel)
    session.commit()
    return channel.characters[stored]


def ensure_sheets(session: Session, series: Channel, looks: dict[str, str]) -> list[str]:
    """Draw the sheet of every character in `looks` (a script's `cast_looks`) that has none yet. A character that
    already has a sheet keeps it, whatever the new script says: the look is fixed by the first one."""
    made = []
    for name, look in (looks or {}).items():
        if not (look or "").strip() or sheet_of(series, name):
            continue
        try:
            save_sheet(session, series, name, look.strip())
            made.append(name)
        except Exception as e:  # noqa: BLE001 — the scene is still drawn, from its text alone
            log.warning("cast sheet failed for %s: %s", name, e)
    return made


def scene_cast(series: Channel, scene: dict) -> list[tuple[str, Path]]:
    """The characters with a sheet that appear in a scene, in a stable order: those listed in `visual.characters`,
    else those named in the image prompt, the motion prompt or as the speaker."""
    from cvm.visuals.sources import KLEIN_MAX_REFS

    visual = scene.get("visual") or {}
    listed = [str(n) for n in visual.get("characters") or []]
    text = " ".join(str(x or "") for x in (visual.get("image_prompt"), visual.get("motion_prompt"), scene.get("speaker")))
    out = []
    for name in (series.characters or {}):
        sheet = sheet_of(series, name)
        if not sheet:
            continue
        named = any(_key(n) == _key(name) for n in listed) if listed else \
            bool(re.search(rf"(?<!\w){re.escape(name.strip())}(?!\w)", text, re.IGNORECASE))
        if named:
            out.append((name, sheet))
    return out[:KLEIN_MAX_REFS]


def prompt_with_refs(prompt: str, cast: list[tuple[str, Path]]) -> str:
    """Tell Klein which reference image is which character, then the scene."""
    if not cast:
        return prompt
    who = " ".join(f"{name} is the character in image {i + 1}." for i, (name, _) in enumerate(cast))
    return f"{who} {prompt.rstrip('. ')}. {KEEP}"
