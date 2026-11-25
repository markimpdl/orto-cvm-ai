"""Per-second signals of a long video, for the viral detection (cvm/lives/viral.py) and the cut editor
(spec docs/superpowers/specs/2026-11-25-editor-de-cortes-design.md).

Each signal is optional (None when the source has none):
- loudness / motion / action: cvm/visuals/highlights.analyse (`tracks` of its result);
- chat: the live's chat replay (cvm/lives/chat.py), moved back CHAT_LAG s because the audience types after the moment;
- replay: YouTube's "most replayed" graph (`heatmap` of the yt-dlp probe, cvm/lives/ingest.info_from);
- comments: comments with a timestamp ("1:05", "1:02:03"), YouTube Data API commentThreads, only with a key;
- reactions: laughter/screams/cheering. NOT WIRED YET: PANNs would add a ~300 MB model and a full pass over the
  audio of a multi-hour live; `reactions_track` returns None until that is measured (the editor hides the lane).

The transcript of the whole video is made once (local whisper with word times, inside gpu.section) and cached next
to the video as `<stem>.transcript.<language>.json`: re-analysing the same recording never transcribes it again.
"""

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from cvm.config import settings

log = logging.getLogger(__name__)
CHAT_LAG = 15  # s the chat trails what happened on screen
SMOOTH_S = 5  # tags and fused score look at 5 s averages, not single seconds
LINE_MAX_S = 12.0  # one transcript line at most this long
LINE_GAP_S = 0.8  # a pause this long starts a new line
WAVE_SR = 4000  # Hz the waveform peaks are computed at (enough for a picture of the sound)
WEIGHTS = {"loudness": 0.25, "motion": 0.15, "chat": 0.25, "replay": 0.2, "comments": 0.1, "reactions": 0.15}
NAMES = {"loudness": "volume", "motion": "movimento", "chat": "chat", "replay": "replay", "comments": "comentários",
         "reactions": "reação"}
TIME = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")


def lines_from_words(words: list[dict]) -> list[dict]:
    """Whisper words [{w, s, e}] → lines [{s, e, text}] that end at a sentence end, a pause or LINE_MAX_S."""
    out: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            out.append({"s": round(float(cur[0]["s"]), 2), "e": round(float(cur[-1]["e"]), 2),
                        "text": " ".join(w["w"] for w in cur).strip()})
            cur.clear()

    for w in words:
        if not str(w.get("w") or "").strip():
            continue
        if cur and (w["s"] - cur[-1]["e"] > LINE_GAP_S or w["e"] - cur[0]["s"] > LINE_MAX_S):
            flush()
        cur.append(w)
        if re.search(r"[.!?…]$", w["w"].strip()):
            flush()
    flush()
    return out


def transcript_path(video: Path, language: str) -> Path:
    return video.with_name(f"{video.stem}.transcript.{language}.json")


def transcript(video: Path, language: str, transcribe=None) -> list[dict]:
    """Lines [{s, e, text}] of the whole video, cached next to it. Whisper runs inside gpu.section (never beside a
    big model) on a 16 kHz mono copy of the sound."""
    cache = transcript_path(video, language)
    if cache.exists():
        try:
            return json.loads(cache.read_text())["lines"]
        except (ValueError, KeyError):
            pass
    from cvm import gpu
    from cvm.lives import progress

    if transcribe is None:
        from cvm.render.captions import transcribe
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "audio.wav"
        progress.report(progress.TRANSCRIPT, "Transcrevendo: separando o áudio")
        subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                        str(wav)], capture_output=True)
        if not wav.exists() or wav.stat().st_size < 1000:
            return []
        progress.report(progress.TRANSCRIPT, "Transcrição " + progress.GPU_WAIT, waiting=True)
        kw = {"on_progress": transcript_reporter()} if _takes(transcribe, "on_progress") else {}
        with gpu.section():
            progress.report(progress.TRANSCRIPT, "Transcrevendo", pct=0.0)
            res = transcribe(wav, language, words=True, **kw) or {}
        gpu.free_whisper()
    lines = lines_from_words(res.get("words") or [])
    cache.write_text(json.dumps({"language": language, "lines": lines}, ensure_ascii=False))
    return lines


