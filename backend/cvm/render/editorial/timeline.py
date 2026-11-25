"""Editorial timeline: the script's scene anchors (one narration word each) become seconds on the narration.

Ported from the bake-off renderer (bakeoff/editorial/render.py `resolve`). `words` are `word_timings` dicts
{"w", "s", "e"}; every returned scene has `start`/`end` (video seconds) plus `_w` (index of its anchor word).
"""

from pathlib import Path

from sqlmodel import Session

from cvm.editorial_text import norm  # noqa: F401  (re-exported)
from cvm.models import Project

TAIL = 1.3  # the last scene holds this long past the narration
LEAD = 0.08  # a scene starts this long before its anchor word
BUMPER_GAP = 0.12  # the bumper starts this long after the hook's last word
MIN_SCENE = 1.2  # shorter scenes merge into the previous one


class EditorialError(RuntimeError):
    fix_state = "scripting"


def _find(words: list[dict], word: str, start: int) -> int:
    for j in range(start, len(words)):
        if norm(words[j]["w"]) == norm(word):
            return j
    after = words[start]["w"] if start < len(words) else "(fim)"
    raise EditorialError(f"A palavra-âncora '{word}' não aparece na narração depois da palavra {start} ({after}). "
                         "Reescreva a cena com uma âncora que seja dita nessa ordem.")


def resolve(cenas: list[dict], words: list[dict], total: float, hook_end: float | None, bumper_s: float) -> list[dict]:
    """Scene start = 80 ms before its anchor word; intra-scene anchors become seconds from the scene start."""
    i = 0
    out: list[dict] = []
    for s in cenas:
        j = _find(words, s["em"], i)
        out.append(dict(s, _w=j, start=max(0.0, words[j]["s"] - LEAD) if out else 0.0))
        i = j + 1
    for k, s in enumerate(out):
        s["end"] = out[k + 1]["start"] if k + 1 < len(out) else total + TAIL
    split = None
    if hook_end is not None and bumper_s > 0:
        he = hook_end + BUMPER_GAP
        for k, s in enumerate(out):
            if s["start"] < he < s["end"]:
                nxt = s["end"]
                s["end"] = he
                split = s
                out.insert(k + 1, {"tipo": "vinheta", "start": he, "end": nxt, "_w": s["_w"]})
                break
    sec = None
    for s in out:  # sections carry over; a scene that opens a section animates the header in
        if s.get("secao"):
            s["secao_nova"] = True
            sec = s["secao"]
        elif s["tipo"] not in ("vinheta", "impacto"):
            s["secao"] = sec
        if s["tipo"] == "vinheta":
            continue

        def rel(word: str, s=s) -> float:
            return round(words[_find(words, word, s["_w"])]["s"] - s["start"], 3)

        hl = (s.get("prova") or {}).get("destaque_em")
        if hl:
            s["_hl"] = [rel(x) for x in (hl if isinstance(hl, list) else [hl])]
        if s.get("itens"):
            s["_items"] = [{"t": rel(a) if k else min(0.12, rel(a)), "text": b} for k, (a, b) in enumerate(s["itens"])]
        if s.get("impacto_em"):
            s["impacto_t"] = rel(s["impacto_em"])
    if split is not None:  # the hook scene lost its tail to the bumper: cues past its new end are clamped
        cap = max(0.0, split["end"] - split["start"] - 0.2)
        if split.get("_hl"):
            split["_hl"] = [min(x, cap) for x in split["_hl"]]
        for it in split.get("_items", []):
            it["t"] = min(it["t"], cap)
        if split.get("impacto_t") is not None:
            split["impacto_t"] = min(split["impacto_t"], cap)
    merged: list[dict] = []
    carry: float | None = None  # start of a short card that sat right after the bumper: the next scene takes over its time
    for s in out:  # a short plain text card joins the previous scene; scenes with content and the bumper are never touched
        if carry is not None:
            s["start"] = carry
            carry = None
        if merged and s["end"] - s["start"] < MIN_SCENE and _plain_card(s) and not s.get("secao_nova"):
            if merged[-1]["tipo"] == "vinheta":
                carry = s["start"]
            else:
                merged[-1]["end"] = s["end"]
            continue
        merged.append(s)
    return merged


def _plain_card(s: dict) -> bool:
    return s["tipo"] in ("pergunta", "impacto") and not any(s.get(k) for k in ("itens", "prova", "valor", "_hl", "_items"))


def _narration_text(content: dict) -> list[str]:
    return [str(sc.get("narration", "")) for sc in (content or {}).get("scenes") or []]


def _same_narration(session: Session, project_id: int, language: str, version: int, latest) -> bool:
    """A newer script version (e.g. the editorial backfill) with the same narration text needs no new voice."""
    from sqlmodel import select

    from cvm.models import Script

    try:
        old = session.exec(select(Script).where(Script.project_id == project_id, Script.language == language,
                                                Script.version == version)).first()
    except Exception:  # noqa: BLE001
        return False
    return old is not None and _narration_text(old.content) == _narration_text(latest.content)


def words_for(session: Session, project: Project, language: str) -> tuple[list[dict], float]:
    """Word timings of the project's narration in `language` and the narration length (seconds)."""
    from cvm.config import settings
    from cvm.render.captions import word_timings
    from cvm.voice.narrate import latest_script, narration_asset

    narr = narration_asset(session, project.id, language)
    script = latest_script(session, project.id, language)
    if not narr or not script:
        raise EditorialError("Falta a narração ou o roteiro para alinhar as cenas editoriais.")
    sv = narr.meta.get("script_version")
    if sv is not None and sv != script.version and not _same_narration(session, project.id, language, sv, script):
        err = EditorialError("A narração é de outra versão do roteiro; gere a voz de novo.")
        err.fix_state = "voicing"
        raise err
    words = word_timings(Path(narr.path), language, script.content["scenes"], narr.meta["scenes"],
                         settings.use_whisper_captions)
    dur = narr.meta.get("duration")
    return words, float(dur if dur is not None else (words[-1]["e"] if words else 0.0))
