"""Visuals of the editorial (news-desk) long video: one real picture per `video`/`foto` scene, a screenshot per
`prova`, a background per section, all written to `<project>/<lang>/editorial/plan.json` for the renderer.

- `video`/`foto`: the files the user attached to the project first, when CLIP (or the file's description) matches the
  scene (unconditionally when the script asks for "meus" or the project's media mode is "mine"); then the order of
  `busca.preferir`: official/CC YouTube clips through `footage.find_footage` (its official half obeys
  `Channel.allow_official_clips`; skipped for scenes longer than an 8 s clip can cover), article lead images only with
  `Channel.allow_article_images`, free banks. Every third-party video is CLIP-checked against the scene's search.
  YouTube searches/metadata are cached in `editorial/cache/` (a curation send-back does not search again), proofs too.
  For a clip, windows of the scene's length (+0.5 s) are cut from it and the Gemma judge looks at a three-frame strip
  of each (third-party logo/watermark, a cut inside, stray on-screen text): the first clean window wins. A clip
  shorter than the scene may be slowed down to 1.7×; beyond that, the next clip. Windows holding ≥ 0.8 s of black
  (blackdetect, a trailer fade-in) are never offered. No judge installed → the first
  window (the clip already passed CLIP in its search).
- No real material → the scene becomes a card (`pergunta` with the narration's words, or `numero` when it has a
  `valor`), marked with `fallback`. Never an AI image here.
- `prova`: `proof.capture` on the scene's page, then the script's other sources; none found (or article images off)
  → a card that keeps its `fonte`.
- Section background: a frame of the section's first real picture, blurred and darkened (`bg/<section>.jpg`).
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from sqlmodel import Session, select

from cvm import progress
from cvm.models import Asset, Channel, Project
from cvm.paths import project_dir
from cvm.render.editorial import timeline
from cvm.render.editorial.timeline import EditorialError
from cvm.visuals import footage, judge, proof
from cvm.visuals import select as vselect

log = logging.getLogger(__name__)

MEDIA = ("video", "foto")
SOURCES = ("meus", "oficial", "noticia", "banco")
PAD = 0.5  # a window is this much longer than its scene (the renderer trims)
MAX_SLOW = 1.7  # a short clip may be slowed down this much, no more
WINDOWS = 4  # candidate windows per clip
ROUNDS = 3  # clips (or pictures) tried per scene before it becomes a card
EDGE = 0.1  # never a window touching the clip's very last frame
CARD_CHARS = 60  # pergunta cards hold at most this much text
OTHER_PROOF_SOURCES = 2  # script sources tried after the scene's own page
WINDOW_RULE = "editorial_window"
DIRTY = ("logo", "watermark", "marca d", "cut", "corte", "caption", "subtitle", "legenda")
JUDGE_CHUNK = 40  # windows per judge call
NO_JUDGE = "juiz indisponível"  # judge.judge's verdict when the model run failed
OWN_DESC_MIN = 0.5  # share of the search words found in an own file's description
BLACK_MIN = 0.8  # a window holding this much black in a row is never used (trailer fade-ins/outs; 728 failed QA on it)
LONG_FOR_YOUTUBE = footage.MAX_CLIP_S * MAX_SLOW - PAD  # longer scenes: an 8 s YouTube excerpt can't cover them


def _duration(path) -> float:
    from cvm.media import duration

    return duration(path)


def _strip(video: Path, a: float, b: float, out: Path) -> Path | None:
    """Start, middle and end frames of the window [a, b] side by side (as `judge.strip` does for a whole clip)."""
    from cvm.media import run_ffmpeg

    ts = (a + min(0.2, (b - a) / 4), (a + b) / 2, max(a, b - 0.3))
    ins = [x for t in ts for x in ("-ss", f"{t:.2f}", "-i", str(video))]
    try:
        run_ffmpeg(*ins, "-filter_complex", "[0]scale=-2:512[a];[1]scale=-2:512[b];[2]scale=-2:512[c];[a][b][c]hstack=3",
                   "-frames:v", "1", str(out))
    except Exception as e:  # noqa: BLE001
        log.warning("window strip failed %s@%.1f: %s", video, a, e)
        return None
    return out if out.exists() else None


def _background(src: str, kind: str, at: float, out: Path) -> Path | None:
    """One frame of the section's material, 1080p, blurred and darkened."""
    from cvm.media import run_ffmpeg

    out.parent.mkdir(parents=True, exist_ok=True)
    seek = ["-ss", f"{at:.2f}"] if kind == "video" else []
    try:
        run_ffmpeg(*seek, "-i", src, "-frames:v", "1", "-vf",
                   "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,gblur=sigma=28,"
                   "eq=brightness=-0.22:saturation=0.8", "-q:v", "3", str(out))
    except Exception as e:  # noqa: BLE001
        log.warning("section background failed %s: %s", src, e)
        return None
    return out if out.exists() else None