def _takes(fn, name: str) -> bool:
    import inspect

    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def transcript_reporter():
    """whisper's on_progress(done_s, total_s) → "Transcrevendo 1h12 de 3h02"."""
    from cvm.lives import progress

    def on_progress(done: float, total: float) -> None:
        if total > 0:
            done = min(done, total)
            progress.report(progress.TRANSCRIPT, f"Transcrevendo {progress.hms(done)} de {progress.hms(total)}",
                            pct=100.0 * done / total, done=round(done), total=round(total))
    return on_progress


def _per_second(n: int) -> np.ndarray:
    return np.zeros(max(1, n))


def chat_track(dens: dict[int, float], duration: float, lag: int = CHAT_LAG) -> list[float] | None:
    """Weighted chat messages per second (cvm/lives/chat.density), moved back `lag` s."""
    if not dens:
        return None
    n = int(duration) + 1
    out = _per_second(n)
    for s, w in dens.items():
        t = int(s) - lag
        if 0 <= t < n:
            out[t] += w
    return [round(float(v), 2) for v in out]


def heatmap_track(heatmap: list[dict] | None, duration: float) -> list[float] | None:
    """YouTube's "most replayed" buckets → a value (0–1) per second."""
    if not heatmap:
        return None
    n = int(duration) + 1
    out = _per_second(n)
    for h in heatmap:
        a, b = max(0, int(h["start"])), min(n, int(np.ceil(h["end"])))
        out[a:b] = np.maximum(out[a:b], float(h["value"]))
    return [round(float(v), 3) for v in out]


