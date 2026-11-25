import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, col, select

from cvm.db import get_session
from cvm.models import Channel, Project, Publication, Render
from cvm.paths import NO_CACHE
from cvm.publish import service
from cvm.config import settings
from cvm.guards import avatar_on_screen
from cvm.publish.platforms import CATEGORIES, UPLOAD_PAGES, YOUTUBE_OFF_BOXES, caption_text, category_for, with_channel_extras, youtube_rules, youtube_tags

router = APIRouter(tags=["publish"])


@router.get("/projects/{project_id}/renders")
def project_renders(project_id: int, session: Session = Depends(get_session)):
    renders = session.exec(select(Render).where(Render.project_id == project_id)).all()
    pubs = session.exec(select(Publication).where(col(Publication.render_id).in_([r.id for r in renders]))).all() if renders else []
    return [{**r.model_dump(), "publications": [p for p in pubs if p.render_id == r.id]} for r in renders]


@router.get("/renders/{render_id}/file")
def render_file(render_id: int, session: Session = Depends(get_session)):
    r = session.get(Render, render_id)
    if not r or not r.path or not Path(r.path).exists():
        raise HTTPException(404)
    return FileResponse(r.path, media_type="video/mp4", filename=Path(r.path).name, headers=NO_CACHE)


@router.get("/renders/{render_id}/sheet")
def render_sheet(render_id: int, session: Session = Depends(get_session)):
    r = session.get(Render, render_id)
    sheet = (r.meta or {}).get("contact_sheet") if r else None
    if not sheet or not Path(sheet).exists():
        raise HTTPException(404)
    return FileResponse(sheet, media_type="image/jpeg", headers=NO_CACHE)


@router.get("/renders/{render_id}/thumbnail")
def render_thumb(render_id: int, v: int = 0, session: Session = Depends(get_session)):
    """v: variant index (0 = main; 1, 2 = alternatives for YouTube "Test & Compare")."""
    r = session.get(Render, render_id)
    meta = (r.meta or {}) if r else {}
    variants = meta.get("thumbnails") or ([meta["thumbnail"]] if meta.get("thumbnail") else [])
    thumb = variants[v] if 0 <= v < len(variants) else None
    if not thumb or not Path(thumb).exists():
        raise HTTPException(404)
    return FileResponse(thumb, media_type="image/jpeg", filename=Path(thumb).name, headers=NO_CACHE)


@router.patch("/renders/{render_id}/seo")
def edit_seo(render_id: int, seo: dict, session: Session = Depends(get_session)):
    r = session.get(Render, render_id)
    if not r:
        raise HTTPException(404)
    r.seo = {**(r.seo or {}), **seo}
    session.add(r)
    session.commit()
    return r


class ScheduleItem(BaseModel):
    render_id: int
    channel_id: int
    scheduled_at: str | None = None
    mode: str | None = None


@router.post("/projects/{project_id}/schedule")
def schedule(project_id: int, items: list[ScheduleItem], session: Session = Depends(get_session)):
    try:
        pubs = service.schedule(session, project_id, [i.model_dump() for i in items])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(409, str(e)) from e
    return {"scheduled": [p.id for p in pubs]}


@router.get("/publications/due")
def publications_due(until: str, session: Session = Depends(get_session)):
    """How many publications the Publish page lists under "ready" (the sidebar badge, polled): ready for manual
    publishing, or scheduled up to `until` (the end of the viewer's day, ISO with offset). /publications is ~2 MB."""
    from datetime import datetime, timezone

    end = datetime.fromisoformat(until.replace("Z", "+00:00"))
    end = end.astimezone(timezone.utc).replace(tzinfo=None) if end.tzinfo else end  # stored as naive UTC
    rows = session.exec(select(Publication.status, Publication.scheduled_at)
                        .where(col(Publication.status).in_(["ready_for_manual_publish", "scheduled"]))).all()
    def naive(at):  # rows mix naive UTC and offset-aware datetimes
        return at.astimezone(timezone.utc).replace(tzinfo=None) if at.tzinfo else at

    due = sum(1 for st, at in rows if st == "ready_for_manual_publish" or (at is not None and naive(at) <= end))
    # `total` = everything waiting on the page (ready + scheduled on later days): the sidebar badge shows it, so the
    # number matches the list (user, 2026-11-23: "mostra 4 mas tem mais"); `count` = due by the end of today
    return {"count": due, "total": len(rows)}


