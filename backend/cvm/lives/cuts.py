"""The cut editor's backend (spec docs/superpowers/specs/2026-11-25-editor-de-cortes-design.md, Parte 2).

No Short of a long video is made before the owner goes through the editor (/fabrica/cortes/[id]):
analyse (worker) → CutReview "review" (shown in Sua vez) → he moves/resizes/creates/deletes cuts, approves or
discards them and writes a comment per cut → produce (worker) creates the projects from the approved cuts.

- kind "shorts" (Crescer link/upload, "Cortar este", cvm/lives/runs.py): one Short project per approved cut through
  cvm/lives/package.create_projects (+ the long "melhores momentos" of an own recording when `options.make_long`);
- kind "segments" (/fabrica/novo "Melhores momentos" links, cvm/media_links.py): the approved cuts become the kept
  ranges of the request's projects, which only then start.
The owner's comment goes into the project's topic (the scriptwriter reads it through get_project) and leads that
cut's script. His decisions are kept per channel (Setting "cut_taste:<channel_id>") and go to the next analyses of
the channel as examples of his taste (`taste`). Channel.prefs.auto_approve_cuts (off by default) keeps the old
automatic path (package.make_package) for a channel that wants it.

Heavy data (per-second tracks for the lanes, transcript, waveform peaks, the highlights analysis) is written to
data/cuts/<id>.json; the review row keeps the cuts and the state.
"""

import json
import logging
from pathlib import Path

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from cvm.config import settings
from cvm.models import Channel, CutReview, Project, Setting, now

log = logging.getLogger(__name__)
STATUSES = ("suggested", "proposed", "approved", "discarded")
OPEN = ("analysing", "review", "producing")
SHORT_MIN_S, SHORT_MAX_S = 15.0, 60.0  # outside this the editor warns (never blocks)
TASTE_KEEP = 40
WAVE_STEP = 0.1


def auto_approve(channel: Channel | None) -> bool:
    """Channel.prefs.auto_approve_cuts: the old automatic path, no editor (off by default)."""
    return bool(channel and (channel.prefs or {}).get("auto_approve_cuts") is True)


def data_path(review_id: int) -> Path:
    return settings.data_dir / "cuts" / f"{review_id}.json"


def load_data(review: CutReview) -> dict:
    p = data_path(review.id)
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def proxy_reporter(duration: float):
    """ffmpeg `-progress` lines → "Versão leve 42%" (no % when the length is unknown)."""
    from cvm.lives import progress

    def on_line(line: str) -> None:
        t = progress.ffmpeg_seconds(line) if line.startswith(("out_time", "time=")) else None
        if t is None:
            return
        if duration > 0:
            pct = min(99.0, 100.0 * t / duration)
            progress.report(progress.PROXY, f"Versão leve {pct:.0f}%", pct=pct, done=round(t), total=round(duration))
        else:
            progress.report(progress.PROXY, f"Versão leve ({progress.hms(t)} prontos)", done=round(t))
    return on_line


def review_proxy(video: Path, duration: float = 0.0, run=None) -> Path | None:
    """A light 480p H.264 copy for the editor's player (fast seek: a keyframe every 2 s, faststart), kept next to
    the recording and reused; None when ffmpeg fails (the player then gets the original). Its progress goes to the
    current reporter ("Versão leve N%", cvm/lives/progress.py)."""
    from cvm.lives import progress

    proxy = video.with_name(video.stem + "_proxy480.mp4")
    if proxy.exists() and proxy.stat().st_size > 0:
        return proxy
    progress.report(progress.PROXY, "Versão leve", pct=0.0 if duration > 0 else None)
    tmp = proxy.with_suffix(".part.mp4")
    cmd = [settings.ffmpeg_bin, "-v", "error", "-nostats", "-progress", "pipe:1", "-y", "-i", str(video),
           "-vf", "scale=-2:480", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
           "-force_key_frames", "expr:gte(t,n_forced*2)", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
           str(tmp)]
    r = (run or progress.run_lines)(cmd, proxy_reporter(duration))
    if r.returncode != 0 or not tmp.exists():
        log.warning("review proxy of %s failed: %s", video.name, (r.stderr or "")[-300:])
        tmp.unlink(missing_ok=True)
        return None
    tmp.replace(proxy)
    return proxy