def hook_end(session: Session, project: Project, language: str) -> float | None:
    """End of the narration of the script's `intro_end` scene (the bumper plays right after it); None = no bumper."""
    from cvm.voice.narrate import latest_script, narration_asset

    script = latest_script(session, project.id, language)
    narr = narration_asset(session, project.id, language)
    ie = (script.content or {}).get("intro_end") if script else None
    if ie is None or not narr:
        return None
    return next((float(t["end"]) for t in (narr.meta or {}).get("scenes", []) if t.get("index") == ie), None)


def bumper_seconds(session: Session, project: Project) -> float:
    """Length of the channel's short bumper (≤ 3 s; an opening tune up to 5 s); 0 when it has none."""
    from cvm.render import bumper

    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    if not bumper.wants_bumper(channel, "youtube", None, project):
        return 0.0
    music = bumper.opening_music(channel)
    return min(bumper.MUSIC_MAX_S, max(bumper.SHORT_S, _duration(music))) if music else bumper.SHORT_S


def needs_classic(session: Session, project: Project) -> bool:
    """Some render of the project is not an editorial long video (a Short, ar-AE…): the classic visuals set is
    still needed for it."""
    from cvm import editorial
    from cvm.render.compose import platforms_for

    return any(not editorial.wanted(session, project, lang, prof.removesuffix("_cuts"))
               for lang in project.languages or [] for prof in platforms_for(session, project))


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "secao"


def _host(url: str) -> str:
    return (urlparse(url).hostname or url).removeprefix("www.")


def _narration(scenes: list[dict], k: int, words: list[dict]) -> str:
    """The narration's words under scene k, cut to a card's length."""
    a = scenes[k]["_w"]
    b = next((s["_w"] for s in scenes[k + 1:] if s["tipo"] != "vinheta" and s["_w"] > a), len(words))
    text = " ".join(w["w"] for w in words[a:b]).strip()
    if len(text) > CARD_CHARS:
        text = text[:CARD_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text[:1].upper() + text[1:]


def _card(s: dict, reason: str, narration: str) -> None:
    s["tipo_pedido"] = s["tipo"]
    s["tipo"] = "numero" if s.get("valor") else "pergunta"
    s["fallback"] = reason
    if s["tipo"] == "pergunta" and not s.get("texto"):
        s["texto"] = narration
    log.info("editorial scene '%s' became a %s card: %s", s.get("em"), s["tipo"], reason)


class _Ctx:
    def __init__(self, session: Session, project: Project, channel: Channel | None, dest: Path, sources_urls: list[str]):
        self.session, self.project, self.channel, self.dest = session, project, channel, dest
        self.sources_urls = sources_urls
        self.used: set[str] = set()
        self.mine_used: list[int] = []
        self.taken: dict[str, list[tuple[float, float]]] = {}  # windows already in the video, per file
        self.mine = [a for a in session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "user_media")
                                             .order_by(Asset.id)).all()
                     if a.path and Path(a.path).exists()] if (project.media_mode or "mix") != "online" else []

    def free(self, file: str, w: tuple[float, float]) -> bool:
        return not any(w[0] < b and a < w[1] for a, b in self.taken.get(file, []))


