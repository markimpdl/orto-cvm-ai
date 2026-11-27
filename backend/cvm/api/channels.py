from fastapi import Depends, HTTPException
from sqlmodel import Session, col, select

from cvm.api.crud import crud_router
from cvm.db import get_session
from cvm.models import Channel, TrendAnalysis

router = crud_router(Channel, "/channels")


def _channel(session: Session, channel_id: int) -> Channel:
    c = session.get(Channel, channel_id)
    if not c:
        raise HTTPException(404)
    return c


def _producing(session: Session, channel_id: int) -> Channel:
    from cvm.publish.tiktok import is_linked_account

    c = _channel(session, channel_id)
    if (c.status or "active") != "active":
        raise HTTPException(409, f"O canal {c.name} está {'pausado' if c.status == 'paused' else 'arquivado'}.")
    if is_linked_account(c):
        raise HTTPException(409, f"{c.name} é a conta do TikTok de outro canal: recebe só os Shorts dele.")
    return c


# ---- linked TikTok account (cvm/publish/tiktok.py) ----------------------------------------------------------------

@router.get("/{channel_id}/tiktok")
def tiktok_account(channel_id: int, session: Session = Depends(get_session)):
    """{connected, channel_id, handle, also, kids, max_seconds}: the TikTok account linked to this YouTube channel."""
    from cvm.publish import tiktok

    return tiktok.info(session, _channel(session, channel_id))


@router.post("/{channel_id}/tiktok")
def connect_tiktok(channel_id: int, body: dict, session: Session = Depends(get_session)):
    """{handle: "@conta", also?: bool}: link a TikTok account (a sibling channel in the same group, same language)."""
    from cvm.publish import tiktok

    channel = _channel(session, channel_id)
    try:
        tiktok.connect(session, channel, str(body.get("handle") or ""))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if "also" in body:
        channel.prefs = {**(channel.prefs or {}), "tiktok_also": bool(body["also"])}
        session.add(channel)
        session.commit()
    return tiktok.info(session, channel)


@router.put("/{channel_id}/tiktok")
def tiktok_default(channel_id: int, body: dict, session: Session = Depends(get_session)):
    """{also: bool}: tick "Publicar também no TikTok" by default on this channel's Shorts."""
    from cvm.publish import tiktok

    channel = _channel(session, channel_id)
    channel.prefs = {**(channel.prefs or {}), "tiktok_also": bool(body.get("also"))}
    session.add(channel)
    session.commit()
    return tiktok.info(session, channel)


def _publish_defaults(c) -> dict:
    """The upload choices shown on the channel page (owner, 2026-11-26: "quero poder ajustar isso na tela do canal")."""
    from cvm.publish.platforms import CATEGORIES, youtube_rules

    rules, prefs = youtube_rules(c), (c.prefs or {})
    off = {n for n, _ in rules["off_boxes"]}
    cat = str(prefs.get("youtube_category") or "")
    return {"synthetic": rules["synthetic"], "paid_promotion": rules["paid_promotion"],
            "auto_chapters": "Capítulos automáticos" not in off,
            "auto_places": "Permitir a localização automática" not in off,
            "notify_subscribers": rules["notify_subscribers"], "made_for_kids": bool(c.made_for_kids),
            "category": cat if cat in CATEGORIES and prefs.get("youtube_category_fixed") else "auto",
            "categories": [{"id": k, "name": v[0]} for k, v in CATEGORIES.items()]}


@router.get("/{channel_id}/publish-defaults")
def get_publish_defaults(channel_id: int, session: Session = Depends(get_session)):
    """{synthetic: auto|yes|no, paid_promotion, auto_chapters, auto_places, notify_subscribers, made_for_kids,
    category: "auto"|id, categories}: what "Preparar no navegador" and the kit fill on YouTube for this channel."""
    return _publish_defaults(_channel(session, channel_id))