def _save(session: Session, review: CutReview) -> CutReview:
    review.updated_at = now()
    for f in ("cuts", "options", "result", "live", "third_party"):
        if f in review.__dict__:  # an expired attribute was not changed
            flag_modified(review, f)
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def cuts_from(props: list[dict], commentary: bool = False) -> list[dict]:
    """Detection proposals (cvm/lives/viral.detect) → the editor's cuts, best first."""
    out = []
    for n, p in enumerate(props, 1):
        out.append({"id": f"c{n}", "origin": "ai", "start": float(p["start"]), "end": float(p["end"]),
                    "status": "suggested" if p.get("suggested") else "proposed", "hook": p.get("hook") or "",
                    "why": p.get("why") or "", "scores": p.get("scores") or {}, "score": p.get("score"),
                    "rank": p.get("rank", n), "tags": p.get("tags") or "", "comment": "",
                    "commentary": commentary})
    return out


def taste(session: Session, channel_id: int | None) -> list[str]:
    """The owner's earlier decisions on this channel's cuts, as short examples for the next analyses."""
    row = session.get(Setting, f"cut_taste:{channel_id}") if channel_id else None
    out = []
    for d in (row.value or {}).get("items", []) if row else []:
        verdict = "APROVOU" if d.get("verdict") == "approved" else "DESCARTOU"
        bits = [f"{verdict} ({d.get('len', 0):.0f} s)"]
        if d.get("hook"):
            bits.append(f"gancho “{d['hook']}”")
        if d.get("text"):
            bits.append(f"fala “{d['text']}”")
        if d.get("comment"):
            bits.append(f"comentário dele: “{d['comment']}”")
        out.append(" — ".join(bits))
    return out


def record_decisions(session: Session, review: CutReview, data: dict | None = None) -> int:
    """Approved and discarded cuts (with the owner's comments) → Setting "cut_taste:<channel_id>" (last TASTE_KEEP)."""
    if not review.channel_id:
        return 0
    lines = (data if data is not None else load_data(review)).get("transcript") or []
    new = []
    for c in review.cuts:
        if c.get("status") not in ("approved", "discarded"):
            continue
        text = " ".join(ln["text"] for ln in lines if c["start"] <= ln["s"] < c["end"])[:200]
        new.append({"verdict": c["status"], "hook": c.get("hook") or "", "why": c.get("why") or "", "text": text,
                    "comment": (c.get("comment") or "")[:300], "len": round(c["end"] - c["start"], 1),
                    "origin": c.get("origin"), "review": review.id, "at": now().isoformat()})
    if not new:
        return 0
    key = f"cut_taste:{review.channel_id}"
    row = session.get(Setting, key) or Setting(key=key, value={})
    old = [d for d in (row.value or {}).get("items", []) if d.get("review") != review.id]
    row.value = {"items": (new + old)[:TASTE_KEEP]}
    flag_modified(row, "value")
    session.add(row)
    session.commit()
    return len(new)


def start(session: Session, channel: Channel | None, live: dict, video: Path, *, item_key: str | None,
          source: str = "lives", kind: str = "shorts", third_party: dict | None = None,
          options: dict | None = None) -> CutReview:
    """The review row, "analysing" while the worker works on it (the page shows it)."""
    review = CutReview(channel_id=channel.id if channel else None, item_key=item_key, source=source, kind=kind,
                       title=str(live.get("title") or video.name)[:200], status="analysing", video_path=str(video),
                       duration=float(live.get("duration") or 0), live=dict(live), third_party=third_party,
                       options=dict(options or {}))
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def analyse(session: Session, channel: Channel, live: dict, video: Path, third_party: dict | None = None,
            examples: list[str] | None = None, *, item_key: str | None = None, source: str = "lives",
            kind: str = "shorts", options: dict | None = None, min_shorts: int = 0, detect=None,
            review: CutReview | None = None) -> CutReview:
    """Worker side: proxy, highlights + signals, transcript, detection → the review waits for the owner.
    Raises (and marks the review "error") when the recording can't be analysed. Each step is reported on the
    review (CutReview.progress, cvm/lives/progress.py) and to an outer reporter (the lives run / link request)."""
    from cvm.lives import package

    review = review or start(session, channel, live, video, item_key=item_key, source=source, kind=kind,
                             third_party=third_party,
                             options={"make_long": not third_party, "commentary": package.shorts_commentary(channel),
                                      **(options or {})})
    from cvm.lives import progress

    with progress.use(progress.review_sink(progress.engine_of(session), review.id)):
        return _analyse(session, review, channel, live, video, third_party, examples, min_shorts, detect)


