""""Refazer com o layout novo" (owner, 2026-11-25): an existing editorial Short of a streamer's video (e.g. 735/736,
made before the 3-panel layout) is composed again as streamer camera + header + gameplay (cvm/render/stack.py), with
the new cover rule (no avatar on a cover of a video without avatar; the streamer's reaction instead).

`redo(session, project, facecam=None, order=None)`:
- the webcam box: the one given, else the box saved in the cut editor of the review that made this Short
  (CutReview.result.shorts), else what is already on the recording row; none → NotRedoable (draw it in the editor);
- written on the project's recording rows (Asset meta facecam / facecam_order);
- the job goes back to "composing" when it was past it (the script, narration and visuals stay); a job before
  composing just picks the box up when it gets there. Scheduled/published Shorts and a step running now are refused.
API: POST /cuts/projects/{project_id}/relayout (cvm/api/cuts.py). Never run by itself.
"""

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from cvm.models import Asset, CutReview, Job, Project, now

REFUSED = {"scheduled", "ready_for_manual_publish", "published"}
AFTER_COMPOSE = {"composing", "rendering", "validating", "seo", "awaiting_publish_review", "failed"}


class NotRedoable(Exception):
    """pt-BR reason shown to the user."""


def _rows(session: Session, project: Project) -> list[Asset]:
    return [a for a in session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "user_media")).all()
            if (a.meta or {}).get("kind", "video") == "video"]


def review_of(session: Session, project: Project) -> CutReview | None:
    """The cut-editor review whose production made this project."""
    for r in session.exec(select(CutReview).where(CutReview.status == "done").order_by(CutReview.id.desc())).all():
        res = r.result or {}
        if project.id in (res.get("shorts") or []) or project.id == res.get("long"):
            return r
    return None


def redo(session: Session, project: Project, facecam: dict | None = None, order: str | None = None) -> dict:
    """{project, facecam, order, state} — see the module docstring."""
    from cvm.lives.cuts import layout_of
    from cvm.render.stack import order_of, source_facecam, validate
    from cvm.voice.narrate import narration_asset

    job = session.exec(select(Job).where(Job.project_id == project.id)).first()
    if job and job.state in REFUSED:
        raise NotRedoable("esse Short já foi agendado ou publicado")
    if job and (job.progress or {}).get("started"):
        raise NotRedoable("uma etapa desse Short está rodando agora; pause o projeto e tente de novo")
    if (project.presenter or "") != "editorial":
        raise NotRedoable("o layout com a câmera do youtuber é para os Shorts sem avatar (cabeçalho + corte)")
    rows = _rows(session, project)
    if not rows:
        raise NotRedoable("esse projeto não tem o vídeo do corte")
    review = review_of(session, project)
    saved = layout_of(review) if review else {}
    if facecam is not None:
        try:
            box = validate(facecam)
        except ValueError as e:
            raise NotRedoable(str(e)) from e
    else:
        box = saved.get("facecam") or source_facecam(rows[0].meta)
    if not box:
        raise NotRedoable("não achei a câmera do youtuber nesse vídeo; marque o retângulo no editor de cortes")
    order = order_of(order or saved.get("facecam_order") or (rows[0].meta or {}).get("facecam_order"))
    for a in rows:
        a.meta = {**(a.meta or {}), "facecam": box, "facecam_order": order}
        flag_modified(a, "meta")
        session.add(a)
    state = job.state if job else None
    ready = all(narration_asset(session, project.id, lang) for lang in project.languages or [])
    if job and job.state in AFTER_COMPOSE and ready:
        job.history = [*job.history, {"from": job.state, "to": "composing", "at": now().isoformat(),
                                      "note": "refeito com o layout novo (câmera do youtuber)"}]
        job.state, job.progress, job.error, job.updated_at = "composing", {}, None, now()
        session.add(job)
        state = "composing"
    session.commit()
    if job and state == "composing":
        from cvm.workers.tasks import run_job

        run_job(job.id)
    return {"project": project.id, "facecam": box, "order": order, "state": state}