@router.get("/publications")
def publications(status: str | None = None, session: Session = Depends(get_session)):
    q = select(Publication).order_by(col(Publication.scheduled_at))
    if status:
        q = q.where(Publication.status == status)
    out = []
    for p in session.exec(q).all():
        r, c = session.get(Render, p.render_id), session.get(Channel, p.channel_id)
        if not r or not c:  # deleted while this list was being read (one lookup per row): the UI needs both
            continue
        proj = session.get(Project, r.project_id)
        out.append({**p.model_dump(), "channel": c, "render": r, "project_title": proj.title if proj else None,
                    "project_id": proj.id if proj else None})
    return out


def _is_short(render: Render | None) -> bool:
    return bool(render) and ("short" in render.profile or render.profile.endswith("_cuts") or render.profile in ("instagram", "tiktok"))


def _views(p: Publication) -> int:
    m = p.metrics or {}
    return int((m.get("analytics") or {}).get("views") or m.get("viewCount") or 0)


def _long_videos(session: Session, channel_id: int, exclude: int | None = None) -> list[tuple[Publication, Render]]:
    """The channel's published long videos with a link, newest first."""
    pubs = session.exec(select(Publication).where(Publication.channel_id == channel_id, Publication.status == "published",
                                                  col(Publication.url).is_not(None))
                        .order_by(col(Publication.published_at).desc(), col(Publication.id).desc())).all()
    out = []
    for p in pubs:
        r = session.get(Render, p.render_id)
        if p.id != exclude and r and not _is_short(r):
            out.append((p, r))
    return out


def _video(p: Publication, r: Render, why: str) -> dict:
    return {"title": (r.seo or {}).get("title"), "url": p.url, "views": _views(p) or None, "why": why}


YOUTUBE_OFF = ["Promoção paga: “Não, meu vídeo não inclui promoção paga”",
               *[f"Deixar desmarcado: {name}" for name, _ in YOUTUBE_OFF_BOXES]]
_WORD = re.compile(r"\w{4,}")


def youtube_off(channel: Channel) -> list[str]:
    """The manual kit's upload checklist: YOUTUBE_OFF, or the channel's own publish_defaults (install wizard)."""
    rules = youtube_rules(channel)
    paid = ("Promoção paga: “Sim, meu vídeo inclui promoção paga”" if rules["paid_promotion"] else YOUTUBE_OFF[0])
    return [paid, *[f"Deixar desmarcado: {name}" for name, _ in rules["off_boxes"]]]


def end_screen_video(session: Session, pub: Publication) -> dict | None:
    """The suggested end-screen video: the channel's best performing long video, else its latest."""
    longs = _long_videos(session, pub.channel_id, pub.id)
    if not longs:
        return None
    best = max(longs, key=lambda pr: _views(pr[0]))
    if _views(best[0]) > 0:
        return _video(*best, "best")
    return _video(*longs[0], "latest")


def related_video(session: Session, pub: Publication, render: Render) -> dict | None:
    """A Short's "related video": the long video it came from, else the most similar title, else the latest long."""
    longs = _long_videos(session, pub.channel_id, pub.id)
    if not longs:
        return None
    for p, r in longs:
        if r.project_id == render.project_id:
            return _video(p, r, "same_project")
    words = {w.lower() for w in _WORD.findall((render.seo or {}).get("title") or "")}
    scored = [(len(words & {w.lower() for w in _WORD.findall((r.seo or {}).get("title") or "")}), i) for i, (p, r) in enumerate(longs[:20])]
    hits, i = max(scored, key=lambda x: (x[0], -x[1]))
    return _video(*longs[i], "topic") if hits >= 2 else _video(*longs[0], "latest")