def _analyse(session: Session, review: CutReview, channel: Channel, live: dict, video: Path,
             third_party: dict | None, examples: list[str] | None, min_shorts: int, detect) -> CutReview:
    from cvm.lives import package, progress, signals, viral

    try:
        dur0 = float(live.get("duration") or 0)
        if dur0:
            length = dur0
        else:  # an upload: measured only for the proxy's percentage
            from cvm.api.media import _duration

            length = float(_duration(video) or 0)
        proxy = review_proxy(video, length)
        light = proxy if proxy and dur0 >= package.PROXY_MIN_S else None  # one decode of a multi-hour original
        progress.report(progress.SIGNALS, "Sinais: volume, movimento e cenas", done=1, total=4)
        dur, r = package.analyse_recording(video, live, source=light)
        live_s = float(live.get("duration") or dur)
        tracks: dict = dict(r.get("tracks") or {})
        progress.report(progress.SIGNALS, "Sinais: chat, replays e comentários", done=2, total=4)
        dens = package.chat_density(live, video) if live.get("url") and live_s >= 600 else {}
        tracks["chat"] = signals.chat_track(dens, dur) if dens else None
        tracks["replay"] = signals.heatmap_track(live.get("heatmap"), dur)
        vid = str(live.get("video_id") or "")
        if (live.get("platform") or "youtube") == "youtube" and len(vid) == 11:
            tracks["comments"] = signals.comments_track(signals.comment_times(vid), dur)
        progress.report(progress.SIGNALS, "Sinais: reações", done=3, total=4)
        tracks["reactions"] = signals.reactions_track(video, dur)
        language = package.source_language(live, channel)
        try:
            lines = signals.transcript(video, language)
        except Exception as e:  # noqa: BLE001 — no Fala lane; detection falls back to the loudness rule
            log.warning("transcript of %s unavailable: %s", video.name, e)
            lines = []
        moments = r.get("moments", [])
        if dens:
            from cvm.lives import chat

            moments = chat.boost(moments, dens, live_s)
        meta = {"moments": moments, "segments": r["segments"], "tracks": tracks}
        props, method = (detect or viral.detect)(video, meta, review.title, not third_party, language, live_s,
                                                 tracks=tracks, examples=examples, taste=taste(session, review.channel_id),
                                                 min_count=min_shorts)
        progress.report(progress.SIGNALS, "Montando o editor", done=4, total=4)
        lanes = signals.for_editor({k: v for k, v in tracks.items() if k in ("chat", "replay", "comments", "reactions")})
        data = {"tracks": lanes, "transcript": lines,
                "waveform": {"step": WAVE_STEP, "peaks": signals.waveform(proxy or video, WAVE_STEP)},
                "analysis": {k: r.get(k) for k in ("segments", "whole", "dropped", "moments")}}
        p = data_path(review.id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False))
        review.duration, review.proxy_path, review.method = dur, str(proxy) if proxy else None, method
        review.options = {**(review.options or {}), **find_layout(channel, proxy or video, dur, own=not third_party,
                                                                  current=review.options)}
        review.cuts, review.status, review.error = cuts_from(props, package.shorts_commentary(channel)), "review", None
        review = _save(session, review)
        progress.report(progress.READY, "Pronto para revisar", pct=100.0, force=True)
        return review
    except Exception as e:
        session.rollback()
        review = session.get(CutReview, review.id)
        review.status, review.error = "error", str(e)[:500]
        _save(session, review)
        raise