def _order(s: dict) -> list[str]:
    pref = (s.get("busca") or {}).get("preferir") or "banco"
    return [x for x in [pref] + [x for x in SOURCES if x != pref] if x != "meus"]


def _terms(s: dict) -> list[str]:
    """What to search for: the script's terms, else the scene's text and its section headline (never the bare anchor)."""
    b = s.get("busca") or {}
    terms = [t for t in b.get("termos") or [] if t]
    if terms:
        return terms
    sec = s.get("secao") or [None, None]
    return [t for t in (s.get("texto"), sec[1] if len(sec) > 1 else None) if t] or [s.get("em") or ""]


def _old_scene(s: dict, k: int) -> dict:
    """The classic scene shape `footage.find_footage` / `select._broll` / `select._article_image` read."""
    b = s.get("busca") or {}
    return {"index": k, "target_seconds": footage.MAX_CLIP_S, "on_screen_text": s.get("texto"),
            "visual": {"type": "footage", "search_terms": _terms(s), "official_channels": b.get("official_channels") or [],
                       "source_url": None}}


def _clip_ok(path: str, query: str, kind: str) -> float | None | bool:
    """CLIP similarity of a picture to the search; False when it is clearly off-topic, None when CLIP is missing."""
    from cvm.visuals import clip_rank, sources

    try:
        if kind == "video":
            sim = footage.clip_match(Path(path), query)
            floor = footage.CLIP_MIN_FOOTAGE
        else:
            sim = clip_rank.similarities(query, [path])[0] if clip_rank.available() else None
            floor = sources.CLIP_MIN
    except Exception as e:  # noqa: BLE001
        log.warning("clip check failed %s: %s", path, e)
        return None
    if sim is not None and sim < floor:
        return False
    return sim


def _own_matches(a: Asset, s: dict) -> bool:
    """An attached file fits the scene: its description names the subject, or CLIP sees it."""
    from cvm.visuals import sources

    terms = _terms(s)
    desc = (a.meta or {}).get("description") or ""
    if desc and any(sources.relevance(t, {"text": desc}) >= OWN_DESC_MIN for t in terms):
        return True
    kind = "video" if (a.meta or {}).get("kind") == "video" else "photo"
    sim = _clip_ok(a.path, terms[0], kind)
    return sim is not None and sim is not False


def _own(a: Asset) -> dict:
    return {"kind": "video" if (a.meta or {}).get("kind") == "video" else "photo", "file": a.path,
            "credit": None, "source": "seu arquivo", "license": "próprio", "own": True}


def _materials(s: dict, k: int, ctx: _Ctx):
    """Real pictures for scene k, best source first, lazily (each next one is searched only when asked)."""
    old = _old_scene(s, k)
    query = _terms(s)[0]
    pref = (s.get("busca") or {}).get("preferir")
    if ctx.mine:  # files attached to this video on purpose: first, when they fit (always, when the script asks)
        anyway = pref == "meus" or ctx.project.media_mode == "mine"
        for a in sorted(ctx.mine, key=lambda a: ctx.mine_used.count(a.id)):
            if anyway or _own_matches(a, s):
                ctx.mine_used.append(a.id)
                yield _own(a)
    for src in _order(s):
        if src == "oficial" and s["tipo"] == "video":
            if s["end"] - s["start"] > LONG_FOR_YOUTUBE:
                log.info("editorial scene %d (%.1f s) is longer than a YouTube excerpt covers: banks/Commons", k,
                         s["end"] - s["start"])
                continue
            allow = bool(ctx.channel and ctx.channel.allow_official_clips)
            for _ in range(ROUNDS):
                got = footage.find_footage(old, ctx.dest, allow, ctx.used)
                if not got:
                    break
                m = {"kind": "video", "file": got["path"], "credit": got.get("credit"), "source": got.get("source"),
                     "license": got.get("license")}
                # YouTube excerpts were CLIP-checked by find_footage; its Commons videos were not
                if got.get("clip") is None and _clip_ok(m["file"], query, "video") is False:
                    log.info("editorial scene %d: %s off-topic for '%s'", k, m["source"], query)
                    continue
                yield m
        elif src == "noticia":
            got = vselect._article_image(old, ctx.sources_urls, ctx.dest, ctx.channel, ctx.used)
            if got:
                yield {"kind": "photo", "file": got["path"], "credit": got.get("credit"), "source": got.get("source"),
                       "license": got.get("license")}
        elif src == "banco":
            for _ in range(ROUNDS):
                got = vselect._broll(old, False, ctx.dest, ctx.used)
                if not got:
                    break
                m = {"kind": "video" if got.get("kind") == "video" else "photo", "file": got["path"],
                     "credit": got.get("credit") or got.get("source"), "source": got.get("page") or got.get("url"),
                     "license": got.get("license")}
                # bank videos were ranked by their poster frame only: the clip itself must show the subject
                if m["kind"] == "video" and _clip_ok(m["file"], got.get("query") or query, "video") is False:
                    log.info("editorial scene %d: bank clip %s off-topic", k, m["source"])
                    continue
                yield m