@router.put("/{channel_id}/publish-defaults")
def put_publish_defaults(channel_id: int, body: dict, session: Session = Depends(get_session)):
    """Any of the GET fields; saved in Channel.prefs.publish_defaults (+ made_for_kids, youtube_category fixed)."""
    from cvm.publish.platforms import CATEGORIES

    c = _channel(session, channel_id)
    prefs = dict(c.prefs or {})
    pd = dict(prefs.get("publish_defaults") or {})
    if "synthetic" in body:
        if body["synthetic"] not in ("auto", "yes", "no"):
            raise HTTPException(422, "synthetic: auto, yes ou no")
        pd["synthetic"] = body["synthetic"]
    for k in ("paid_promotion", "auto_chapters", "auto_places", "notify_subscribers"):
        if k in body:
            pd[k] = bool(body[k])
    prefs["publish_defaults"] = pd
    if "category" in body:
        cat = str(body["category"] or "auto")
        if cat == "auto":
            prefs.pop("youtube_category_fixed", None)
        elif cat in CATEGORIES:
            prefs["youtube_category"], prefs["youtube_category_fixed"] = cat, True
        else:
            raise HTTPException(422, "categoria desconhecida")
    if "made_for_kids" in body:
        c.made_for_kids = bool(body["made_for_kids"])
    c.prefs = prefs
    session.add(c)
    session.commit()
    session.refresh(c)
    return _publish_defaults(c)


@router.delete("/{channel_id}/tiktok")
def disconnect_tiktok(channel_id: int, session: Session = Depends(get_session)):
    """Unlink the TikTok account: its channel is archived (history kept); scheduled TikTok posts stay on /publicar."""
    from cvm.publish import tiktok

    channel = _channel(session, channel_id)
    tiktok.disconnect(session, channel)
    return tiktok.info(session, channel)


def _latest(session: Session, channel_id: int) -> TrendAnalysis | None:
    return session.exec(select(TrendAnalysis).where(TrendAnalysis.channel_id == channel_id)
                        .order_by(col(TrendAnalysis.id).desc())).first()


@router.get("/{channel_id}/characters/{name}/voice")
def character_voice(channel_id: int, name: str, session: Session = Depends(get_session)):
    from pathlib import Path

    from fastapi.responses import FileResponse

    from cvm.voice.characters import find

    entry = find(_channel(session, channel_id), name)
    if not entry or not entry.get("voice") or not Path(entry["voice"]).is_file():
        raise HTTPException(404)
    return FileResponse(entry["voice"], media_type="audio/wav")


@router.post("/{channel_id}/characters")
def design_character(channel_id: int, body: dict, session: Session = Depends(get_session)):
    """{name, description}: create or redo a character's voice (use "Narradora"/"Narrador" for the narrator).
    The voice is designed in the worker (~1 min); the entry shows "pending" meanwhile."""
    from cvm.workers.tasks import design_character_voice

    channel = _channel(session, channel_id)
    name, description = str(body.get("name", "")).strip(), str(body.get("description", "")).strip()
    if not name or not description:
        raise HTTPException(422, "name e description são obrigatórios")
    chars = {k: v for k, v in (channel.characters or {}).items() if k.strip().lower() != name.lower()}
    old = next((v for k, v in (channel.characters or {}).items() if k.strip().lower() == name.lower()), {})
    channel.characters = {**chars, name: {**old, "description": description, "pending": True}}
    session.add(channel)
    session.commit()
    design_character_voice(channel_id, name, description)
    return {"queued": True}


@router.get("/{channel_id}/characters/{name}/sheet")
def character_sheet(channel_id: int, name: str, session: Session = Depends(get_session)):
    """The character's reference drawing (Klein series, cvm/visuals/cast.py)."""
    from fastapi.responses import FileResponse

    from cvm.visuals.cast import sheet_of

    sheet = sheet_of(_channel(session, channel_id), name)
    if not sheet:
        raise HTTPException(404)
    return FileResponse(sheet, media_type="image/png")