def find_layout(channel: Channel | None, video: Path, duration: float, own: bool, current: dict | None = None,
                detect=None) -> dict:
    """The Shorts layout options found at analysis time: {facecam: {x,y,w,h,auto}|None, facecam_order}.
    An own recording on a channel with a marked camera (Channel.prefs.facecam) uses that box; anything else is
    auto-detected (cvm/lives/facecam.py, light: ~20 frames of the proxy). A box the owner already set is kept."""
    from cvm.lives import facecam as fc_mod
    from cvm.render.stack import facecam_of, order_of

    current = current or {}
    order = order_of(current.get("facecam_order"))
    if "facecam" in current:  # set before (a re-analysis): the owner's choice stays
        return {"facecam": current["facecam"], "facecam_order": order}
    from cvm.lives import progress

    progress.report(progress.SIGNALS, "Procurando a câmera do youtuber", done=4, total=4)
    box = facecam_of(channel) if own else None
    if box:
        box = {**box, "auto": False, "from": "channel"}
    else:
        box = (detect or fc_mod.detect)(video, duration)
    return {"facecam": box, "facecam_order": order}


LAYOUT_OFF = "off"


def set_layout(session: Session, review: CutReview, body: dict) -> CutReview:
    """The editor's "Câmera do youtuber": {facecam?: {x,y,w,h} | null, order?: youtuber_top | header_top,
    enabled?: bool}. A drawn box replaces the detected one (auto false); enabled false keeps the box but makes the
    Shorts without the camera panel. Allowed while in review, and after it (for "Refazer com o layout novo")."""
    from cvm.render.stack import ORDERS, validate

    if review.status not in ("review", "done"):
        raise ValueError("Esses cortes não podem mais ser mudados.")
    opts = dict(review.options or {})
    if "facecam" in body:
        if body["facecam"] is None:
            opts["facecam"] = None
        else:
            opts["facecam"] = {**validate(body["facecam"]), "auto": False}
    if "order" in body and body["order"] is not None:
        if body["order"] not in ORDERS:
            raise ValueError("ordem: " + " | ".join(ORDERS))
        opts["facecam_order"] = body["order"]
    if "enabled" in body and body["enabled"] is not None:
        opts["facecam_off"] = not bool(body["enabled"])
    review.options = opts
    return _save(session, review)


def layout_of(review: CutReview) -> dict:
    """{facecam, facecam_order} for the Short projects' recording meta; {} when there is no camera or it is off."""
    from cvm.render.stack import order_of, validate

    opts = review.options or {}
    if opts.get("facecam_off") or not opts.get("facecam"):
        return {}
    try:
        box = validate(opts["facecam"])
    except ValueError:
        return {}
    return {"facecam": box, "facecam_order": order_of(opts.get("facecam_order"))}


