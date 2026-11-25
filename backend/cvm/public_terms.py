"""Personal-terms guard for the public copy: finds the owner's names (scripts/public/terms.txt) in text files and in
file names. Used by the cleanup test and by the export (scripts/export_public.py)."""

import re
import unicodedata
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".next", ".pytest_cache", ".ruff_cache"}


def load_terms(path: Path) -> list[str]:
    """One term per line; blank lines and lines starting with '#' are ignored. A term starting with "~" matches whole
    words only ("~Ana" finds "Ana" but not "banana")."""
    return [t.strip() for t in path.read_text(encoding="utf-8").splitlines() if t.strip() and not t.startswith("#")]


def fold(text: str) -> str:
    """Lower case without accents: "Coração" → "coracao", "ÁGUA" → "agua"."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def matcher(terms: list[str]):
    """A function text → the terms found in it (case and accents folded; "~term" = whole words only)."""
    pats = []
    for t in (t.strip() for t in terms):
        word = t.startswith("~")
        body = fold(t.lstrip("~").strip())
        if body:
            pats.append((t, re.compile(rf"(?<!\w){re.escape(body)}(?!\w)" if word else re.escape(body))))

    def found(text: str) -> list[str]:
        folded = fold(text)
        return [t for t, rx in pats if rx.search(folded)]

    return found


def _files(paths: list[Path]):
    for p in paths:
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and not SKIP_DIRS.intersection(f.relative_to(p).parts):
                    yield f


def _text(f: Path) -> str | None:
    try:
        raw = f.read_bytes()
    except OSError:
        return None
    if b"\0" in raw[:8192]:  # binary
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def find_terms(paths: list[Path], terms: list[str]) -> list[str]:
    """Every hit as "path:line: term" (line 0 = the file name), case- and accent-insensitive."""
    found = matcher(terms)
    hits: list[str] = []
    for f in _files([Path(p) for p in paths]):
        hits += [f"{f}:0: {t}" for t in found(f.name)]
        text = _text(f)
        if text is None:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            hits += [f"{f}:{n}: {t}" for t in found(line)]
    return hits
