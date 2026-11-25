"""Word-level captions as an ASS file (karaoke-style highlight of the spoken word).

Word timings come from mlx-whisper on the narration; if the model is unavailable we spread each
scene's words evenly over its known duration (scene timings come from TTS, so this is still close).
"""

import logging
import re
import subprocess
import threading
from pathlib import Path

from cvm.models_registry import cuda_loader, repo

log = logging.getLogger(__name__)
WHISPER_LANG = {"pt-BR": "pt", "ar-AE": "ar", "en-US": "en"}
# one whisper at a time per process: two threads loading it at once crashed the worker (numba JIT race, SIGABRT)
_WHISPER_LOCK = threading.Lock()


@cuda_loader("voice")
def _whisper_cuda():
    import torch
    from transformers import pipeline

    return pipeline("automatic-speech-recognition", model=repo("whisper"), torch_dtype=torch.float16, device="cuda:0")


def _pcm16k(audio: Path):
    import numpy as np

    from cvm.config import settings

    raw = subprocess.run([str(settings.ffmpeg_bin), "-v", "error", "-i", str(audio), "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="float32")


class _ProgressBar:
    """Stands in for tqdm.tqdm inside mlx_whisper.transcribe: its frame counter (100 frames = 1 s of audio) goes to
    `on_progress(done_s, total_s)` instead of the terminal."""

    def __init__(self, on_progress, total=None, **_):
        self.on_progress, self.total, self.n = on_progress, float(total or 0), 0.0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, n=1):
        self.n += n
        try:
            self.on_progress(self.n / 100.0, self.total / 100.0)
        except Exception:  # noqa: BLE001 — progress never breaks the transcript
            pass


def _mlx_progress(on_progress):
    """(verbose, context manager) for mlx_whisper.transcribe: with a callback, its tqdm bar is swapped for
    _ProgressBar while the call runs (inside _WHISPER_LOCK, so no other transcript sees the swap)."""
    import contextlib
    import sys
    import types

    if on_progress is None:
        return None, contextlib.nullcontext()
    mod = sys.modules.get("mlx_whisper.transcribe")
    if mod is None or not hasattr(mod, "tqdm"):
        return None, contextlib.nullcontext()

    @contextlib.contextmanager
    def swap():
        real = mod.tqdm
        mod.tqdm = types.SimpleNamespace(tqdm=lambda *a, **k: _ProgressBar(on_progress, **k))
        try:
            yield
        finally:
            mod.tqdm = real
    return False, swap()


def transcribe(audio: Path, language: str, words: bool = False, on_progress=None) -> dict:
    """{"text", "words": [{"w", "s", "e"}]} (words only when asked) — mlx-whisper on the Mac, Whisper on CUDA elsewhere.
    on_progress(done_s, total_s): how much of the audio is done (mlx-whisper only; the CUDA pipeline has no hook)."""
    from cvm.models_registry import MLX, repo

    if MLX:
        import mlx_whisper

        with _WHISPER_LOCK:
            verbose, bar = _mlx_progress(on_progress)
            with bar:
                res = mlx_whisper.transcribe(str(audio), path_or_hf_repo=repo("whisper"), word_timestamps=words,
                                             language=WHISPER_LANG[language], condition_on_previous_text=words,
                                             verbose=verbose)
        ws = [{"w": w["word"].strip(), "s": w["start"], "e": w["end"]} for seg in res["segments"] for w in seg.get("words", [])]
        return {"text": res.get("text", "").strip(), "words": ws}
    res = _whisper_cuda()({"raw": _pcm16k(audio), "sampling_rate": 16000}, chunk_length_s=30, batch_size=4,
                          return_timestamps="word" if words else False,
                          generate_kwargs={"language": WHISPER_LANG[language], "task": "transcribe"})
    ws = []
    for c in res.get("chunks", []) if words else []:
        s, e = c["timestamp"]
        ws.append({"w": c["text"].strip(), "s": s, "e": e if e is not None else s + 0.3})
    return {"text": res.get("text", "").strip(), "words": ws}


def words_from_whisper(audio: Path, language: str) -> list[dict]:
    return transcribe(audio, language, words=True)["words"]


def words_from_script(scenes: list[dict], timings: list[dict]) -> list[dict]:
    out = []
    by_idx = {t["index"]: t for t in timings}
    for sc in scenes:
        t = by_idx.get(sc["index"])
        words = sc["narration"].split()
        if not t or not words:
            continue
        step = (t["end"] - t["start"]) / len(words)
        out += [{"w": w, "s": t["start"] + i * step, "e": t["start"] + (i + 1) * step} for i, w in enumerate(words)]
    return out


def _norm(w: str) -> str:
    import re
    import unicodedata

    w = unicodedata.normalize("NFKD", w.lower()).encode("ascii", "ignore").decode() or w.lower()
    return re.sub(r"[^\w]", "", w)


def align_script(script_words: list[str], heard: list[dict], fallback: list[dict]) -> list[dict]:
    """Show the script's exact words, timed by the matching whisper words (interpolating the gaps)."""
    from difflib import SequenceMatcher

    times: list[tuple[float, float] | None] = [None] * len(script_words)
    sm = SequenceMatcher(a=[_norm(w) for w in script_words], b=[_norm(h["w"]) for h in heard], autojunk=False)
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            times[a + k] = (heard[b + k]["s"], heard[b + k]["e"])
    out = []
    for i, w in enumerate(script_words):
        if times[i] is None:  # interpolate between the nearest known neighbours, else use the TTS-based estimate
            prev = next((times[j][1] for j in range(i - 1, -1, -1) if times[j]), None)
            nxt = next((times[j][0] for j in range(i + 1, len(times)) if times[j]), None)
            if prev is not None and nxt is not None and nxt >= prev:
                times[i] = (prev, max(prev + 0.05, (prev + nxt) / 2))
            else:
                times[i] = (fallback[i]["s"], fallback[i]["e"])
        out.append({"w": w, "s": times[i][0], "e": times[i][1]})
    return out


def word_timings(audio: Path, language: str, scenes: list[dict], timings: list[dict], use_whisper: bool) -> list[dict]:
    from cvm.models_registry import is_ready

    estimate = words_from_script(scenes, timings)
    if use_whisper and is_ready("whisper"):
        try:
            return align_script([w["w"] for w in estimate], words_from_whisper(audio, language), estimate)
        except Exception as e:  # noqa: BLE001
            log.warning("whisper unavailable (%s); using scene-proportional timings", e)
    return estimate


def _ts(t: float) -> str:
    h, rem = divmod(max(t, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


# emoji and pictographs: libass can't draw colour emoji, so they come out as empty boxes (tofu)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\uFE0F\u200D\u20E3"
                    "\U000E0020-\U000E007F]")


def clean_text(text: str) -> str:
    """Text safe for the caption fonts: no emoji, single spaces."""
    return re.sub(r"[ \t]{2,}", " ", _EMOJI.sub("", text)).strip()


SOURCE_LABEL = {"pt-BR": "Fonte", "en-US": "Source", "ar-AE": "المصدر"}


def has_arabic(text: str) -> bool:
    return bool(re.search("[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]", text))


PAUSE_S = 0.8  # a gap in the speech longer than this starts a new caption line


def write_ass(words: list[dict], out: Path, width: int, height: int, margin_bottom: int, overlays: list[dict] | None = None,
              per_line: int = 4, big: bool = False) -> Path:
    """overlays: [{text, s, e, style: Title|Credit|Card|Hook}] for on-screen text and source credits. libass handles RTL.
    big=True (Shorts): bigger words, 3 per line, and the spoken word pops."""
    font_size = int(height * (0.058 if big else 0.045 if height > width else 0.055))
    if big:
        per_line = min(per_line, 3)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Arial Black,{int(font_size * 1.1)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,3,{max(3, font_size // 12)},0,8,60,60,{int(height * 0.08)},-1
Style: Credit,Arial,{int(font_size * 0.45)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,3,2,0,1,40,40,{int(height * 0.03)},-1
Style: Card,Arial Black,{int(font_size * 1.2)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,{max(3, font_size // 12)},0,5,90,90,0,-1
Style: Hook,Arial Black,{int(font_size * 1.7)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,{max(5, font_size // 7)},3,5,70,70,0,-1
Style: Cap,Arial Black,{font_size},&H00FFFFFF,&H0000E5FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,{max(3, font_size // 14)},2,2,60,60,{margin_bottom},-1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    max_chars = (15 if big else 18) if height > width else 34
    pop = "{\\fscx118\\fscy118\\t(0,110,\\fscx100\\fscy100)}" if big else ""
    reset = "\\fscx100\\fscy100" if big else ""
    chunks, cur = [], []
    for w in words:
        # a pause in the speech (silence, the source's own voice) ends the line: it never stays frozen across it
        if cur and (len(" ".join(x["w"] for x in cur + [w])) > max_chars or len(cur) >= per_line
                    or w["s"] - cur[-1]["e"] > PAUSE_S):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    lines = []
    for chunk in chunks:
        start, end = chunk[0]["s"], chunk[-1]["e"]
        for j, cur in enumerate(chunk):
            parts = [(pop + "{\\c&H00E5FF&}" + clean_text(w["w"]).upper() + "{\\c&HFFFFFF&" + reset + "}") if k == j else clean_text(w["w"]).upper()
                     for k, w in enumerate(chunk)]
            seg_end = chunk[j + 1]["s"] if j + 1 < len(chunk) else end
            lines.append(f"Dialogue: 0,{_ts(cur['s'])},{_ts(seg_end)},Cap,,0,0,0,,{' '.join(parts)}")
    for o in overlays or []:
        lines.append(f"Dialogue: 1,{_ts(o['s'])},{_ts(o['e'])},{o['style']},,0,0,0,,{o.get('fx', '')}{clean_text(o['text'])}")
    out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return out
