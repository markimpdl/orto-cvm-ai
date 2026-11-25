"""Turn an existing Short of a cut into the clip-only format (owner, 2026-11-25): only the cut with its own sound,
editorial header on top, captions of the original speech, no narration and no avatar on screen.

For Short projects made before the change (queued with commentary). `convert`:
- project: presenter "editorial", the "SEM COMENTÁRIO" note at the start of the topic;
- its recording rows: keep_audio on, audio_under off;
- every language's latest script gets a new version whose scenes are narration-free `user_media` pieces covering
  the cut in order (≤ SCENE_S each, visual.keep_audio=true); packaging/hook/title are kept (the header comes from
  packaging.header, else the title), the spoken subscribe asks are dropped;
- the job goes back to "voicing" (nothing to voice, then visuals/compose) when it was past the script; a job still
  before its script just writes it in the new format; a published/scheduled one is refused, and so is a step
  running right now (pause it first).
API: POST /cuts/projects/{project_id}/clip-only (cvm/api/cuts.py). Never run by itself.
"""

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from cvm.models import Asset, Job, Project, Script, now

SCENE_S = 12.0
AFTER_SCRIPT = {"awaiting_approval", "voicing", "sourcing_visuals", "lipsyncing", "composing", "rendering",
                "validating", "seo", "awaiting_publish_review", "failed"}
REFUSED = {"scheduled", "ready_for_manual_publish", "published"}


class NotConvertible(Exception):
    """pt-BR reason shown to the user."""


def _segment(asset: Asset) -> tuple[float, float]:
    segs = (asset.meta or {}).get("segments") or []
    if segs:
        return float(segs[0]["start"]), float(segs[-1]["end"])
    return 0.0, float((asset.meta or {}).get("duration") or 0)


def scenes_for(asset: Asset) -> list[dict]:
    a, b = _segment(asset)
    if b - a < 1:
        raise NotConvertible("o material do Short não tem um trecho marcado")
    out, t, i = [], a, 0
    while t < b - 0.5:
        n = min(SCENE_S, b - t)
        if b - (t + n) < 3:  # no 1–2 s tail scene
            n = b - t
        out.append({"index": i, "narration": "", "on_screen_text": None, "target_seconds": round(n, 2),
                    "visual": {"type": "user_media", "media_id": asset.id, "media_start": round(t, 2), "keep_audio": True}})
        t += n
        i += 1
    return out


def convert(session: Session, project: Project) -> dict:
    """{project, scripts: [languages rewritten], state} — see the module docstring."""
    from cvm.lives.package import NO_COMMENTARY

    job = session.exec(select(Job).where(Job.project_id == project.id)).first()
    if job and job.state in REFUSED:
        raise NotConvertible("esse Short já foi agendado ou publicado")
    if job and (job.progress or {}).get("started"):
        raise NotConvertible("uma etapa desse Short está rodando agora; pause o projeto e tente de novo")
    rows = [a for a in session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "user_media")).all()
            if (a.meta or {}).get("kind", "video") == "video"]
    if not rows:
        raise NotConvertible("esse projeto não tem o vídeo do corte")
    for a in rows:
        a.meta = {**(a.meta or {}), "keep_audio": True, "audio_under": False}
        flag_modified(a, "meta")
        session.add(a)
    a, b = _segment(rows[0])
    note = NO_COMMENTARY.format(length=round(b - a))
    project.presenter = "editorial"
    if "SEM COMENTÁRIO" not in (project.topic or ""):
        project.topic = note + (project.topic or "")
    session.add(project)
    done = []
    for lang in project.languages or []:
        sc = session.exec(select(Script).where(Script.project_id == project.id, Script.language == lang)
                          .order_by(Script.version.desc())).first()
        if not sc:
            continue
        content = {k: v for k, v in (sc.content or {}).items() if k not in ("blocks", "cta_points", "editorial")}
        content.update(scenes=scenes_for(rows[0]), clip_only=True, intro_end=None)
        session.add(Script(project_id=project.id, language=lang, version=sc.version + 1, content=content,
                           approved=sc.approved))
        done.append(lang)
    state = job.state if job else None
    if job and done and job.state in AFTER_SCRIPT and job.state != "awaiting_approval":
        job.history = [*job.history, {"from": job.state, "to": "voicing", "at": now().isoformat(),
                                      "note": "convertido para só o corte (sem comentário)"}]
        job.state, job.progress, job.error, job.updated_at = "voicing", {}, None, now()
        session.add(job)
        state = "voicing"
    session.commit()
    if job and state == "voicing":
        from cvm.workers.tasks import run_job

        run_job(job.id)
    return {"project": project.id, "scripts": done, "state": state}
