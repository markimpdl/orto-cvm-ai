"""Which moments of a recording can go viral as Shorts (spec "Adendo": análise de viralização).

candidates → the strongest windows by loudness/motion (cvm/visuals/highlights.py); transcribe_windows → what is said
in each (local whisper, inside gpu.section so it never runs beside a big model); score → Claude (subscription, light
model) gives each a 0–10 viral score with the on-screen hook and a one-line reason; choose → score ≥ 7, no overlap,
at most highlights.MAX_SHORTS. pick_moments ties it together and falls back to the loudness rule ("volume") when
Claude or whisper is not available.
"""

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

from cvm.config import settings
from cvm.visuals import highlights

log = logging.getLogger(__name__)
MIN_SCORE = 7
LIMIT = 12
MAX_LIMIT = 40  # a 24 h marathon is worth many more candidates than a 20 min video


def candidate_limit(duration: float) -> int:
    """How many windows to transcribe and score: 12 for a short video, +2 per hour of live, at most MAX_LIMIT."""
    return int(min(MAX_LIMIT, LIMIT + 2 * max(0.0, duration / 3600 - 1)))


def shorts_limit(duration: float) -> int:
    """At most highlights.MAX_SHORTS Shorts per video, +1 per 2 hours of a long live, at most 20."""
    return int(min(20, highlights.MAX_SHORTS + max(0.0, duration / 3600 - 2) // 2))

PROMPT = """Você escolhe trechos de vídeo para virar Shorts virais (YouTube Shorts, Reels, TikTok) de um canal brasileiro de games.
Vídeo: "{title}" ({who}).
Abaixo, trechos candidatos (início–fim em segundos) com a fala transcrita. Dê a cada um uma nota de 0 a 10 de potencial
de viralizar SOZINHO como Short, pensando em: gancho no primeiro segundo, payoff no fim, surpresa, humor, tensão ou
conflito, reação forte, e se dá para entender sem contexto. Momentos calmos, de menu, de carregamento ou só andando
valem pouco. Quando aparece [chat: N msgs/min], é quanto o público da live reagiu ali: chat explodindo costuma marcar
o momento mais forte. Seja exigente: só dê 7+ ao que você postaria.
{examples}
Trechos:
{items}

Responda SÓ com um array JSON, sem texto antes ou depois:
[{{"i": <número do trecho>, "score": <0-10>, "hook": "<texto curto e forte para a tela no 1º segundo, até 6 palavras>", "why": "<motivo em uma linha>"}}]"""


def candidates(meta: dict, limit: int = LIMIT) -> list[dict]:
    """The `limit` strongest 7–45 s windows of the file (its scored moments, else its kept segments), in time order."""
    return highlights.top_moments(meta.get("moments") or meta.get("segments") or [], limit, min_s=7.0, max_s=45.0)


def transcribe_windows(video: Path, windows: list[dict], language: str, transcribe=None) -> list[dict]:
    """Each window + "text" (what is said; "[sem fala]" when nothing)."""
    from cvm import gpu

    if transcribe is None:
        from cvm.render.captions import transcribe
    out = []
    # whisper on short windows is small (~1.5 GB): the light slot runs it beside a long presenter step instead of
    # waiting hours behind it (it falls back to the heavy lock when memory is short)
    with tempfile.TemporaryDirectory() as td, gpu.light():
        for n, w in enumerate(windows):
            wav = Path(td) / f"w{n}.wav"
            subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-ss", f"{w['start']:.2f}", "-t",
                            f"{w['end'] - w['start']:.2f}", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                           capture_output=True)
            text = ""
            if wav.exists() and wav.stat().st_size > 1000:
                text = (transcribe(wav, language) or {}).get("text", "").strip()
            out.append({**w, "text": text or "[sem fala]"})
    gpu.free_whisper()
    return out


def _json_array(text: str) -> list:
    text = re.sub(r"```(?:json)?", "", text or "")
    a, b = text.find("["), text.rfind("]")
    if a < 0 or b <= a:
        return []
    try:
        data = json.loads(text[a:b + 1])
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def score(windows: list[dict], title: str, own: bool, run=None, examples: list[str] | None = None) -> list[dict]:
    """Windows that got a valid score, each + {"viral": 0–10, "hook", "why"}, in time order."""
    chosen = run is None
    if chosen:
        from cvm.scripting.agents import light

        run = light("empty")  # the chosen AI; at Claude's limit it already tries Codex itself
    items = "\n".join(f"{i}. {w['start']:.0f}–{w['end']:.0f} s"
                      + (f" [chat: {w['chat_rate']:.0f} msgs/min]" if w.get("chat_rate") is not None else "")
                      + f": {w.get('text', '')[:600]}" for i, w in enumerate(windows))
    ex = ("Formatos que estão viralizando agora neste tema (referência): " + "; ".join(examples[:8]) + "\n") if examples else ""
    prompt = PROMPT.format(title=title, who="do próprio canal" if own else "de outro criador", items=items, examples=ex)
    from cvm.grow.viral_search import DENY, mcp_config_empty

    # the transcript may be someone else's words: this run gets no tools at all
    from cvm import claude_limit

    try:
        res = run(prompt, timeout=600, step="viral", mcp_config=mcp_config_empty(), disallowed_tools=DENY)
    except claude_limit.ClaudeLimit:
        if chosen:
            raise  # neither Claude nor Codex can take it now: loudness rule (pick_moments)
        res = _codex(prompt)  # Claude at its limit: Codex scores (raises ClaudeLimit too when it can't)
    out = []
    for row in _json_array(str(res.get("result", ""))):
        if not isinstance(row, dict):
            continue
        i, sc = row.get("i"), row.get("score")
        if (isinstance(i, bool) or isinstance(sc, bool) or not isinstance(i, int) or not 0 <= i < len(windows)
                or not isinstance(sc, int | float) or not 0 <= sc <= 10):
            continue
        out.append({**windows[i], "viral": int(round(sc)), "hook": str(row.get("hook") or "")[:80],
                    "why": str(row.get("why") or "")[:200]})
    return sorted(out, key=lambda r: r["start"])


def _codex(prompt: str) -> dict:
    """The same scoring prompt through Codex (ChatGPT subscription) when Claude is at its limit."""
    from datetime import UTC, datetime, timedelta

    from cvm import claude_limit
    from cvm.scripting import codex_runner

    if not codex_runner.available():
        raise claude_limit.ClaudeLimit(datetime.now(UTC) + timedelta(hours=1), "Codex indisponível")
    return codex_runner.run_codex(prompt, timeout=600, mcp="empty")  # someone else's words: no tools at all


def choose(rows: list[dict], min_score: int = MIN_SCORE, limit: int = highlights.MAX_SHORTS, min_count: int = 0) -> list[dict]:
    """Score ≥ min_score, no overlap, at most `limit`; min_count keeps the best ones even below the bar (a link the
    user chose himself always gives at least one Short)."""
    chosen: list[dict] = []
    for r in sorted(rows, key=lambda r: -r["viral"]):
        if len(chosen) >= limit or (r["viral"] < min_score and len(chosen) >= min_count):
            break
        if any(r["start"] < c["end"] and r["end"] > c["start"] for c in chosen):
            continue
        chosen.append(r)
    return sorted(chosen, key=lambda r: r["start"])


def pick_moments(video: Path, meta: dict, title: str, own: bool, language: str,
                 examples: list[str] | None = None, min_count: int = 0,
                 duration: float = 0.0) -> tuple[list[dict], str]:
    """(moments for Shorts, "viral" | "volume"). Falls back to the loudness rule on any Claude/whisper failure.
    min_count: at least this many Shorts whatever the scores (the user's own pick of a video)."""
    src = meta.get("moments") or meta.get("segments") or []
    try:
        rows = score(transcribe_windows(video, candidates(meta, candidate_limit(duration)), language), title, own,
                     examples=examples)
        if rows:
            return choose(rows, min_count=min_count, limit=shorts_limit(duration)), "viral"
    except Exception as e:  # noqa: BLE001 — incl. claude_limit.ClaudeLimit: the package is still made
        log.warning("viral scoring unavailable, using loudness: %s", e)
    moments = highlights.top_moments(src, None)
    if len(moments) < min_count:
        moments = highlights.top_moments(src, min_count, min_s=5.0)
    return moments, "volume"
