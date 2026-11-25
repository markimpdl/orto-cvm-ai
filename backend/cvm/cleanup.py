"""Free disk from old, already-published videos: only the big video files go (final renders, avatar and AI clips,
stock footage, compose intermediates). Everything that records that the video was made stays: scripts and SEO
(title, description, tags) in the DB, voices (wav), thumbnails and stills (images), contact sheets, the YouTube link
and metrics — plus a `historico.json` written in the project folder before anything is deleted.

Rules (re-checked on every run, never trusted from the UI):
- a render's video goes only when every publication of it is `published` and the latest is at least `days` old;
- the project's other videos go only when every render of the project qualifies;
- the user's own uploads (`media/`) are never touched; files under MIN_BYTES are kept.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, select

from cvm.config import settings
from cvm.models import Asset, Channel, Job, Project, Publication, Render, Script

VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi"}
MIN_BYTES = 1_000_000  # "only big things go"
KEEP_DIRS = {"media"}  # the user's own clips


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def _root(project_id: int) -> Path:
    return settings.data_dir / "projects" / str(project_id)


def _videos(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXT
            and not (set(f.relative_to(root).parts[:-1]) & KEEP_DIRS) and f.stat().st_size >= MIN_BYTES]


def _kind(rel: Path, render_paths: set[Path]) -> str:
    if rel in render_paths:
        return "render"
    parts = set(rel.parts)
    if "avatar" in parts:
        return "avatar"
    if "visuals" in parts:
        return "visual"
    return "other"


def _project_plan(s: Session, p: Project, cutoff: datetime) -> dict | None:
    root = _root(p.id)
    renders = s.exec(select(Render).where(Render.project_id == p.id)).all()
    ok, kept, latest = [], [], None
    for r in renders:
        pubs = s.exec(select(Publication).where(Publication.render_id == r.id)).all()
        when = max((_aware(x.published_at) for x in pubs if x.published_at), default=None)
        if pubs and all(x.status == "published" for x in pubs) and when and when <= cutoff:
            ok.append(r)
            latest = max(latest or when, when)
        elif r.path and Path(r.path).exists():
            reason = ("não publicado" if not pubs else "publicado há pouco" if all(x.status == "published" for x in pubs)
                      else "publicação pendente")
            kept.append({"language": r.language, "profile": r.profile, "reason": reason})
    if not ok:
        return None
    all_done = not kept
    render_paths = {Path(r.path).resolve().relative_to(root.resolve()) for r in ok
                    if r.path and Path(r.path).exists() and Path(r.path).resolve().is_relative_to(root.resolve())}
    kept_paths = {Path(r.path).resolve() for r in renders if r not in ok and r.path}
    files = []
    for f in _videos(root):
        rel = f.resolve().relative_to(root.resolve())
        if f.resolve() in kept_paths:
            continue
        kind = _kind(rel, render_paths)
        if kind == "render" or all_done:
            files.append({"path": str(rel), "bytes": f.stat().st_size, "kind": kind})
    if not files:
        return None
    ch = s.get(Channel, p.channel_id) if p.channel_id else None
    return {"project_id": p.id, "title": p.title, "channel": ch.name if ch else None,
            "published_at": latest.isoformat() if latest else None, "bytes": sum(f["bytes"] for f in files),
            "files": sorted(files, key=lambda f: -f["bytes"]), "kept_renders": kept}


def preview(s: Session, days: int = 3) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, days))
    out = [x for p in s.exec(select(Project).order_by(Project.id)).all() if (x := _project_plan(s, p, cutoff))]
    return {"days": days, "total_bytes": sum(x["bytes"] for x in out), "projects": out}


def history(s: Session, p: Project) -> dict:
    """What stays on record for a cleaned project (also written to <project>/historico.json)."""
    ch = s.get(Channel, p.channel_id) if p.channel_id else None
    job = s.exec(select(Job).where(Job.project_id == p.id)).first()
    scripts = {}
    for sc in s.exec(select(Script).where(Script.project_id == p.id).order_by(Script.version)).all():
        scripts[sc.language] = {"version": sc.version, "approved": sc.approved, "content": sc.content}
    renders = []
    for r in s.exec(select(Render).where(Render.project_id == p.id)).all():
        pubs = s.exec(select(Publication).where(Publication.render_id == r.id)).all()
        renders.append({"language": r.language, "profile": r.profile, "part": r.part, "file": r.path, "seo": r.seo,
                        "thumbnails": (r.meta or {}).get("thumbnails"), "duration_s": (r.meta or {}).get("duration"),
                        "publications": [{"status": x.status, "url": x.url, "mode": str(x.mode),
                                          "published_at": x.published_at.isoformat() if x.published_at else None,
                                          "metrics": x.metrics} for x in pubs]})
    return {"project_id": p.id, "title": p.title, "topic": p.topic, "channel": ch.name if ch else None,
            "style": str(p.style), "languages": p.languages, "created_at": p.created_at.isoformat(),
            "state": job.state if job else None, "scripts": scripts, "renders": renders}


def run(s: Session, project_ids: list[int], days: int = 3) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, days))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    done, total = [], 0
    for pid in project_ids:
        p = s.get(Project, pid)
        plan = _project_plan(s, p, cutoff) if p else None
        if not plan:
            continue
        root = _root(pid)
        (root / "historico.json").write_text(json.dumps({**history(s, p), "cleaned_at": now},
                                                        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        removed, freed = set(), 0
        for f in plan["files"]:
            path = root / f["path"]
            try:
                size = path.stat().st_size
                path.unlink()
            except FileNotFoundError:
                continue
            removed.add(str(path.resolve()))
            freed += size
        for d in sorted((x for x in root.rglob("*_ltx") if x.is_dir()), reverse=True):  # emptied LTX work folders
            if not any(d.iterdir()):
                d.rmdir()
        for r in s.exec(select(Render).where(Render.project_id == pid)).all():
            if r.path and str(Path(r.path).resolve()) in removed:
                r.meta = {**(r.meta or {}), "video_removed": {"at": now, "bytes": next(
                    (f["bytes"] for f in plan["files"] if str((root / f["path"]).resolve()) == str(Path(r.path).resolve())), 0)}}
                s.add(r)
        for a in s.exec(select(Asset).where(Asset.project_id == pid)).all():
            if a.path and str(Path(a.path).resolve()) in removed:
                a.meta = {**(a.meta or {}), "removed": now}
                s.add(a)
        s.commit()
        done.append({"project_id": pid, "removed_bytes": freed, "files": len(removed)})
        total += freed
    return {"removed_bytes": total, "projects": done}
