"""Cut editor (spec docs/superpowers/specs/2026-11-25-editor-de-cortes-design.md, cvm/lives/cuts.py):

GET   /cuts?status=review[,analysing…]&channel_id=   → {items: [summary]}
GET   /cuts/prep                                      → {items: [...]} downloads + analyses with their step/progress
GET   /cuts/channels/{channel_id}/settings            → {auto_approve_cuts, shorts_commentary}
PUT   /cuts/channels/{channel_id}/settings            {auto_approve_cuts?, shorts_commentary?} → same
GET   /cuts/{id}                                      → summary + cuts (+ warnings) + tracks + transcript + waveform
GET   /cuts/{id}/video?original=false                 → the 480p proxy (else the recording), HTTP Range for seeking
PUT   /cuts/{id}/cuts        {cuts: [...]}            → the whole edited list (add/move/resize/delete/status/comment)
PATCH /cuts/{id}/cuts/{cut_id} {start?, end?, status?, comment?, commentary?} → that cut
POST  /cuts/{id}/produce     {make_long?}             → "producing"; the worker creates the projects (tasks.produce_cuts)
POST  /cuts/projects/{project_id}/clip-only            → an existing Short redone as only the cut (no commentary)
POST  /cuts/projects/{project_id}/relayout {facecam?, order?} → an existing editorial Short recomposed with the
                                                        streamer camera + header + gameplay layout (cvm/lives/relayout.py)
PUT   /cuts/{id}/layout   {facecam?: {x,y,w,h}|null, order?: youtuber_top|header_top, enabled?} → summary (options)
POST  /cuts/{id}/dismiss                              → "dismissed" (nothing produced; the decisions still teach)
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from cvm.db import get_session
from cvm.lives import cuts
from cvm.models import Channel, CutReview

router = APIRouter(prefix="/cuts", tags=["cuts"])


def _review(session: Session, review_id: int) -> CutReview:
    r = session.get(CutReview, review_id)
    if not r:
        raise HTTPException(404, "Revisão de cortes não encontrada.")
    return r


@router.get("")
def list_reviews(status: str | None = None, channel_id: int | None = None, session: Session = Depends(get_session)):
    return {"items": cuts.listing(session, status, channel_id)}


@router.get("/prep")
def prep(session: Session = Depends(get_session)):
    """The phase before the editor, with each item's step and progress (cvm/lives/progress.py)."""
    return {"items": cuts.prep(session)}


class CutSettings(BaseModel):
    auto_approve_cuts: bool | None = None
    shorts_commentary: bool | None = None


def _settings(c: Channel) -> dict:
    from cvm.lives.package import shorts_commentary

    return {"auto_approve_cuts": cuts.auto_approve(c), "shorts_commentary": shorts_commentary(c)}


@router.get("/channels/{channel_id}/settings")
def get_settings(channel_id: int, session: Session = Depends(get_session)):
    c = session.get(Channel, channel_id)
    if not c:
        raise HTTPException(404)
    return _settings(c)


@router.put("/channels/{channel_id}/settings")
def put_settings(channel_id: int, body: CutSettings, session: Session = Depends(get_session)):
    c = session.get(Channel, channel_id)
    if not c:
        raise HTTPException(404)
    c.prefs = {**(c.prefs or {}), **{k: v for k, v in body.model_dump().items() if v is not None}}
    flag_modified(c, "prefs")
    session.add(c)
    session.commit()
    return _settings(c)


@router.post("/projects/{project_id}/clip-only")
def clip_only(project_id: int, session: Session = Depends(get_session)):
    """An existing Short made with commentary → only the cut (cvm/lives/clip_only.py)."""
    from cvm.lives import clip_only as co
    from cvm.models import Project

    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(404)
    try:
        return co.convert(session, p)
    except co.NotConvertible as e:
        raise HTTPException(409, str(e)) from e


class RelayoutIn(BaseModel):
    facecam: dict | None = None
    order: str | None = None


@router.post("/projects/{project_id}/relayout")
def relayout(project_id: int, body: RelayoutIn | None = None, session: Session = Depends(get_session)):
    """An existing editorial Short redone with the streamer camera + header + gameplay layout (cvm/lives/relayout.py)."""
    from cvm.lives import relayout as rl
    from cvm.models import Project

    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(404)
    b = body or RelayoutIn()
    try:
        return rl.redo(session, p, b.facecam, b.order)
    except rl.NotRedoable as e:
        raise HTTPException(409, str(e)) from e


class LayoutIn(BaseModel):
    model_config = {"extra": "forbid"}
    facecam: dict | None = None
    order: str | None = None
    enabled: bool | None = None


@router.put("/{review_id}/layout")
def put_layout(review_id: int, body: LayoutIn, session: Session = Depends(get_session)):
    """The Shorts layout of this video: the streamer's camera box (null = none), the panel order, on/off."""
    r = _review(session, review_id)
    try:
        cuts.set_layout(session, r, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return cuts.summary(r)


@router.get("/{review_id}")
def get_review(review_id: int, session: Session = Depends(get_session)):
    return cuts.detail(_review(session, review_id))


@router.get("/{review_id}/video")
def video(review_id: int, original: bool = False, session: Session = Depends(get_session)):
    """FileResponse answers Range requests with 206, so the player can seek a multi-hour recording."""
    r = _review(session, review_id)
    path = None if original else r.proxy_path
    if not (path and Path(path).exists()):
        path = r.video_path
    if not path or not Path(path).exists():
        raise HTTPException(404, "O arquivo do vídeo não está mais aqui.")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-cache"})


class CutsIn(BaseModel):
    cuts: list[dict]


@router.put("/{review_id}/cuts")
def put_cuts(review_id: int, body: CutsIn, session: Session = Depends(get_session)):
    r = _review(session, review_id)
    try:
        cuts.update_cuts(session, r, body.cuts)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return cuts.detail(r)


class CutPatch(BaseModel):
    start: float | None = None
    end: float | None = None
    status: str | None = None
    comment: str | None = None
    commentary: bool | None = None


@router.patch("/{review_id}/cuts/{cut_id}")
def patch_cut(review_id: int, cut_id: str, body: CutPatch, session: Session = Depends(get_session)):
    r = _review(session, review_id)
    try:
        c = cuts.patch_cut(session, r, cut_id, body.model_dump())
    except KeyError as e:
        raise HTTPException(404, "Corte não encontrado.") from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {**c, "warnings": cuts.warnings(c, not r.third_party)}


class ProduceIn(BaseModel):
    make_long: bool | None = None


@router.post("/{review_id}/produce")
def produce(review_id: int, body: ProduceIn | None = None, session: Session = Depends(get_session)):
    r = _review(session, review_id)
    try:
        cuts.mark_producing(session, r, (body or ProduceIn()).make_long)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    from cvm.workers.tasks import produce_cuts

    produce_cuts(r.id)
    return cuts.summary(r)


@router.post("/{review_id}/dismiss")
def dismiss(review_id: int, session: Session = Depends(get_session)):
    r = _review(session, review_id)
    try:
        cuts.dismiss(session, r)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return cuts.summary(r)
