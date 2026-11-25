"""Word normalisation for the editorial format: scene anchors are compared with the narration through `norm`."""

import re
import unicodedata

from num2words import num2words


def _plain(w: str) -> str:
    w = unicodedata.normalize("NFKD", w.lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in w if not unicodedata.combining(c)))


def _spelled() -> dict[str, str]:
    out: dict[str, str] = {}
    for lang in ("pt_BR", "en"):
        for n in range(101):
            out.setdefault(_plain(num2words(n, lang=lang)), str(n))
    return out


_NUMBERS = _spelled()


def norm(word: str) -> str:
    """Lowercase, no accents, no punctuation; spelled numbers 0-100 (pt and en) become digits ("seis" = "six" = "6"). Single tokens and hyphenated English ("twenty-one") are mapped;
    compound spelled numbers split over words ("vinte e um") are not merged."""
    w = _plain(word)
    return _NUMBERS.get(w, w)