_BLACK: dict[tuple[str, float], list[tuple[float, float]]] = {}


def _black_spans(path: str) -> list[tuple[float, float]]:
    """Black stretches (start, end) of a clip, by ffmpeg blackdetect (the same detector as render QA), cached per
    file and mtime. A failed probe gives no spans (the window is then judged as before)."""
    import subprocess

    from cvm.media import _bin
    from cvm.config import settings

    try:
        key = (str(path), Path(path).stat().st_mtime)
    except OSError:
        return []
    if key not in _BLACK:
        try:
            r = subprocess.run([_bin(settings.ffmpeg_bin, "ffmpeg"), "-hide_banner", "-nostats", "-i", str(path),
                                "-vf", "blackdetect=d=0.3:pix_th=0.10", "-an", "-f", "null", "-"],
                               capture_output=True, text=True, timeout=300)
            log_ = r.stderr or ""
        except Exception as e:  # noqa: BLE001
            log.warning("blackdetect failed on %s: %s", path, e)
            log_ = ""
        _BLACK[key] = [(float(a), float(b)) for a, b in
                       re.findall(r"black_start:([\d.]+)\s+black_end:([\d.]+)", log_)]
    return _BLACK[key]


def clear_of_black(w: tuple[float, float], spans: list[tuple[float, float]]) -> bool:
    """True when the window [a, b] overlaps no black stretch by `BLACK_MIN` seconds or more."""
    a, b = w
    return all(min(b, e) - max(a, s) < BLACK_MIN for s, e in spans)


