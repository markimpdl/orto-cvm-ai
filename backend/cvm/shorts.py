"""A Short is written as a Short, never cut out of the long video (user, 2026-11-23: "o short nao pode ser só um
pedaco do video completo, ele precisa ser escrito para ser um short").

Project 643 (safari) showed why: its `youtube_shorts` render was the whole 6-minute script in 9:16 (75 scenes, cut at
2:53), ending with the long video's subscribe ask and "watch the next video" — no hook of its own, no loop.

So when a long script would be rendered in a vertical profile, the composer drops those profiles from the project
and opens a sibling project for the Short instead: same channel, style, languages, avatar and the same files of the
user (rows pointing to the same paths), vertical platforms only. The scriptwriter then follows its Shorts rules
(hook in the first second, one point, cuts every 2–3 s, no subscribe ask, last line loops into the first).
Since 2026-11-23 the `_cuts` profiles (clips cut out of the long video) go the same way: no loop, no own hook.
"""

import logging

from sqlmodel import Session, select

from cvm.models import Asset, Project, Setting

log = logging.getLogger(__name__)

VERTICAL = {"youtube_shorts", "instagram", "tiktok"}
LONG_S = 75.0  # a narration longer than this is a long video's script, not a Short's


def _key(project_id: int) -> str:
    return f"short_sibling:{project_id}"


def sibling_of(session: Session, project_id: int) -> int | None:
    row = session.get(Setting, _key(project_id))
    return (row.value or {}).get("project_id") if row else None


def long_script(session: Session, project: Project, language: str) -> bool:
    from cvm.voice.narrate import narration_asset

    narr = narration_asset(session, project.id, language)
    return bool(narr and float((narr.meta or {}).get("duration") or 0) > LONG_S)


def split_targets(session: Session, project: Project, targets: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The (language, profile) pairs to render for `project`: vertical profiles of a long script are removed and
    handed to a sibling Short project (opened once)."""
    # cuts of the long video ("youtube_shorts_cuts") are Shorts too: same rule (user, 2026-11-23: "short
    # ficou sem final. Os scripts de shorts precisam terminar como se fosse loops")
    vertical = [(lang, prof) for lang, prof in targets
                if prof.removesuffix("_cuts") in VERTICAL and long_script(session, project, lang)]
    if not vertical or len(vertical) == len(targets):  # nothing vertical, or nothing else to render: as before
        return targets
    if not sibling_of(session, project.id):
        try:
            open_sibling(session, project, sorted({p.removesuffix('_cuts') for _, p in vertical}))
        except Exception as e:  # noqa: BLE001 — never block the long video on the Short
            log.warning("project %s: could not open the Short's own project: %s", project.id, e)
    return [t for t in targets if t not in vertical]


def open_sibling(session: Session, project: Project, platforms: list[str]) -> Project:
    """The Short's own project, with the same material; its script is written from scratch as a Short."""
    from cvm.suggest import new_project

    media = session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "user_media")).all()

    def attach(p: Project) -> None:
        for a in media:
            session.add(Asset(project_id=p.id, kind="user_media", path=a.path, source=a.source, license=a.license,
                              meta=dict(a.meta or {})))
        session.commit()

    topic = (f"SHORT com roteiro PRÓPRIO (35–50 s), feito para Shorts e não um pedaço do vídeo longo "
             f"\"{project.title}\" (projeto {project.id}). Um único momento forte do material, mostrado no primeiro "
             f"segundo; uma história só; sem pedido de inscrição; a última frase emenda na primeira (loop). "
             f"Use os mesmos arquivos do usuário. Tema do vídeo longo: {project.topic}")
    data = {"title": f"Short: {project.title}"[:200], "topic": topic, "channel_id": project.channel_id,
            "series_id": project.series_id, "avatar_id": project.avatar_id, "style": project.style,
            "languages": list(project.languages), "platforms": platforms, "presenter": project.presenter,
            "media_mode": project.media_mode, "media_cut": project.media_cut, "hook": True, "bumper": False,
            "force_similar": True}
    sib = new_project(session, data, before_job=attach if media else None)
    session.add(Setting(key=_key(project.id), value={"project_id": sib.id}))
    session.commit()
    log.info("project %s: Short gets its own project %s (%s)", project.id, sib.id, ", ".join(platforms))
    return sib