@router.post("/{channel_id}/characters/{name}/sheet")
def redraw_character_sheet(channel_id: int, name: str, body: dict | None = None, session: Session = Depends(get_session)):
    """{look?}: draw the character's reference sheet again (another seed), optionally from a corrected look.
    Drawn in the worker (~1 min); the entry shows "sheet_pending" meanwhile. Scenes drawn afterwards follow the
    new sheet; finished episodes keep their images."""
    from cvm.visuals import cast
    from cvm.workers.tasks import draw_character_sheet

    channel = _channel(session, channel_id)
    if not cast.uses_sheets(channel):
        raise HTTPException(409, "Esta série não desenha com fichas de personagem (FLUX.2 Klein).")
    stored, old = cast._entry(channel, name)
    look = str((body or {}).get("look") or old.get("look") or "").strip()
    if not look:
        raise HTTPException(422, "Descreva a aparência do personagem (look).")
    channel.characters = {**(channel.characters or {}), stored: {**{k: v for k, v in old.items() if k != "sheet_error"},
                                                                  "look": look, "sheet_pending": True}}
    session.add(channel)
    session.commit()
    draw_character_sheet(channel_id, stored, look)
    return {"queued": True}


@router.get("/meta/voices")
def voice_library(session: Session = Depends(get_session)):
    """Every character voice already made, in any series: [{channel_id, channel, name, description}]."""
    from pathlib import Path

    out = []
    for c in session.exec(select(Channel)).all():
        for name, e in (c.characters or {}).items():
            if e.get("voice") and Path(e["voice"]).is_file():  # a character with only a drawing has no voice to lend
                out.append({"channel_id": c.id, "channel": c.name, "name": name, "description": e.get("description")})
    return out


@router.post("/{channel_id}/characters/reuse")
def reuse_character(channel_id: int, body: dict, session: Session = Depends(get_session)):
    """{from_channel_id, name, as_name?}: use a voice from another series (same audio reference, no new design)."""
    from cvm.voice.characters import find

    channel, src = _channel(session, channel_id), _channel(session, int(body["from_channel_id"]))
    entry = find(src, str(body["name"]))
    if not entry or not entry.get("voice"):
        raise HTTPException(404, "voz não encontrada")
    as_name = str(body.get("as_name") or body["name"]).strip()
    chars = {k: v for k, v in (channel.characters or {}).items() if k.strip().lower() != as_name.lower()}
    channel.characters = {**chars, as_name: {k: v for k, v in entry.items() if k not in ("pending", "error")}}
    session.add(channel)
    session.commit()
    return channel.characters[as_name]


@router.delete("/{channel_id}/characters/{name}")
def delete_character(channel_id: int, name: str, session: Session = Depends(get_session)):
    channel = _channel(session, channel_id)
    channel.characters = {k: v for k, v in (channel.characters or {}).items() if k.strip().lower() != name.lower()}
    session.add(channel)
    session.commit()
    return {"deleted": name}


@router.get("/{channel_id}/ideas")
def channel_ideas(channel_id: int, session: Session = Depends(get_session)):
    """The channel's latest niche ideas (Claude), each with its index and project_id once produced."""
    _channel(session, channel_id)
    a = _latest(session, channel_id)
    if not a:
        return None
    return {**a.model_dump(), "items": [{**it, "index": i} for i, it in enumerate(a.items)]}


@router.post("/{channel_id}/ideas")
def new_channel_ideas(channel_id: int, session: Session = Depends(get_session)):
    """Ask Claude (subscription) for video ideas from the channel's niche/brief. Runs in the worker."""
    from cvm.api.trends import AI_IDEAS_OFF, _ai_ideas_on
    from cvm.workers.tasks import run_trend_analysis

    _producing(session, channel_id)
    if not _ai_ideas_on(session):
        raise HTTPException(409, AI_IDEAS_OFF)
    last = _latest(session, channel_id)
    if last and last.status == "running":
        return last
    a = TrendAnalysis(channel_id=channel_id)
    session.add(a)
    session.commit()
    session.refresh(a)
    run_trend_analysis(a.id)
    return a


