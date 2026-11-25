"""Readable copy of each finished video (2026-11-23): when a video reaches
publish review, it is also saved under settings.export_dir as

    <channel>/<YYYY-MM-DD>_<id>-<title-slug>/
        <id>-<slug>[-<profile>].mp4   (hard link when possible, so no extra disk)
        capa.jpg / capa-<profile>.jpg
        publicacao.md                 (title, description, tags, hashtags, chapters, schedule, per render)

so the user can publish by hand from Finder. Re-exporting replaces the files (a re-render refreshes them).
"""

import os
import re
import shutil
import unicodedata
from pathlib import Path

from sqlmodel import Session, select

from cvm.config import settings
from cvm.models import Channel, Project, Publication, Render

PROFILE_LABEL = {"youtube": "YouTube (longo)", "youtube_shorts": "YouTube Shorts", "instagram_reels": "Instagram Reels",
                 "tiktok": "TikTok"}


def slug(text: str, limit: int = 60) -> str:
    """Plain lowercase words joined by dashes (no emoji, accents or #tags)."""
    text = re.sub(r"#\w+", " ", text or "")
    text = re.sub(r"^.*?Short \d+:\s*", "", text)  # "🟡 Short 2: …" → the title itself
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].rstrip("-") or "video"


def _place(src: Path, dest: Path) -> None:
    dest.unlink(missing_ok=True)
    try:
        os.link(src, dest)  # same disk: no copy
    except OSError:
        shutil.copy2(src, dest)


def _md(project: Project, renders: list[Render], schedules: dict[int, str]) -> str:
    out = [f"# {project.title}", ""]
    for r in renders:
        seo = r.seo or {}
        label = PROFILE_LABEL.get(r.profile, r.profile) + (f" — corte {r.part}" if r.part else "")
        out += [f"## {label} ({r.language})", ""]
        if schedules.get(r.id):
            out += [f"**Agendado:** {schedules[r.id]}", ""]
        out += ["**Título**", "", seo.get("title") or project.title, "", "**Descrição**", "", seo.get("description") or "", ""]
        if seo.get("tags"):
            out += ["**Tags**", "", ", ".join(seo["tags"]), ""]
        if seo.get("hashtags"):
            out += ["**Hashtags**", "", " ".join(seo["hashtags"]), ""]
        chapters = (r.meta or {}).get("chapters") or []
        if chapters:
            out += ["**Capítulos**", ""]
            out += [f"{int(c['t']) // 60}:{int(c['t']) % 60:02d} {c['title']}" for c in chapters]
            out += [""]
    return "\n".join(out).rstrip() + "\n"


def export_project(session: Session, project: Project, root: Path | None = None) -> Path | None:
    """Write (or refresh) the project's readable folder; returns it, or None when no render file exists yet."""
    renders = [r for r in session.exec(select(Render).where(Render.project_id == project.id)).all()
               if r.path and Path(r.path).exists()]
    if not renders:
        return None
    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    root = Path(root or settings.export_dir).expanduser()
    base = slug(project.title)
    day = (project.created_at.date().isoformat() if getattr(project, "created_at", None) else "")
    folder = root / (channel.name if channel else "Sem canal") / f"{day + '_' if day else ''}{project.id}-{base}"
    folder.mkdir(parents=True, exist_ok=True)
    single = len(renders) == 1
    for r in renders:
        tag = "" if single else f"-{r.profile}" + (f"-{r.part}" if r.part else "")
        _place(Path(r.path), folder / f"{project.id}-{base}{tag}.mp4")
        thumb = (r.meta or {}).get("thumbnail")
        if thumb and Path(thumb).exists():
            _place(Path(thumb), folder / f"capa{tag}.jpg")
    schedules = {}
    for r in renders:
        pub = session.exec(select(Publication).where(Publication.render_id == r.id)).first()
        if pub and pub.scheduled_at:
            schedules[r.id] = pub.scheduled_at.strftime("%d/%m/%Y %H:%M UTC")
    (folder / "publicacao.md").write_text(_md(project, renders, schedules), encoding="utf-8")
    return folder