def windows(scene_s: float, clip_s: float) -> list[tuple[float, float]]:
    """Candidate windows (start, end) for a scene of `scene_s` seconds inside a clip of `clip_s` seconds."""
    need, usable = scene_s + PAD, clip_s - EDGE
    if usable >= need:
        n = max(1, min(WINDOWS, int(usable // need)))
        starts = [0.0] if n == 1 else [i * (usable - need) / (n - 1) for i in range(n)]
        return [(round(a, 2), round(a + need, 2)) for a in starts]
    if usable > 0 and usable * MAX_SLOW >= need:
        return [(0.0, round(usable, 2))]  # slowed down by the renderer (≤ 1.7×)
    return []


def _status(v: dict) -> str:
    """"clean" | "refused" | "unknown" (the judge did not really look: treated as no judge, never as clean)."""
    problems = [str(p).lower() for p in v.get("problems") or []]
    if v.get("clean") is None and (NO_JUDGE in problems or "clean" in v):
        return "unknown"
    if "clean" in v:
        return "clean" if v["clean"] else "refused"
    if NO_JUDGE in problems or v.get("score") is None:  # an older judge.py: unreadable answer
        return "unknown"
    ok = judge.verdict_ok(v) and not any(w in p for p in problems for w in DIRTY)
    return "clean" if ok else "refused"


def _judge(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(items), JUDGE_CHUNK):  # one model run per chunk: a failed run loses only its chunk
        out += judge.judge(items[i:i + JUDGE_CHUNK])
    return out


def _pick_media(scenes: list[dict], words: list[dict], ctx: _Ctx) -> None:
    todo = {k: _materials(s, k, ctx) for k, s in enumerate(scenes) if s["tipo"] in MEDIA}
    tries = dict.fromkeys(todo, 0)
    strips = ctx.dest / "windows"
    strips.mkdir(parents=True, exist_ok=True)
    total = len(todo)
    while todo:
        progress.report(total - len(todo), total, f"vídeos e fotos reais ({total - len(todo)}/{total} cenas)")
        asks: list[tuple[int, dict, tuple[float, float], Path]] = []
        for k in list(todo):
            s = scenes[k]
            if tries[k] >= ROUNDS or (m := next(todo[k], None)) is None:
                _card(s, "sem vídeo ou foto real do assunto", _narration(scenes, k, words))
                del todo[k]
                continue
            tries[k] += 1
            if m["kind"] == "photo":
                _accept(s, m, 0.0, None, judged=False)
                del todo[k]
                continue
            try:
                clip_s = _duration(m["file"])
            except Exception as e:  # noqa: BLE001
                log.warning("unreadable clip %s: %s", m["file"], e)
                continue
            black = _black_spans(m["file"])
            for i, w in enumerate(windows(s["end"] - s["start"], clip_s)):
                if ctx.free(m["file"], w) and clear_of_black(w, black):  # never the same stretch of a file twice in the video
                    asks.append((k, m, w, strips / f"s{k:03d}_{tries[k]}_{i}.jpg"))
        if not asks:
            continue
        status: dict[int, str] = dict.fromkeys(range(len(asks)), "unknown")
        if judge.ready():
            items, idx = [], []
            for n, (k, m, w, out) in enumerate(asks):
                if (p := _strip(Path(m["file"]), w[0], w[1], out)) is not None:
                    terms = ", ".join(_terms(scenes[k]))
                    items.append({"image": str(p), "context": f"{terms} | {_narration(scenes, k, words)}",
                                  "kind": "clip", "rule": WINDOW_RULE})
                    idx.append(n)
                else:
                    status[n] = "refused"  # no frames out of it: not a usable window
            for n, v in zip(idx, _judge(items)):
                status[n] = _status(v)
                if status[n] == "refused":
                    log.info("editorial scene %d: window %.1f–%.1f refused (%s)", asks[n][0], *asks[n][2],
                             v.get("problems"))
            if any(x == "unknown" for x in status.values()):
                log.warning("visual judge gave no answer for some windows: first window taken without the check")
        else:
            log.info("visual judge not installed: first window of each clip taken without the logo/cut check")
        for n, (k, m, w, _) in enumerate(asks):  # in order: each scene's first clean (or unchecked) free window
            if k in todo and status[n] != "refused" and ctx.free(m["file"], w):
                _accept(scenes[k], m, *w, judged=status[n] == "clean")
                ctx.taken.setdefault(m["file"], []).append(w)
                del todo[k]


def _accept(s: dict, m: dict, start: float, end: float | None, judged: bool) -> None:
    """The plan's media entry: `start`/`window_end` are seconds inside `file` (photos: 0 / None); the scene's own
    `start`/`end` stay its place on the video's timeline."""
    s["media"] = {"kind": m["kind"], "file": m["file"], "start": start, "window_end": end, "credit": m.get("credit"),
                  "source": m.get("source"), "license": m.get("license"), "own": bool(m.get("own")), "judged": judged}
    if not m.get("own") and not s.get("fonte"):
        src = m.get("source") or ""
        fonte = m.get("credit") or (_host(src) if src.startswith("http") else src)
        if fonte:
            s["fonte"] = fonte


def _capture(url: str, phrase: str, out: Path, cache: Path) -> dict | None:
    """A proof screenshot, reused when the same page and phrase were already captured for this video."""
    import hashlib

    memo = cache / f"proof_{hashlib.sha1(f'{url}|{phrase}'.encode()).hexdigest()[:20]}.json"
    if memo.exists():
        try:
            hit = json.loads(memo.read_text())
            if hit and Path(hit["img"]).exists():
                return hit
        except (OSError, ValueError, KeyError, TypeError):
            pass
    got = proof.capture(url, phrase, out)
    if got:
        memo.parent.mkdir(parents=True, exist_ok=True)
        memo.write_text(json.dumps(got, ensure_ascii=False, default=str))
    return got


def _proofs(scenes: list[dict], words: list[dict], ctx: _Ctx) -> None:
    allow = bool(ctx.channel and ctx.channel.allow_article_images)
    for k, s in enumerate(scenes):
        if s["tipo"] != "prova":
            continue
        pv = s.get("prova") or {}
        if pv.get("url") and not s.get("fonte"):
            s["fonte"] = _host(pv["url"])
        if not allow:
            _card(s, "prints de matéria desligados no canal", _narration(scenes, k, words))
            continue
        urls = [pv.get("url")] + [u for u in ctx.sources_urls if u != pv.get("url")][:OTHER_PROOF_SOURCES]
        for i, url in enumerate(u for u in urls if u):
            progress.report(k, len(scenes), "print da fonte")
            got = _capture(url, pv.get("frase") or "", ctx.dest / "provas" / f"p{k:03d}_{i}.png", ctx.dest / "cache")
            if got:
                s.update(img=got["img"], size=got["size"], boxes=got["boxes"])
                if url != pv.get("url"):
                    s["prova"] = {**pv, "url": url}
                    s["fonte"] = _host(url)
                break
        else:
            _card(s, "frase da prova não encontrada na fonte", _narration(scenes, k, words))


def _backgrounds(scenes: list[dict], dest: Path) -> None:
    first: dict[str, dict] = {}
    for s in scenes:
        sec = s.get("secao")
        if sec and s.get("media") and _slug(" ".join(sec)) not in first:
            first[_slug(" ".join(sec))] = s
    made: dict[str, str | None] = {}
    for key, s in first.items():
        m = s["media"]
        at = (m["start"] + (m["window_end"] or m["start"])) / 2 if m["kind"] == "video" else 0.0
        p = _background(m["file"], m["kind"], at, dest / "bg" / f"{key}.jpg")
        made[key] = str(p) if p else None
    last = None
    for s in scenes:  # a section without material of its own keeps the previous section's background
        sec = s.get("secao")
        if sec:
            last = made.get(_slug(" ".join(sec))) or last
        s["bg"] = last
    first_bg = next((s["bg"] for s in scenes if s["bg"]), None)
    for s in scenes:
        s["bg"] = s["bg"] or first_bg


def source(session: Session, project: Project, language: str) -> Path:
    """Find the editorial video's pictures in `language` and write `<project>/<lang>/editorial/plan.json`."""
    from cvm.voice.narrate import latest_script

    script = latest_script(session, project.id, language)
    cenas = ((script.content or {}).get("editorial") or {}).get("cenas") if script else None
    if not cenas:
        raise EditorialError("O roteiro não tem as cenas editoriais (editorial.cenas); escreva-as para este vídeo.")
    words, total = timeline.words_for(session, project, language)
    scenes = timeline.resolve([dict(c) for c in cenas], words, total, hook_end(session, project, language),
                              bumper_seconds(session, project))
    dest = project_dir(project.id, language) / "editorial"
    dest.mkdir(parents=True, exist_ok=True)
    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    ctx = _Ctx(session, project, channel, dest, [u for u in (script.content.get("sources") or []) if u.startswith("http")])
    token = footage.CACHE_DIR.set(dest / "cache")  # YouTube searches/metadata kept for the next run of this video
    try:
        _pick_media(scenes, words, ctx)
    finally:
        footage.CACHE_DIR.reset(token)
    _proofs(scenes, words, ctx)
    _backgrounds(scenes, dest)
    out = dest / "plan.json"
    out.write_text(json.dumps(scenes, ensure_ascii=False, indent=1, default=str))
    log.info("project %s %s: editorial plan with %d scenes (%d cards from fallbacks)", project.id, language,
             len(scenes), sum(1 for s in scenes if s.get("fallback")))
    return out
