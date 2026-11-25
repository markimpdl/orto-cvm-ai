"""YouTube chapters for long videos, from the script's `blocks` (one chapter per 60–90 s block).

YouTube only shows chapters when the first one is at 0:00, there are at least 3 and each lasts ≥ 10 s, so the
intro (hook + promise) becomes the first chapter and blocks that would be too short are merged into the one
before. Times are in the final video (the bumper's added seconds included). Scripts without blocks get none here:
the SEO agent builds them from the scene timings as before.
"""

INTRO = {"pt-BR": "Introdução", "en-US": "Intro", "ar-AE": "المقدمة"}
MIN_S = 10.0


def from_blocks(script: dict, scene_starts: dict[int, float], language: str, bumper: dict | None = None,
                t0: float = 0.0, end: float | None = None) -> list[dict]:
    """[{t, title}] or [] when the script has no blocks or they don't make valid chapters."""
    blocks = script.get("blocks") or []
    if not blocks:
        return []

    def shift(t: float) -> float:
        return t + bumper["seconds"] if bumper and t >= bumper["at"] - 0.01 else t

    out = [{"t": 0.0, "title": INTRO.get(language, "Intro")}]
    for b in blocks:
        starts = [scene_starts[i] for i in b.get("scenes") or [] if i in scene_starts]
        if not starts or not (b.get("title") or "").strip():
            continue
        t = shift(min(starts) - t0)
        if t < MIN_S and len(out) == 1:  # no intro before the first block: it opens the video
            out[0]["title"] = b["title"].strip()
            continue
        if t - out[-1]["t"] < MIN_S:
            continue  # too short: stays inside the previous chapter
        out.append({"t": round(t, 2), "title": b["title"].strip()})
    if end is not None and len(out) > 1 and end - out[-1]["t"] < MIN_S:
        out.pop()
    return out if len(out) >= 3 else []


def stamp(t: float) -> str:
    s = int(t)
    h, m = divmod(s // 60, 60)
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def as_text(chapters: list[dict]) -> str:
    """The lines to paste in the description ("0:00 Introdução")."""
    return "\n".join(f"{stamp(c['t'])} {c['title']}" for c in chapters)