@router.get("/publications/{pub_id}/kit")
def kit(pub_id: int, session: Session = Depends(get_session)):
    """Everything needed to publish by hand: file, thumbnail, metadata to copy, upload page, checklist (Phase 7)."""
    p = session.get(Publication, pub_id)
    if not p:
        raise HTTPException(404)
    r, c = session.get(Render, p.render_id), session.get(Channel, p.channel_id)
    platform = c.platform.value
    seo, meta = r.seo or {}, r.meta or {}
    yt = platform.startswith("youtube")
    short = _is_short(r)
    description = with_channel_extras(seo.get("description"), c)
    project = session.get(Project, r.project_id)
    # "altered or synthetic content" only when the AI avatar is on screen (user's rule 2026-11-24, cvm/guards.py)
    rule = youtube_rules(c)["synthetic"]  # a channel's publish_defaults may force it (install wizard)
    synthetic = avatar_on_screen(session, project, r) if rule == "auto" else rule == "yes"
    kids = bool(c.made_for_kids)
    end_screen = end_screen_video(session, p) if yt and not short else None
    related = related_video(session, p, r) if yt and short else None
    cat_id = category_for(r, c, getattr(project.style, "value", project.style) if project else None) if yt else None
    category = {"id": cat_id, "name": CATEGORIES[cat_id][0]} if yt else None
    title_variants = _title_variants(session, r, seo) if yt and not short else []
    chapters = bool(re.search(r"(?m)^\s*0?0:00\b", description))
    checklist = ["Enviar o arquivo de vídeo", "Colar título e descrição"]
    if rule != "auto":  # the channel's own choice (install wizard), whatever is on screen
        checklist.append(f"Conteúdo alterado ou sintético: {'Sim' if synthetic else 'Não'} (escolha do canal)")
    else:
        checklist.append("Marcar “Conteúdo alterado ou sintético”: Sim (o avatar de IA aparece no vídeo)" if synthetic
                         else "Conteúdo alterado ou sintético: Não (sem avatar na tela)")
    if yt:
        checklist.extend(youtube_off(c))
        checklist.append("Público: “Sim, é conteúdo para crianças” (canal infantil, obrigatório)" if kids
                         else "Público: “Não, não é conteúdo para crianças”")
        checklist.append(f"Categoria: {category['name']}")
        if not short:
            checklist.append("Enviar a thumbnail")
            if len(meta.get("thumbnails") or []) > 1:
                checklist.append("Em \"Testar e comparar\", subir as outras thumbnails (o YouTube escolhe a que retém mais)")
            if len(title_variants) > 1:
                checklist.append("Em \"Testar e comparar\", colar os títulos alternativos")
            if chapters:
                checklist.append("Capítulos: já estão na descrição (o primeiro em 0:00); confira se o YouTube os mostrou")
            if end_screen:
                checklist.append(f"Tela final (últimos 20 s): “Vídeo específico” = {end_screen['title'] or end_screen['url']} "
                                 f"({end_screen['url']}) + botão de inscrição")
            else:
                checklist.append("Tela final (últimos 20 s): “Melhor para o espectador” + botão de inscrição")
            if meta.get("end_screen_from") is not None:
                checklist.append(f"A tela final começa em {_mmss(meta['end_screen_from'])} (fundo limpo até o fim)")
        else:
            checklist.append(f"Vídeo relacionado: {related['title'] or related['url']} ({related['url']})" if related
                             else "Vídeo relacionado: nenhum longo publicado neste canal ainda")
    if platform in ("instagram", "tiktok"):
        checklist.append("Escolher a capa (primeiro frame com o gancho)")
    return {
        "publication": p, "channel": c, "render_id": r.id,
        "file_url": f"/renders/{r.id}/file", "thumbnail_url": f"/renders/{r.id}/thumbnail", "file_path": r.path,
        "video_removed": meta.get("video_removed"),  # cleaned up (cvm/cleanup.py): it is on YouTube
        "thumbnail_variants": [f"/renders/{r.id}/thumbnail?v={i}" for i in range(len(meta.get("thumbnails") or []))],
        "title": seo.get("title"), "description": description, "tags": youtube_tags(seo.get("tags", [])),
        "hashtags": seo.get("hashtags", []), "caption": caption_text(r, platform, c),
        "upload_page": UPLOAD_PAGES.get(platform), "checklist": checklist,
        "is_short": short, "end_screen_video": end_screen, "end_screen_from": meta.get("end_screen_from"),
        "related_video": related, "made_for_kids": kids, "made_for_kids_locked": kids, "synthetic": synthetic,
        "category": category, "chapters": chapters, "title_variants": title_variants,
    }