def _float(v, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise ValueError(f"{name}: número de segundos")
    return round(float(v), 2)


def validate_cuts(review: CutReview, raw: list) -> list[dict]:
    """The owner's edited list → clean cuts. Times inside the video, end after start (≥ 1 s), known status,
    comment ≤ 2000 chars. The AI's fields (hook, why, scores, score, rank, tags) always come from the stored cut;
    a new id is an owner-made cut. ValueError (pt-BR) on anything else."""
    if not isinstance(raw, list) or len(raw) > 200:
        raise ValueError("cuts: lista de cortes")
    known = {c["id"]: c for c in review.cuts}
    dur = float(review.duration or 0)
    out, seen, n = [], set(), 0
    for c in raw:
        if not isinstance(c, dict):
            raise ValueError("cada corte é um objeto")
        a, b = _float(c.get("start"), "start"), _float(c.get("end"), "end")
        if a < 0 or b - a < 1 or (dur and b > dur + 0.5):
            raise ValueError("corte fora do vídeo ou com menos de 1 s")
        status = c.get("status") or "approved"
        if status not in STATUSES:
            raise ValueError("status: " + " | ".join(STATUSES))
        comment = str(c.get("comment") or "")
        if len(comment) > 2000:
            raise ValueError("comentário longo demais (máx. 2000 caracteres)")
        cid = str(c.get("id") or "")
        base = known.get(cid)
        if not base or cid in seen:
            while f"u{n}" in known or f"u{n}" in seen:
                n += 1
            cid = f"u{n}"
            base = {"origin": "owner", "hook": "", "why": "", "scores": {}, "score": None, "rank": None, "tags": ""}
        seen.add(cid)
        commentary = c.get("commentary", base.get("commentary", review.options.get("commentary", False)))
        out.append({**base, "id": cid, "start": a, "end": min(b, dur) if dur else b, "status": status, "comment": comment,
                    "commentary": bool(commentary)})
    return out


def update_cuts(session: Session, review: CutReview, raw: list) -> CutReview:
    if review.status != "review":
        raise ValueError("Esses cortes não estão mais em revisão.")
    review.cuts = validate_cuts(review, raw)
    return _save(session, review)


def patch_cut(session: Session, review: CutReview, cut_id: str, fields: dict) -> dict:
    """One cut changed (start, end, status, comment); KeyError when it doesn't exist."""
    cuts = [dict(c) for c in review.cuts]
    i = next((i for i, c in enumerate(cuts) if c["id"] == cut_id), None)
    if i is None:
        raise KeyError(cut_id)
    cuts[i] = {**cuts[i], **{k: v for k, v in fields.items() if k in ("start", "end", "status", "comment", "commentary") and v is not None}}
    update_cuts(session, review, cuts)
    return next(c for c in review.cuts if c["id"] == cut_id)


def approved(review: CutReview) -> list[dict]:
    return sorted([c for c in review.cuts if c.get("status") == "approved"], key=lambda c: c["start"])


def mark_producing(session: Session, review: CutReview, make_long: bool | None = None) -> CutReview:
    """API side of "Produzir aprovados": checks and hands the work to the worker (produce)."""
    if review.status != "review":
        raise ValueError("Esses cortes não estão em revisão.")
    if make_long is not None:
        review.options = {**(review.options or {}), "make_long": bool(make_long)}
    long_ok = review.kind == "shorts" and review.options.get("make_long") and not review.third_party
    if not approved(review) and not long_ok:
        raise ValueError("Aprove pelo menos um corte antes de produzir.")
    review.status = "producing"
    return _save(session, review)


def produce(session: Session, review: CutReview) -> dict:
    """Worker side: the projects from the approved cuts; the decisions go to the channel's taste. Marks the review
    "done" (result = the package) or back to "review" with the error."""
    try:
        data = load_data(review)
        result = _produce_segments(session, review) if review.kind == "segments" else _produce_shorts(session, review, data)
    except Exception as e:
        log.warning("cuts of review %s failed: %s", review.id, e)
        session.rollback()
        review = session.get(CutReview, review.id)
        review.status, review.error = "review", str(e)[:500]
        _save(session, review)
        raise
    record_decisions(session, review, data)
    review.status, review.result, review.error = "done", result, None
    _save(session, review)
    _item_done(session, review, result)
    return result


def _moments(review: CutReview) -> list[dict]:
    return [{"start": c["start"], "end": c["end"], "hook": c.get("hook") or "", "why": c.get("why") or "",
             **({"viral": int(round(c["score"]))} if c.get("score") is not None and c.get("origin") == "ai" else {}),
             "comment": c.get("comment") or "", "commentary": bool(c.get("commentary"))} for c in approved(review)]


def _produce_shorts(session: Session, review: CutReview, data: dict) -> dict:
    from cvm.lives import package

    channel = session.get(Channel, review.channel_id)
    r = data.get("analysis") or {}
    if not r.get("segments"):
        r = {**r, "segments": [{"start": 0.0, "end": review.duration}], "whole": True, "dropped": {}, "moments": []}
    pkg = package.create_projects(session, channel, review.live, Path(review.video_path), r, _moments(review),
                                  review.method or "viral", review.third_party, review.duration,
                                  make_long=bool(review.options.get("make_long", True)), layout=layout_of(review))
    return {**pkg, "review": review.id}


def _produce_segments(session: Session, review: CutReview) -> dict:
    """/fabrica/novo: the approved cuts are the kept ranges of the request's projects; then their jobs start."""
    from cvm import media_links
    from cvm.models import Asset
    from cvm.suggest import start as start_job

    cuts = approved(review)
    if not cuts:
        raise ValueError("Aprove pelo menos um trecho antes de produzir.")
    segs = [{"start": c["start"], "end": c["end"]} for c in cuts]
    notes = [f"{_fmt(c['start'])}–{_fmt(c['end'])}: {c['comment'].strip()}" for c in cuts if (c.get("comment") or "").strip()]
    done = []
    for pid in review.options.get("project_ids") or []:
        p = session.get(Project, pid)
        if not p:
            continue
        for a in session.exec(select(Asset).where(Asset.project_id == pid, Asset.kind == "user_media",
                                                  Asset.path == review.video_path)).all():
            a.meta = {**(a.meta or {}), "segments": segs, "segments_auto": False, "whole": False, "moments": []}
            flag_modified(a, "meta")
            session.add(a)
        if notes:
            p.topic = (f"{p.topic or ''}\n\nTrechos escolhidos pelo dono no editor de cortes, com instruções dele "
                       "(mandam no roteiro desses trechos): " + "; ".join(notes)).strip()
            session.add(p)
        session.commit()
        start_job(session, p)
        done.append(pid)
    if review.item_key:
        media_links.set_status(session, review.item_key, "ok")
    return {"projects": done, "segments": segs, "review": review.id}


def _fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def _item_done(session: Session, review: CutReview, result: dict) -> None:
    """The Crescer list shows the package once produced."""
    if review.source != "lives" or not review.item_key or not review.channel_id:
        return
    from cvm.lives import runs

    channel = session.get(Channel, review.channel_id)
    item = next((i for i in runs.status(session, channel)["items"] if i["video_id"] == review.item_key), None)
    if item:
        runs.put(session, channel, {**item, "status": "ok", "package": {k: v for k, v in result.items() if k != "review"},
                                    "review": review.id, "error": None})


def dismiss(session: Session, review: CutReview) -> CutReview:
    """The owner gives up on this video: nothing is produced, his discards still teach the next analyses."""
    if review.status not in ("review", "error"):
        raise ValueError("Essa revisão não pode ser descartada agora.")
    record_decisions(session, review)
    review.status = "dismissed"
    _save(session, review)
    if review.source == "lives" and review.item_key and review.channel_id:
        from cvm.lives import runs

        channel = session.get(Channel, review.channel_id)
        item = next((i for i in runs.status(session, channel)["items"] if i["video_id"] == review.item_key), None)
        if item:
            runs.put(session, channel, {**item, "status": "error", "error": "cortes descartados por você"})
    elif review.source == "fabrica" and review.item_key:
        from cvm import media_links

        media_links.set_status(session, review.item_key, "error", "trechos descartados no editor de cortes")
    return review


REUSED = ("vídeo de outro criador sem comentário: o YouTube pode tratar como conteúdo reutilizado e bloquear a "
          "monetização do canal")


def warnings(cut: dict, own: bool = True) -> list[str]:
    """What the editor shows beside a cut (never blocks): Short length outside 15–60 s, and a clip-only cut of
    another creator's video (YouTube's reused-content rule; the owner's goal is YPP)."""
    extra = [REUSED] if not own and not cut.get("commentary") and cut.get("status") != "discarded" else []
    return extra + _length_warnings(cut)


def _length_warnings(cut: dict) -> list[str]:
    n = cut["end"] - cut["start"]
    if n < SHORT_MIN_S:
        return [f"curto para um Short ({n:.0f} s; o normal é {SHORT_MIN_S:.0f}–{SHORT_MAX_S:.0f} s)"]
    if n > SHORT_MAX_S:
        return [f"longo para um Short ({n:.0f} s; o normal é {SHORT_MIN_S:.0f}–{SHORT_MAX_S:.0f} s)"]
    return []


def summary(review: CutReview) -> dict:
    cuts = review.cuts or []
    return {"id": review.id, "channel_id": review.channel_id, "title": review.title, "status": review.status,
            "kind": review.kind, "source": review.source, "method": review.method, "duration": review.duration,
            "error": review.error, "url": (review.live or {}).get("url") or None, "own": not review.third_party,
            "uploader": (review.third_party or {}).get("uploader"),
            "counts": {s: sum(1 for c in cuts if c.get("status") == s) for s in STATUSES},
            "options": review.options or {}, "result": review.result or {}, "progress": review.progress or None,
            "created_at": review.created_at.isoformat() if review.created_at else None,
            "updated_at": review.updated_at.isoformat() if review.updated_at else None}


def detail(review: CutReview) -> dict:
    """Everything the editor needs: the review, its cuts (with Short-length warnings) and the lanes' data."""
    d = load_data(review)
    return {**summary(review), "cuts": [{**c, "warnings": warnings(c, not review.third_party)} for c in review.cuts or []],
            "tracks": d.get("tracks") or {}, "transcript": d.get("transcript") or [],
            "waveform": d.get("waveform") or {"step": WAVE_STEP, "peaks": []},
            "has_proxy": bool(review.proxy_path and Path(review.proxy_path).exists())}


def listing(session: Session, status: str | None = None, channel_id: int | None = None, limit: int = 50) -> list[dict]:
    q = select(CutReview).order_by(CutReview.id.desc())
    if status:
        q = q.where(CutReview.status.in_(status.split(",")))
    if channel_id:
        q = q.where(CutReview.channel_id == channel_id)
    return [summary(r) for r in session.exec(q.limit(limit)).all()]


READY_SHOW_H = 2  # a finished analysis stays in the Fábrica queue this long, as "Pronto para revisar"
ERROR_SHOW_H = 24


def _recent(iso: str | None, hours: float) -> bool:
    from datetime import UTC, datetime, timedelta

    if not iso:
        return False
    try:
        at = datetime.fromisoformat(iso)
    except ValueError:
        return False
    return datetime.now(UTC) - (at if at.tzinfo else at.replace(tzinfo=UTC)) < timedelta(hours=hours)


def prep(session: Session) -> list[dict]:
    """The phase before the editor, for the Fábrica queue ("Analisando cortes: …"), every channel:
    - Crescer links/uploads still downloading (runs items "working" with no review yet; their progress row);
    - reviews being analysed, plus the ones that finished in the last READY_SHOW_H h (link to the editor) or failed
      in the last ERROR_SHOW_H h.
    /fabrica/novo requests show on their own line (GET /projects/links carries the same progress).
    Each: {key, kind: "download"|"review", title, channel_id, status, progress, review_id, error}."""
    from cvm.lives import progress

    out: list[dict] = []
    reviews = session.exec(select(CutReview).where(CutReview.status.in_(("analysing", "review", "error")))
                           .order_by(CutReview.id.desc()).limit(100)).all()
    analysing = {(r.channel_id, r.item_key) for r in reviews if r.status == "analysing"}
    for row in session.exec(select(Setting).where(Setting.key.like("lives:%"))).all():
        try:
            cid = int(row.key.split(":", 1)[1])
        except ValueError:
            continue
        for i in (row.value or {}).get("items", []):
            if i.get("status") != "working" or (cid, i.get("video_id")) in analysing:
                continue
            out.append({"key": f"lives:{cid}:{i['video_id']}", "kind": "download", "title": i.get("title") or "",
                        "channel_id": cid, "status": "working", "review_id": None, "error": None,
                        "progress": progress.get(session, progress.lives_key(cid, i["video_id"]))})
    for r in reviews:
        if r.source == "fabrica" and r.status == "analysing":
            continue  # the /fabrica/novo line shows it
        p = r.progress or None
        if r.status == "review" and not (p and p.get("step") == "ready" and _recent(p.get("at"), READY_SHOW_H)):
            continue
        if r.status == "error" and not _recent(r.updated_at.isoformat() if r.updated_at else None, ERROR_SHOW_H):
            continue
        out.append({"key": f"review:{r.id}", "kind": "review", "title": r.title, "channel_id": r.channel_id,
                    "status": r.status, "review_id": r.id, "progress": p, "error": r.error})
    return out