def comment_times(video_id: str, api_key: str | None = None, client=None, pages: int = 5) -> list[tuple[int, float]]:
    """[(second, weight)] of the timestamps written in the video's top comments (weight = likes + 1).
    YouTube Data API commentThreads (free quota, 1 unit per page); [] without a key or on any error."""
    if api_key is None:
        from cvm.config import key

        api_key = key("youtube_api_key")
    if not api_key or not video_id:
        return []
    own = client is None
    if own:
        import httpx

        client = httpx.Client()
    out: list[tuple[int, float]] = []
    token = None
    try:
        for _ in range(pages):
            params = {"part": "snippet", "videoId": video_id, "maxResults": 100, "order": "relevance",
                      "textFormat": "plainText", "key": api_key}
            if token:
                params["pageToken"] = token
            r = client.get("https://www.googleapis.com/youtube/v3/commentThreads", params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            for it in data.get("items") or []:
                sn = ((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
                text = str(sn.get("textOriginal") or sn.get("textDisplay") or "")
                w = float(sn.get("likeCount") or 0) + 1.0
                for h, m, s in TIME.findall(text):
                    out.append((int(h or 0) * 3600 + int(m) * 60 + int(s), w))
            token = data.get("nextPageToken")
            if not token:
                break
    except Exception as e:  # noqa: BLE001 — comments are a bonus signal
        log.warning("comment timestamps of %s unavailable: %s", video_id, e)
    finally:
        if own:
            client.close()
    return out


def comments_track(times: list[tuple[int, float]], duration: float, spread: int = 5) -> list[float] | None:
    """Comment timestamps → per-second weight, each spread ±spread s (people round to the nearest moment)."""
    n = int(duration) + 1
    out = _per_second(n)
    for t, w in times:
        if not 0 <= t < n:
            continue
        for d in range(-spread, spread + 1):
            if 0 <= t + d < n:
                out[t + d] += w * (1 - abs(d) / (spread + 1))
    return [round(float(v), 2) for v in out] if out.any() else None


def reactions_track(video: Path, duration: float) -> list[float] | None:
    """Laughter / screams / cheering per second. Not wired yet (see the module docstring): None."""
    return None


def _smooth(a: np.ndarray, k: int = SMOOTH_S) -> np.ndarray:
    return np.convolve(a, np.ones(k) / k, mode="same") if len(a) >= k else a


def zscores(tracks: dict[str, list | None]) -> dict[str, np.ndarray]:
    """Present tracks → smoothed z-scores (each against its own video)."""
    out = {}
    for name, v in tracks.items():
        if name not in WEIGHTS or not v:
            continue
        a = _smooth(np.asarray(v, dtype=float))
        out[name] = (a - a.mean()) / (a.std() + 1e-6)
    return out


def fused(z: dict[str, np.ndarray], weights: dict[str, float] | None = None) -> np.ndarray:
    """Weighted mean of the present z-scores, per second."""
    weights = weights or WEIGHTS
    if not z:
        return np.zeros(1)
    n = max(len(a) for a in z.values())
    total, wsum = np.zeros(n), 0.0
    for name, a in z.items():
        w = weights.get(name, 0.0)
        total[:len(a)] += w * a
        wsum += w
    return total / (wsum or 1.0)


def tag(z: dict[str, np.ndarray], a: float, b: float) -> str:
    """Compact label of what stands out in [a, b): "chat↑↑ volume↑" (↑ ≥ 1 sd above the video, ↑↑ ≥ 2)."""
    parts = []
    for name, arr in z.items():
        part = arr[int(a):max(int(a) + 1, int(np.ceil(b)))]
        if not len(part):
            continue
        top = float(part.max())
        if top >= 2:
            parts.append((top, f"{NAMES[name]}↑↑"))
        elif top >= 1:
            parts.append((top, f"{NAMES[name]}↑"))
    return " ".join(p for _, p in sorted(parts, reverse=True))


def window_bonus(z: dict[str, np.ndarray], a: float, b: float) -> float:
    """How far the fused signals stand above the video inside [a, b) (mean, ≥ 0)."""
    f = fused(z)
    part = f[int(a):max(int(a) + 1, int(np.ceil(b)))]
    return round(max(0.0, float(part.mean())) if len(part) else 0.0, 2)


def peaks(z: dict[str, np.ndarray], n: int = 10, min_gap: float = 60.0, lo: float = 0.0, hi: float | None = None,
          min_z: float = 1.5) -> list[int]:
    """Up to n seconds where the fused signals peak (≥ min_z), min_gap apart, inside [lo, hi)."""
    f = fused(z)
    hi = len(f) if hi is None else min(len(f), int(hi))
    out: list[int] = []
    for t in np.argsort(f)[::-1]:
        if len(out) >= n or f[t] < min_z:
            break
        if not lo <= t < hi or any(abs(int(t) - p) < min_gap for p in out):
            continue
        out.append(int(t))
    return sorted(out)


def scene_cuts(motion: list[float] | None, thr: float = 0.3) -> list[float]:
    """Seconds with a scene change (local peak of highlights' motion score above thr)."""
    if not motion:
        return []
    m = np.asarray(motion, dtype=float)
    return [float(i) for i in range(len(m)) if m[i] >= thr and m[i] >= m[max(0, i - 1)] and m[i] >= m[min(len(m) - 1, i + 1)]]


def waveform(video: Path, step: float = 0.1) -> list[int]:
    """Peak level per `step` s of the sound, 0–255 against the loudest peak of the file (the editor's Áudio lane).
    Read as a stream: a 3 h live never sits whole in memory."""
    block = max(1, int(WAVE_SR * step))
    p = subprocess.Popen([settings.ffmpeg_bin, "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(WAVE_SR),
                          "-f", "s16le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    peaks_: list[float] = []
    rest = b""
    try:
        while True:
            chunk = p.stdout.read(block * 2 * 600)
            if not chunk:
                break
            buf = rest + chunk
            k = len(buf) // (block * 2)
            if k:
                a = np.frombuffer(buf[:k * block * 2], dtype=np.int16).reshape(k, block)
                peaks_.extend(np.abs(a.astype(np.int32)).max(axis=1).tolist())
            rest = buf[k * block * 2:]
        if len(rest) >= 2:
            a = np.frombuffer(rest[:len(rest) // 2 * 2], dtype=np.int16)
            peaks_.append(float(np.abs(a.astype(np.int32)).max()))
    finally:
        p.stdout.close()
        p.wait()
    top = max(peaks_) if peaks_ else 0
    if not top:
        return [0] * len(peaks_)
    return [int(min(255, round(v / top * 255))) for v in peaks_]


def for_editor(tracks: dict[str, list | None]) -> dict[str, list[float]]:
    """Present tracks scaled 0–1 (min–max) for the editor's lanes."""
    out = {}
    for name, v in tracks.items():
        if not v:
            continue
        a = np.asarray(v, dtype=float)
        lo, hi = float(a.min()), float(a.max())
        out[name] = [round(float(x), 3) for x in ((a - lo) / (hi - lo) if hi > lo else np.zeros(len(a)))]
    return out
