"""Guided reading scripts for recording an avatar (cvm/avatar/texts/*.txt; also read aloud for the bake-offs).

Format: `# Title · direction` headers followed by text; `[...]` lines are stage directions, never read aloud.
"""

import re
from pathlib import Path

TEXTS = Path(__file__).resolve().parent / "texts"
FILES = {"pt-BR": "pt", "ar-AE": "ar", "en-US": "en", "video": "video"}


def sections(kind: str) -> list[dict]:
    out: list[dict] = []
    for line in (TEXTS / f"{FILES[kind]}.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#"):
            title, _, direction = line.lstrip("# ").partition("·")
            out.append({"title": title.strip(), "direction": direction.strip(), "lines": []})
        elif line:
            if not out:
                out.append({"title": "", "direction": "", "lines": []})
            out[-1]["lines"].append({"text": line.strip("[]"), "cue": line.startswith("[")})
    return out


def spoken_text(kind: str, first_only: bool = False) -> str:
    """What is actually said aloud (for TTS tests and reference transcripts)."""
    secs = sections(kind)[:1] if first_only else sections(kind)
    return " ".join(ln["text"] for s in secs for ln in s["lines"] if not ln["cue"])


def as_text(kind: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", (TEXTS / f"{FILES[kind]}.txt").read_text(encoding="utf-8").strip())