@router.post("/{channel_id}/ideas/produce")
def produce_channel_ideas(channel_id: int, body: dict, session: Session = Depends(get_session)):
    """{indexes: [int]} or {best: n}: put those ideas in the production queue (same pipeline as trends).
    `channel_ids` (this channel + siblings of its group): one project per channel, each adapted to its audience;
    the idea links to this channel's project. A similar recent video (cvm/guards.py) skips the idea unless
    `force_similar`; skipped ideas come back in `skipped` with the reason."""
    from cvm.channels import ChannelClosed, siblings
    from cvm.suggest import adaptation_note, project_from_channel_idea

    channel = _producing(session, channel_id)
    a = _latest(session, channel_id)
    if not a or a.status != "done":
        raise HTTPException(409, "sem ideias prontas para este canal")
    group = {c.id: c for c in siblings(session, channel)}
    targets = [channel, *[group[int(i)] for i in dict.fromkeys(body.get("channel_ids") or []) if int(i) in group]]
    force = bool(body.get("force_similar"))
    open_ = [i for i, it in enumerate(a.items) if not it.get("project_id")]
    wanted = body.get("indexes") if body.get("indexes") is not None else open_[: int(body.get("best") or 0)]
    items, created, skipped = [dict(it) for it in a.items], [], []
    for i in wanted:
        if i not in open_:
            continue
        made = []
        for c in targets:
            note = adaptation_note(c, [x for x in targets if x.id != c.id]) if len(targets) > 1 else ""
            try:
                made.append(project_from_channel_idea(session, c, items[i], note=note, force_similar=force))
            except ChannelClosed as e:  # also SimilarTopic: a similar video on that channel in the last 30 days
                skipped.append({"index": i, "channel_id": c.id, "reason": str(e)})
                if c.id == channel.id:
                    break  # the idea's own channel refused it: no siblings either
        if made and made[0].channel_id == channel.id:
            items[i]["project_id"] = made[0].id
        created += [p.id for p in made]
    a.items = items  # new list so SQLAlchemy saves the JSON change
    session.add(a)
    session.commit()
    return {"created": created, "skipped": skipped}


# --- publishing calendar (cvm/schedule.py, docs/plano-canais-e-voz.md Phases 2–3) ---

@router.get("/{channel_id}/week")
def channel_week(channel_id: int, session: Session = Depends(get_session)):
    """This week in the audience's timezone: goal per format, calendar (filled/empty slots) and next free slots."""
    from cvm import schedule

    return schedule.week(session, _channel(session, channel_id))


@router.get("/{channel_id}/slots")
def channel_slots(channel_id: int, kind: str | None = None, profile: str | None = None, style: str | None = None,
                  n: int = 6, session: Session = Depends(get_session)):
    """Next free publishing times for a format (`kind` short|long|episode…, or a render `profile` like youtube)."""
    from cvm import schedule

    channel = _channel(session, channel_id)
    kind = kind or schedule.kind_of_profile(channel, profile or "youtube", style)
    return schedule.next_slots(session, channel, kind, n=max(1, min(n, 20)))


@router.get("/goals")
def channel_goals(session: Session = Depends(get_session)):
    """The sidebar's summary: every active or paused channel with this week's goal per format."""
    from cvm import schedule
    from cvm.channels import FLAGS
    from cvm.publish.tiktok import is_linked_account

    out = []
    for c in session.exec(select(Channel).order_by(col(Channel.id))).all():
        if (c.status or "active") not in ("active", "paused") or is_linked_account(c):
            continue  # a linked TikTok account shows on its YouTube channel's page
        out.append({"id": c.id, "name": c.name, "language": c.language, "flag": FLAGS.get(c.language, ""),
                    "status": c.status or "active", "youtube_handle": c.youtube_handle,
                    "goal": schedule.week_goal(session, c)})
    return out


# "/goals" must come before the CRUD router's "/{item_id}" (which would answer 422 for it)
_goals = next(r for r in router.routes if getattr(r, "path", "") == "/channels/goals")
router.routes.remove(_goals)
router.routes.insert(0, _goals)


@router.get("/{channel_id}/bumpers")
def bumper_options(channel_id: int, session: Session = Depends(get_session)):
    """The short bumpers this channel can use: its own (when it has a logo in data/channels/<id>/brand) and those
    of every other channel with a logo. `chosen` = prefs.bumper_from (another channel's id) or null (its own)."""
    from cvm.render.bumper_brand import logo_file

    channel = _channel(session, channel_id)
    others = [{"id": c.id, "name": c.name, "language": c.language}
              for c in session.exec(select(Channel).where(Channel.status != "archived")).all()
              if c.id != channel_id and logo_file(c.id)]
    return {"own": bool(logo_file(channel_id)), "chosen": (channel.prefs or {}).get("bumper_from"), "others": others}


