"""Checks on synthesised speech.

- trim_edges: drop leading/trailing silence so gaps between sentences are the ones we choose.
- word_error_rate + heard: whisper round trip; a high WER means the TTS skipped, repeated or garbled words
  (cloned voices hallucinate now and then), so narrate.py regenerates that sentence with another seed.
- loop_artifact: the same short word run repeated many times is a classic TTS failure even when WER looks ok.
- noise_bursts: a loud stretch of hiss (no words, so whisper can't see it) — the voice model now and then breathes
  static instead of a pause (an episode made on the PC had two, 0.8–1 s long).
"""

import re
import unicodedata
from pathlib import Path

import numpy as np

SILENCE_DB = -40.0
KEEP_S = 0.04  # keep a little air so consonants are not clipped


def trim_edges(audio: np.ndarray, sr: int) -> np.ndarray:
    if not len(audio):
        return audio
    win = max(1, int(0.01 * sr))
    n = len(audio) // win
    if n == 0:
        return audio
    rms = np.sqrt(np.mean(audio[: n * win].reshape(n, win) ** 2, axis=1) + 1e-12)
    loud = np.nonzero(20 * np.log10(rms) > SILENCE_DB)[0]
    if not len(loud):
        return audio
    keep = int(KEEP_S * sr)
    start = max(0, loud[0] * win - keep)
    end = min(len(audio), (loud[-1] + 1) * win + keep)
    return audio[start:end]


def _tokens(text: str, language: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text.lower())
    if language == "ar-AE":  # ignore diacritics, tatweel and alef/yeh/teh-marbuta spelling variants
        text = re.sub(r"[ً-ْـ]", "", text)
        text = text.translate(str.maketrans("أإآىة", "ااايه"))
    else:
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode() or text
    return re.findall(r"\w+", text)


def word_errors(expected: str, heard_text: str, language: str) -> tuple[int, int]:
    """(edit distance in words, words expected). Both sides go through the narration's text preparation first, so
    whisper's digits ("4K", "1080", "18:30") match the words of the script ("quatro K", "mil e oitenta", ...)."""
    from cvm.voice.text import prepare

    ref, hyp = _tokens(prepare(expected, language), language), _tokens(prepare(heard_text, language), language)
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1], len(ref)


def first_word_ok(expected: str, heard_text: str, language: str) -> bool:
    """The take starts with the sentence's first word (whisper heard it, allowing one-letter slips on longer words)."""
    from cvm.voice.text import prepare

    ref, hyp = _tokens(prepare(expected, language), language), _tokens(prepare(heard_text, language), language)
    if not ref:
        return True
    if not hyp:
        return False
    a, b = ref[0], hyp[0]
    if a == b:
        return True
    if len(a) < 4 or abs(len(a) - len(b)) > 1:
        return False
    return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b)) <= 1


def word_error_rate(expected: str, heard_text: str, language: str) -> float:
    errors, n = word_errors(expected, heard_text, language)
    return errors / n if n else 0.0


def loop_artifact(heard_text: str, language: str) -> bool:
    toks = _tokens(heard_text, language)
    for n in (1, 2, 3):
        for i in range(len(toks) - n * 4 + 1):
            gram = toks[i:i + n]
            if all(toks[i + k * n:i + (k + 1) * n] == gram for k in range(1, 4)):
                return True
    return False


def heard(wav: Path, language: str) -> str:
    from cvm.render.captions import transcribe

    return transcribe(wav, language)["text"]


def heard_words(wav: Path, language: str) -> tuple[str, list[dict]]:
    """(text, [{"w", "s", "e"}]) — the whisper round trip with word times."""
    from cvm.render.captions import transcribe

    r = transcribe(wav, language, words=True)
    return r["text"], r.get("words") or []


EDGE_GAP_S = 0.2  # a made-up word after this much silence can be cut off cleanly


def edge_extras(expected: str, words: list[dict], language: str) -> dict:
    """Words the voice made up outside the sentence: after its last word (643 scene 25: "...do passeio inteiro." then
    0.7 s of silence and "Tiff e...") or before its first. {"tail": text, "cut_at": s} when a pause separates the
    tail (cut there), {"tail": text} when it is glued to the sentence (the take is bad), {"lead": text}; {} if clean.
    A word that is in the sentence's last/first words is not counted (whisper repeats or splits words)."""
    from cvm.voice.text import prepare

    ref = _tokens(prepare(expected, language), language)
    hyp = [(_tokens(prepare(w.get("w", ""), language), language), float(w.get("s", 0)), float(w.get("e", 0))) for w in words]
    hyp = [h for h in hyp if h[0]]
    if not ref or not hyp:
        return {}
    out: dict = {}
    end_set, start_set = set(ref[-6:]), set(ref[:6])
    last = next((i for i in range(len(hyp) - 1, -1, -1) if set(hyp[i][0]) & set(ref[-2:])), None)
    if last is not None and last < len(hyp) - 1:
        tail = [h for h in hyp[last + 1:] if not set(h[0]) & end_set]
        if tail:
            out["tail"] = " ".join(" ".join(h[0]) for h in tail)
            gap = hyp[last + 1][1] - hyp[last][2]
            if gap >= EDGE_GAP_S:
                out["cut_at"] = round(hyp[last][2] + min(0.12, gap / 2), 3)
    first = next((i for i, h in enumerate(hyp) if set(h[0]) & set(ref[:2])), None)
    if first:
        lead = [h for h in hyp[:first] if not set(h[0]) & start_set]
        if lead:
            out["lead"] = " ".join(" ".join(h[0]) for h in lead)
    return out


NOISE_FLAT = 0.25  # spectral flatness (300 Hz–6 kHz) of hiss; voiced speech ~0.0–0.1, an "s" peaks briefly near 0.3
NOISE_MIN_S = 0.3


def noise_bursts(audio: np.ndarray, sr: int) -> list[tuple[float, float]]:
    """(start, end) of loud noise-like stretches of at least NOISE_MIN_S: flat spectrum in the speech band for
    several 50 ms frames in a row, at speaking level. Fricatives are flat too, but only for a frame or two."""
    x = audio if audio.ndim == 1 else audio.mean(1)
    f = int(0.05 * sr)
    n = len(x) // f
    if n < 2:
        return []
    fr = x[: n * f].reshape(n, f).astype(np.float64)
    rms = 20 * np.log10(np.sqrt((fr ** 2).mean(1)) + 1e-9)
    voiced = rms[rms > -40]
    if not len(voiced):
        return []
    freqs = np.fft.rfftfreq(f, 1 / sr)
    band = (freqs > 300) & (freqs < 6000)
    spec = np.abs(np.fft.rfft(fr * np.hanning(f), axis=1))[:, band] ** 2
    flat = np.exp(np.mean(np.log(spec + 1e-12), 1)) / (spec.mean(1) + 1e-12)
    bad = (flat > NOISE_FLAT) & (rms > np.median(voiced) - 12)
    out, i, need = [], 0, int(round(NOISE_MIN_S / 0.05))
    while i < n:
        if bad[i]:
            j = i
            while j + 1 < n and bad[j + 1]:
                j += 1
            if j - i + 1 >= need:
                out.append((round(i * 0.05, 2), round((j + 1) * 0.05, 2)))
            i = j + 1
        else:
            i += 1
    return out