def _title_variants(session: Session, r: Render, seo: dict) -> list[str]:
    """YouTube "Test & Compare" titles: the SEO title first, then the script's packaging alternatives (same language)."""
    from sqlmodel import select

    from cvm.models import Script

    script = session.exec(select(Script).where(Script.project_id == r.project_id, Script.language == r.language)
                          .order_by(Script.id.desc())).first()
    pkg = ((script.content or {}).get("packaging") or {}) if script else {}
    titles = [seo.get("title") or pkg.get("title") or ""]
    titles += [str(a.get("title") or "") for a in pkg.get("alternatives") or [] if isinstance(a, dict)]
    out: list[str] = []
    for t in (t.strip() for t in titles):
        if t and t not in out:
            out.append(t)
    return out[:3]


def _mmss(s) -> str:
    try:
        s = int(float(s))
    except (TypeError, ValueError):
        return str(s)
    return f"{s // 60}:{s % 60:02d}"


class PublishedIn(BaseModel):
    url: str | None = None


@router.post("/publications/{pub_id}/published")
def mark_published(pub_id: int, body: PublishedIn, session: Session = Depends(get_session)):
    p = session.get(Publication, pub_id)
    if not p:
        raise HTTPException(404)
    return service.mark_published(session, p, body.url)


@router.post("/publications/{pub_id}/unpublish")
def unpublish(pub_id: int, session: Session = Depends(get_session)):
    p = session.get(Publication, pub_id)
    if not p:
        raise HTTPException(404)
    try:
        return service.unpublish(session, p)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/projects/{project_id}/unpublish")
def unpublish_project(project_id: int, session: Session = Depends(get_session)):
    """Undo "published" for every post of a project (kids series list, project page)."""
    pubs = session.exec(select(Publication).join(Render).where(Render.project_id == project_id,
                                                                Publication.status == "published")).all()
    if not pubs:
        raise HTTPException(409, "nenhuma publicação marcada como publicada")
    for p in pubs:
        service.unpublish(session, p)
    return {"reverted": [p.id for p in pubs]}


@router.post("/publications/{pub_id}/now")
def publish_now(pub_id: int, session: Session = Depends(get_session)):
    p = session.get(Publication, pub_id)
    if not p:
        raise HTTPException(404)
    try:
        return service.publish_now(session, p)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/publications/{pub_id}/reveal")
def reveal(pub_id: int, session: Session = Depends(get_session)):
    """Show the file in Finder / Explorer (drag & drop into the platform's upload page)."""
    import subprocess
    import sys

    p = session.get(Publication, pub_id)
    r = session.get(Render, p.render_id) if p else None
    if not r or not r.path:
        raise HTTPException(404)
    if sys.platform == "win32":
        subprocess.run(["explorer", f"/select,{Path(r.path)}"], check=False)
    else:
        subprocess.run(["open", "-R", r.path], check=False)
    return {"ok": True}


@router.post("/publications/{pub_id}/browser")
def browser_prepare(pub_id: int, session: Session = Depends(get_session)):
    """Open the upload page in Chrome, attach the video and fill the metadata; the user clicks Publish."""
    import subprocess
    import sys

    from cvm.config import REPO_ROOT

    p = session.get(Publication, pub_id)
    if not p:
        raise HTTPException(404)
    # a new run replaces an earlier one (e.g. it went to the wrong channel): stop its watcher, forget its link
    subprocess.run(["pkill", "-f", f"cvm.publish.browser_upload {pub_id}$"], capture_output=True) if sys.platform != "win32" else None
    (settings.data_dir / "publish-browser" / f"{pub_id}.json").unlink(missing_ok=True)
    log = settings.data_dir / "logs" / f"publish-browser-{pub_id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([sys.executable, "-m", "cvm.publish.browser_upload", str(pub_id)], cwd=REPO_ROOT / "backend",
                     stdout=log.open("w"), stderr=subprocess.STDOUT, start_new_session=True)
    return {"started": True}


@router.get("/publications/{pub_id}/browser")
def browser_status(pub_id: int):
    from cvm.publish.browser_upload import status

    return status(pub_id) or {"state": "idle", "steps": []}


@router.post("/channels/{channel_id}/browser-login")
def browser_login(channel_id: int, session: Session = Depends(get_session)):
    """One-time sign-in in a normal Chrome window on the channel's publishing profile."""
    from cvm.models import Channel
    from cvm.publish.browser_upload import login

    c = session.get(Channel, channel_id)
    if not c:
        raise HTTPException(404)
    login(c)
    return {"ok": True}