# ---- streamer camera ("melhores momentos da live" layout, cvm/render/stack.py) ------------------------------------

def facecam_sources(session: Session, channel_id: int, n: int = 8) -> list[dict]:
    """The channel's most recent video files to mark the camera on: videos attached to its projects (links,
    uploads), then the downloaded lives in data/lives/<id>/. [{name, path}], newest first, files that exist."""
    from pathlib import Path

    from cvm.config import settings
    from cvm.models import Asset, Project

    out, seen = [], set()
    rows = session.exec(select(Asset).join(Project, Asset.project_id == Project.id)
                        .where(Project.channel_id == channel_id, Asset.kind == "user_media")
                        .order_by(col(Asset.id).desc()).limit(60)).all()
    for a in rows:
        p = Path(a.path)
        if (a.meta or {}).get("kind") == "video" and p.exists() and str(p) not in seen:
            seen.add(str(p))
            out.append({"name": (a.meta or {}).get("name") or p.name, "path": str(p)})
    lives = settings.data_dir / "lives" / str(channel_id)
    if lives.is_dir():
        for p in sorted(lives.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True):
            if str(p) not in seen:
                seen.add(str(p))
                out.append({"name": p.name, "path": str(p)})
    return out[:n]


@router.get("/{channel_id}/facecam")
def get_facecam(channel_id: int, session: Session = Depends(get_session)):
    """{facecam: {x,y,w,h}|null, landscape, sources: [{index, name}]} for the camera marker on /canais/[id]."""
    from cvm.render.stack import facecam_of

    c = _channel(session, channel_id)
    return {"facecam": facecam_of(c), "landscape": bool((c.prefs or {}).get("facecam_landscape")),
            "sources": [{"index": i, "name": s["name"]} for i, s in enumerate(facecam_sources(session, channel_id))]}


@router.get("/{channel_id}/facecam/frame")
def facecam_frame(channel_id: int, source: int = 0, at: float = 0.3, session: Session = Depends(get_session)):
    """One JPEG frame (1280 wide) of one of the channel's recent videos, `at` = position as a share of its length.
    A single ffmpeg frame grab: quick enough for the API thread."""
    import subprocess
    import tempfile
    from pathlib import Path

    from fastapi.responses import Response

    from cvm.config import settings
    from cvm.media import _bin, duration

    _channel(session, channel_id)
    sources = facecam_sources(session, channel_id)
    if not sources:
        raise HTTPException(404, "Este canal ainda não tem vídeos baixados ou enviados para marcar a câmera.")
    src = sources[min(max(source, 0), len(sources) - 1)]["path"]
    try:
        t = duration(src) * min(max(at, 0.0), 0.95)
    except Exception:  # noqa: BLE001 — unreadable length: the first seconds
        t = 1.0
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "frame.jpg"
        r = subprocess.run([_bin(settings.ffmpeg_bin, "ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                            "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "4", str(out)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode or not out.exists():
            raise HTTPException(500, f"não consegui tirar um quadro do vídeo: {(r.stderr or '')[-200:]}")
        return Response(out.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.put("/{channel_id}/facecam")
def put_facecam(channel_id: int, body: dict, session: Session = Depends(get_session)):
    """{facecam: {x,y,w,h} (fractions of the frame) | null, landscape?: bool} → Channel.prefs.facecam (+ the 16:9
    option facecam_landscape). null removes the camera: lives go back to the plain layout."""
    from cvm.render.stack import validate

    c = _channel(session, channel_id)
    prefs = dict(c.prefs or {})
    if body.get("facecam") is None:
        prefs.pop("facecam", None)
    else:
        try:
            prefs["facecam"] = validate(body["facecam"])
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    if "landscape" in body:
        prefs["facecam_landscape"] = bool(body["landscape"])
    c.prefs = prefs
    session.add(c)
    session.commit()
    return {"facecam": prefs.get("facecam"), "landscape": bool(prefs.get("facecam_landscape"))}
