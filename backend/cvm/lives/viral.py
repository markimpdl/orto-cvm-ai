"""Which moments of a recording can go viral as Shorts.

Current method (spec docs/superpowers/specs/2026-11-25-editor-de-cortes-design.md, Parte 1), `detect`:
the whole video's transcript (cvm/lives/signals.transcript, cached) in 30 s rows tagged with the per-second signals
(volume, motion, chat, YouTube replay, timestamped comments), sent to Claude (subscription, light model, no tools) in
~12 min chunks with the signal peaks it must look at; Claude proposes moments with their own start/end and five 0–10
scores; edges snap to sentence bounds / scene cuts; final score = weighted criteria + a small signal bonus; a second
light call reorders the best RERANK_N; the best non-overlapping ones are marked `suggested`. Without a transcript or
with Claude and Codex at their limit it falls back to the loudness rule ("volume"). The cut editor shows every
proposal; `pick_moments` (automatic path, Channel.prefs.auto_approve_cuts) takes the suggested ones.

Older window method, still used by tests and kept for reference: candidates → transcribe_windows → score → choose.
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
    items ="\n".join(f"{i}. {w['start']:.0f}–{w['end']:.0f} s"
                      + (f" [chat: {w['chat_rate']:.0f} msgs/min]" if w.get("chat_rate") is not None else "")
                      + f": {w.get('text', '')[:600]}" for i, w in enumerate(windows))
    ex = ("Formatos que estão viralizando agora neste tema (referência): " + "; ".join(examples[:8]) + "\n") if examples else ""
    prompt = PROMPT.format(title=title, who="do próprio canal" if own else "de outro criador", items=items, examples=ex)
    res = _ask(prompt, None if chosen else run)
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


def _ask(prompt: str, run=None, timeout: int = 600) -> dict:
    """One light call with no tools at all (the transcript may be someone else's words). run=None: the chosen AI
    (at Claude's limit it already tries Codex itself, else ClaudeLimit); a given run (tests, callers) that hits
    Claude's limit goes to Codex."""
    from cvm import claude_limit
    from cvm.grow.viral_search import DENY, mcp_config_empty

    chosen = run is None
    if chosen:
        from cvm.scripting.agents import light

        run = light("empty")
    try:
        return run(prompt, timeout=timeout, step="viral", mcp_config=mcp_config_empty(), disallowed_tools=DENY)
    except claude_limit.ClaudeLimit:
        if chosen:
            raise  # neither Claude nor Codex can take it now: loudness rule
        return _codex(prompt)  # raises ClaudeLimit too when Codex can't


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


# --- whole-transcript detection (spec docs/superpowers/specs/2026-11-25-editor-de-cortes-design.md, Parte 1) ---
# The old method only showed Claude 12 windows picked by loudness/motion, with fixed edges. Now: the whole video's
# transcript (cvm/lives/signals.transcript) in 30 s lines tagged with the signals, sent in ~12 min chunks; Claude
# proposes moments with its own start/end and scores five criteria; a second light call compares the best ~15.

WEIGHTS = {"hook": 0.30, "payoff": 0.20, "emotion": 0.20, "standalone": 0.20, "trend": 0.10}
SIGNAL_MAX = 1.0  # the signals add at most this to the 0–10 score (the transcript judgement leads)
ROW_S = 30.0
CHUNK_S = 12 * 60.0
OVERLAP_S = 90.0
MIN_LEN, MAX_LEN = 5.0, 180.0  # a proposal outside this is a parsing accident (15–60 s is only warned in the editor)
SNAP_S = 4.0  # how far an edge may move to a sentence bound / scene cut
MAX_PROPOSALS = 20
RERANK_N = 15
PEAKS_PER_CHUNK = 4

PROPOSE = """Você escolhe trechos de um vídeo longo para virar Shorts virais (YouTube Shorts, Reels, TikTok).
Vídeo: "{title}" ({who}). Este bloco vai de {lo} s a {hi} s do vídeo.
Cada linha abaixo cobre ~30 s: [início–fim em segundos] (sinais) e a fala transcrita. Sinais: volume, movimento,
chat (o público da live reagindo), replay (trecho mais repetido no YouTube), comentários (gente citando aquele minuto);
↑ = acima do normal deste vídeo, ↑↑ = muito acima.
{peaks}{taste}{examples}
Proponha os momentos que podem viralizar SOZINHOS como Short, cada um com o SEU começo e fim, em segundos do vídeo:
comece na preparação (o que deixa curioso) e termine na conclusão ou na reação, sem cortar frase no meio. Em geral
15 a 60 s. No máximo 6 por bloco; nenhum se o bloco for fraco. Momentos de menu, carregamento ou só andando não servem.
Notas de 0 a 10 para cada um:
- hook: o primeiro segundo prende?
- payoff: tem desenvolvimento e conclusão (virada, punchline, reação forte)?
- emotion: emoção ou valor (humor, tensão, surpresa, informação útil)?
- standalone: dá para entender sem ter visto o resto do vídeo?
- trend: combina com o que está em alta e com o tema do canal?
Seja exigente: 7+ só para o que você postaria.

Linhas:
{rows}

Responda SÓ com um array JSON, sem texto antes ou depois:
[{{"start": <s>, "end": <s>, "hook": "<texto da tela no 1º segundo, até 6 palavras>", "why": "<motivo em uma linha>", "scores": {{"hook": <0-10>, "payoff": <0-10>, "emotion": <0-10>, "standalone": <0-10>, "trend": <0-10>}}}}]"""

RERANK = """Abaixo estão os melhores trechos propostos para virar Shorts do vídeo "{title}". Compare uns com os outros
e ordene do que tem MAIS chance de viralizar sozinho para o que tem menos.

{items}

Responda SÓ com um array JSON com os ids na ordem, do melhor para o pior, por exemplo [3, 1, 2]."""


def _mmss(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def rows(lines: list[dict], duration: float, tracks: dict | None) -> list[dict]:
    """30 s rows [{s, e, text, tag}] of the transcript (silent rows kept: a scream or a chat burst can matter)."""
    from cvm.lives import signals

    z = signals.zscores(tracks or {})
    out = []
    t = 0.0
    end = max(float(duration or 0), max((ln["e"] for ln in lines), default=0.0))
    while t < end:
        b = min(end, t + ROW_S)
        text = " ".join(ln["text"] for ln in lines if t <= ln["s"] < b).strip()
        out.append({"s": t, "e": b, "text": text, "tag": signals.tag(z, t, b)})
        t = b
    return out


def chunk_rows(rs: list[dict], size: float = CHUNK_S, overlap: float = OVERLAP_S) -> list[tuple[float, float, list[dict]]]:
    """(lo, hi, rows) blocks of ~size s that overlap by `overlap`, so a moment on a border is seen whole once."""
    if not rs:
        return []
    end = rs[-1]["e"]
    out = []
    lo = 0.0
    while True:
        hi = min(end, lo + size)
        part = [r for r in rs if r["e"] > lo and r["s"] < hi]
        if part:
            out.append((lo, hi, part))
        if hi >= end:
            break
        lo = hi - overlap
    return out


def _num(v) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def parse_proposals(text: str, lo: float, hi: float, duration: float) -> list[dict]:
    """Claude's answer → [{start, end, hook, why, scores}] inside the chunk (± ROW_S), MIN_LEN–MAX_LEN long."""
    out = []
    for row in _json_array(text):
        if not isinstance(row, dict):
            continue
        a, b = _num(row.get("start")), _num(row.get("end"))
        if a is None or b is None or not MIN_LEN <= b - a <= MAX_LEN:
            continue
        if a < max(0.0, lo - ROW_S) or b > hi + ROW_S or (duration and b > duration + 1):
            continue
        raw = row.get("scores") if isinstance(row.get("scores"), dict) else {}
        scores = {k: max(0, min(10, int(round(v)))) for k in WEIGHTS if (v := _num(raw.get(k))) is not None}
        out.append({"start": round(a, 1), "end": round(min(b, duration or b), 1), "hook": str(row.get("hook") or "")[:80],
                    "why": str(row.get("why") or "")[:200], "scores": scores})
    return out


def _nearest(points: list[float], t: float, within: float) -> float | None:
    if not points:
        return None
    c = min(points, key=lambda c: abs(c - t))
    return c if abs(c - t) <= within else None


def snap(p: dict, lines: list[dict], cuts: list[float], duration: float) -> dict:
    """Start at the beginning of the sentence it falls in (else the nearest scene cut), end at the end of its
    sentence (else a scene cut), each edge moving at most SNAP_S."""
    a, b = p["start"], p["end"]
    sa = [ln["s"] for ln in lines if ln["s"] <= a + 0.5 and a - ln["s"] <= SNAP_S and ln["e"] > a - 0.5]
    if sa:
        a = max(sa)
    elif (c := _nearest(cuts, a, SNAP_S / 2)) is not None:
        a = c
    eb = [ln["e"] for ln in lines if ln["s"] < b and ln["e"] >= b - 0.5 and ln["e"] - b <= SNAP_S]
    if eb:
        b = min(eb)
    elif (c := _nearest(cuts, b, SNAP_S / 2)) is not None and c > a:
        b = c
    return {**p, "start": round(max(0.0, a), 1), "end": round(min(b, duration) if duration else b, 1)}


def final_score(scores: dict, bonus: float) -> float:
    """Weighted 0–10 from the criteria (a missing one counts 0) + up to SIGNAL_MAX from the signals."""
    base = sum(w * float(scores.get(k, 0)) for k, w in WEIGHTS.items())
    return round(min(10.0, base + min(SIGNAL_MAX, max(0.0, bonus) * 0.5)), 1)


def _overlap(p: dict, q: dict) -> float:
    inter = min(p["end"], q["end"]) - max(p["start"], q["start"])
    return max(0.0, inter) / max(1e-6, min(p["end"] - p["start"], q["end"] - q["start"]))


def merge(props: list[dict]) -> list[dict]:
    """Best first; a proposal mostly inside a better one (two chunks, or two takes of one moment) is dropped."""
    out: list[dict] = []
    for p in sorted(props, key=lambda p: -p["score"]):
        if any(_overlap(p, q) > 0.5 for q in out):
            continue
        out.append(p)
    return out


def rerank(props: list[dict], lines: list[dict], title: str, run=None) -> list[dict]:
    """The best RERANK_N compared against each other by a second light call; the rest keep their score order.
    Any failure keeps the score order."""
    head, tail = props[:RERANK_N], props[RERANK_N:]
    if len(head) > 1:
        items = "\n".join(
            f"id {i}: {_mmss(p['start'])}–{_mmss(p['end'])} | gancho: {p.get('hook', '')} | motivo: {p.get('why', '')} | "
            f"fala: {' '.join(ln['text'] for ln in lines if p['start'] <= ln['s'] < p['end'])[:300]}"
            for i, p in enumerate(head))
        try:
            ids = [i for i in _json_array(str(_ask(RERANK.format(title=title, items=items), run).get("result", "")))
                   if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(head)]
            order = list(dict.fromkeys(ids)) + [i for i in range(len(head)) if i not in ids]
            head = [head[i] for i in order]
        except Exception as e:  # noqa: BLE001 — incl. ClaudeLimit: the score order stands
            log.warning("viral rerank unavailable: %s", e)
    return [{**p, "rank": n} for n, p in enumerate(head + tail, 1)]


def mark_suggested(props: list[dict], limit: int, min_score: float = MIN_SCORE, min_count: int = 1) -> list[dict]:
    """In the given order (best first), mark as suggested up to `limit` non-overlapping proposals scoring
    ≥ min_score; at least min_count whatever the score."""
    chosen: list[dict] = []
    out = []
    for p in props:
        ok = (len(chosen) < limit and (p["score"] >= min_score or len(chosen) < min_count)
              and not any(p["start"] < c["end"] and p["end"] > c["start"] for c in chosen))
        if ok:
            chosen.append(p)
        out.append({**p, "suggested": ok})
    return out


def propose(lines: list[dict], tracks: dict | None, duration: float, title: str, own: bool,
            examples: list[str] | None = None, taste: list[str] | None = None, run=None,
            limit: int | None = None) -> list[dict]:
    """Proposals best first, each {start, end, hook, why, scores, score, viral, signal, tags, rank, suggested}.
    Raises the runner's error (ClaudeLimit…) when no chunk could be judged."""
    from cvm.lives import signals

    tracks = tracks or {}
    z = signals.zscores(tracks)
    cuts = signals.scene_cuts(tracks.get("motion"))
    who = "do próprio canal" if own else "de outro criador"
    tx = ("Gosto do dono deste canal (decisões dele em cortes anteriores; siga esse gosto):\n- "
          + "\n- ".join(taste[:12]) + "\n") if taste else ""
    ex = ("Formatos que estão viralizando agora neste tema (referência): " + "; ".join(examples[:8]) + "\n") if examples else ""
    from cvm.lives import progress

    found: list[dict] = []
    errors: list[Exception] = []
    chunks = chunk_rows(rows(lines, duration, tracks))
    for n, (lo, hi, part) in enumerate(chunks, 1):
        progress.report(progress.AI, f"IA analisando bloco {n} de {len(chunks)}", pct=100.0 * (n - 1) / len(chunks),
                        done=n - 1, total=len(chunks))
        pk = signals.peaks(z, PEAKS_PER_CHUNK, lo=lo, hi=hi) if z else []
        peak_txt = ("Picos de sinal que você TEM de olhar (proponha o momento ou diga no motivo por que não): "
                    + ", ".join(f"{p} s" for p in pk) + "\n") if pk else ""
        listing = "\n".join(f"[{r['s']:.0f}–{r['e']:.0f}]" + (f" ({r['tag']})" if r["tag"] else "") + " "
                            + (r["text"][:500] or "[sem fala]") for r in part)
        prompt = PROPOSE.format(title=title, who=who, lo=int(lo), hi=int(hi), peaks=peak_txt, taste=tx, examples=ex,
                                rows=listing)
        try:
            res = _ask(prompt, run)
        except Exception as e:  # noqa: BLE001 — one chunk failing must not lose the others
            errors.append(e)
            log.warning("viral chunk %s–%s failed: %s", lo, hi, e)
            continue
        found += parse_proposals(str(res.get("result", "")), lo, hi, duration)
    if not found and errors:
        raise errors[0]
    out = []
    for p in found:
        p = snap(p, lines, cuts, duration)
        bonus = signals.window_bonus(z, p["start"], p["end"]) if z else 0.0
        sc = final_score(p["scores"], bonus)
        out.append({**p, "score": sc, "viral": int(round(sc)), "signal": bonus,
                    "tags": signals.tag(z, p["start"], p["end"]) if z else ""})
    progress.report(progress.AI, "IA comparando os melhores cortes", pct=99.0, done=len(chunks), total=len(chunks))
    out = rerank(merge(out)[:MAX_PROPOSALS], lines, title, run)
    return mark_suggested(out, limit or shorts_limit(duration))


def volume_proposals(meta: dict, duration: float, min_count: int = 0) -> list[dict]:
    """The loudness/motion (and chat) rule in the proposals' shape, best first (Claude or whisper unavailable)."""
    src = meta.get("moments") or meta.get("segments") or []
    strong = {(m["start"], m["end"]) for m in highlights.top_moments(src, None)}
    out = []
    for m in sorted(highlights.top_moments(src, MAX_PROPOSALS), key=lambda m: -(m.get("score") or 0)):
        s = float(m.get("score") or 0)
        sc = round(min(10.0, max(0.0, 5 + 2 * s)), 1)
        why = "volume e movimento acima do resto do vídeo" + (" e chat agitado" if m.get("chat_rate") else "")
        out.append({**m, "score": sc, "viral": int(round(sc)), "hook": "", "why": why, "scores": {}, "signal": s,
                    "tags": "", "suggested": (m["start"], m["end"]) in strong})
    if min_count and sum(p["suggested"] for p in out) < min_count:
        for p in out[:min_count]:
            p["suggested"] = True
    return [{**p, "rank": n} for n, p in enumerate(out, 1)]


def detect(video: Path, meta: dict, title: str, own: bool, language: str, duration: float,
           tracks: dict | None = None, examples: list[str] | None = None, taste: list[str] | None = None,
           run=None, transcribe=None, min_count: int = 0) -> tuple[list[dict], str]:
    """(proposals best first, "viral" | "volume"): the whole-transcript method, else the loudness rule (no
    transcript, Claude and Codex at their limit, any error)."""
    from cvm.lives import signals

    try:
        lines = signals.transcript(video, language, transcribe=transcribe)
        if not lines:
            raise RuntimeError("sem fala transcrita")
        dur = float(duration or 0) or max(ln["e"] for ln in lines)
        props = propose(lines, tracks if tracks is not None else meta.get("tracks"), dur, title, own,
                        examples=examples, taste=taste, run=run)
        if min_count and sum(p["suggested"] for p in props) < min_count:
            props = mark_suggested(props, shorts_limit(dur), min_count=min_count)
        return props, "viral"
    except Exception as e:  # noqa: BLE001 — incl. claude_limit.ClaudeLimit
        log.warning("viral detection unavailable, using loudness: %s", e)
    return volume_proposals(meta, duration, min_count), "volume"


def pick_moments(video: Path, meta: dict, title: str, own: bool, language: str,
                 examples: list[str] | None = None, min_count: int = 0,
                 duration: float = 0.0, taste: list[str] | None = None) -> tuple[list[dict], str]:
    """(moments for Shorts in time order, "viral" | "volume") for the automatic path (Channel.prefs
    auto_approve_cuts): detect(), then the suggested ones (score ≥ MIN_SCORE, no overlap, at most shorts_limit).
    min_count: at least this many Shorts whatever the scores (the user's own pick of a video)."""
    props, method = detect(video, meta, title, own, language, duration, examples=examples, taste=taste)
    if method == "viral":
        marked = mark_suggested(props, shorts_limit(duration), min_count=min_count)
        return sorted([p for p in marked if p["suggested"]], key=lambda p: p["start"]), "viral"
    src = meta.get("moments") or meta.get("segments") or []
    moments = highlights.top_moments(src, None)
    if len(moments) < min_count:
        moments = highlights.top_moments(src, min_count, min_s=5.0)
    return moments, "volume"


