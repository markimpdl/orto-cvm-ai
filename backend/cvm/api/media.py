"""The user's own clips and images for a project: a small library the scriptwriter can place in scenes
(visual type "user_media" + media_id), and that the user can drop on any scene after visuals are picked."""

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session, select

from cvm.db import get_session
from cvm.models import Asset, Project
from cvm.paths import project_dir

router = APIRouter(prefix="/projects", tags=["media"])
VIDEO = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
SLIDE_TEXT_CHARS = 700  # per slide, in the description the scriptwriter reads


def media_kind(path: Path) -> str | None:
    ext = path.suffix.lower()
    return "video" if ext in VIDEO else "image" if ext in IMAGE else None


def _duration(path: Path) -> float | None:
    import subprocess

    from cvm.config import settings

    probe = str(settings.ffprobe_bin)
    out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    try:
        return round(float(out.stdout.strip()), 1)
    except ValueError:
        return None


def _has_speech(path: Path) -> bool:
    """The clip has an audio track that isn't silence (mean level above -45 dB)."""
    import subprocess

    from cvm.config import settings

    probe = subprocess.run([str(settings.ffprobe_bin), "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    if not probe.stdout.strip():
        return False
    out = subprocess.run([str(settings.ffmpeg_bin), "-v", "info", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out)
    return bool(m and float(m.group(1)) > -45)


def make_preview(path: Path, kind: str, duration: float | None) -> Path | None:
    """Small jpg the scriptwriter looks at: the image itself, or a grid of frames of a video (about one every 8 s,
    4 to 8 frames) with the second of each frame written on it, so the script can pick where a scene cuts in."""
    import subprocess

    from cvm.config import settings

    out = path.parent / ".previews" / f"{path.stem}.jpg"
    out.parent.mkdir(exist_ok=True)
    ff = settings.ffmpeg_bin
    if kind == "image":
        cmd = [ff, "-v", "error", "-y", "-i", str(path), "-vf", "scale='min(768,iw)':-2", "-frames:v", "1", str(out)]
    else:
        d = duration or 4.0
        n = max(4, min(8, round(d / 8)))
        times = [d * (i + 0.5) / n for i in range(n)]
        ins = [x for t in times for x in ("-ss", f"{t:.2f}", "-i", str(path))]
        label = "drawtext=text='{t}s':x=8:y=8:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=6"
        chains = [f"[{i}]scale=384:-2,{label.format(t=int(t))}[f{i}]" for i, t in enumerate(times)]
        cols = 4
        grid = "".join(f"[f{i}]" for i in range(n)) + f"xstack=inputs={n}:layout=" + "|".join(
            f"{(i % cols) and '+'.join(['w0'] * (i % cols)) or '0'}_{(i // cols) and 'h0' or '0'}" for i in range(n)) + ":fill=black"
        cmd = [ff, "-v", "error", "-y", *ins, "-filter_complex", ";".join(chains) + ";" + grid, "-frames:v", "1", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        import logging

        logging.getLogger(__name__).warning("preview failed for %s: %s", path.name, proc.stderr[-400:])
    return out if out.exists() else None


def _pdf_pages(pdf: Path) -> int:
    import subprocess

    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    return 0


def _slide_text(pdf: Path, page: int) -> str:
    import subprocess

    out = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-layout", "-enc", "UTF-8", str(pdf), "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    text = re.sub(r"[ \t]+", " ", out)
    text = re.sub(r"\s*\n\s*", " / ", text.strip()).strip(" /")
    return text[:SLIDE_TEXT_CHARS] + ("…" if len(text) > SLIDE_TEXT_CHARS else "")


def save_pdf(session: Session, project_id: int, upload: UploadFile, description: str = "") -> list[Asset]:
    """A slide deck: every page becomes one image (1920 px wide, in order) with the page's text as its description
    ("Slide 3/12: …"), so the scriptwriter explains the slides in sequence (style "lesson"). Needs poppler
    (pdftoppm/pdftotext: brew install poppler; on Windows, poppler's bin folder on PATH)."""
    import subprocess

    name = Path(upload.filename or "slides.pdf").name
    stem = (re.sub(r"[^\w\-]+", "_", Path(name).stem)[-40:] or "slides").strip("_") or "slides"
    d = project_dir(project_id) / "media"
    d.mkdir(exist_ok=True)
    pdf = d / ".pdf" / f"{stem}.pdf"
    pdf.parent.mkdir(exist_ok=True)
    n = 1
    while pdf.exists():
        pdf, n = pdf.with_name(f"{stem}_{n}.pdf"), n + 1
    stem = pdf.stem
    with pdf.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    try:
        pages = _pdf_pages(pdf)
    except FileNotFoundError:
        raise HTTPException(501, "para ler PDF instale o poppler (Mac: brew install poppler)")
    if not pages:
        raise HTTPException(422, "PDF sem páginas ou ilegível")
    proc = subprocess.run(["pdftoppm", "-png", "-scale-to-x", "1920", "-scale-to-y", "-1", str(pdf), str(d / f"{stem}-slide")],
                          capture_output=True, text=True)
    files = sorted(d.glob(f"{stem}-slide-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
    if proc.returncode != 0 or not files:
        raise HTTPException(422, f"não consegui converter o PDF: {proc.stderr[-300:]}")
    note = description.strip()
    out = []
    for i, img in enumerate(files, 1):
        text = _slide_text(pdf, i)
        desc = f"Slide {i}/{len(files)} de {name}" + (f": {text}" if text else " (sem texto: veja a imagem)") + (f" — {note}" if note else "")
        preview = make_preview(img, "image", None)
        a = Asset(project_id=project_id, kind="user_media", path=str(img), source="seu arquivo", license="próprio",
                  meta={"kind": "image", "filename": f"{name} — slide {i}", "description": desc, "duration": None,
                        "slide": i, "slides": len(files), "deck": name, "preview": str(preview) if preview else None})
        session.add(a)
        out.append(a)
    session.commit()
    for a in out:
        session.refresh(a)
    return out


def save_upload(session: Session, project_id: int, upload: UploadFile, description: str = "") -> list[Asset]:
    """One file → one library item; a PDF → one item per slide."""
    if Path(upload.filename or "").suffix.lower() == ".pdf":
        return save_pdf(session, project_id, upload, description)
    return [save_media(session, project_id, upload, description)]


def save_media(session: Session, project_id: int, upload: UploadFile, description: str = "") -> Asset:
    name = Path(upload.filename or "arquivo").name
    safe = re.sub(r"[^\w.\-]+", "_", name)[-80:] or "arquivo"
    kind = media_kind(Path(safe))
    if not kind:
        raise HTTPException(415, "envie vídeo (mp4, mov, webm), imagem (jpg, png, webp) ou slides em PDF")
    d = project_dir(project_id) / "media"
    d.mkdir(exist_ok=True)
    dest = d / safe
    n = 1
    while dest.exists():
        dest, n = d / f"{Path(safe).stem}_{n}{Path(safe).suffix}", n + 1
    with dest.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    if dest.suffix.lower() == ".heic":  # browsers and ffmpeg builds handle jpg better
        import subprocess

        jpg = dest.with_suffix(".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", str(dest), "--out", str(jpg)], capture_output=True)
        if jpg.exists():
            dest.unlink()
            dest = jpg
    return attach_file(session, project_id, dest, name, description)


def attach_file(session: Session, project_id: int, dest: Path, name: str, description: str = "",
                keep_audio: bool | None = None) -> Asset:
    """A file already on disk → one library item of the project (preview, duration, sound). keep_audio=True plays
    the file's own sound (a live's recording); None keeps the default (on for lesson/review styles when it talks)."""
    kind = media_kind(dest)
    duration = _duration(dest) if kind == "video" else None
    preview = make_preview(dest, kind, duration)
    sound = {}
    if kind == "video":  # video lessons: a recording with the user's voice keeps it by default (untick on the project)
        has = _has_speech(dest)
        project = session.get(Project, project_id)
        keep = keep_audio if keep_audio is not None else has and getattr(project, "style", None) in ("lesson", "review")
        sound = {"has_audio": has, "keep_audio": keep}
    a = Asset(project_id=project_id, kind="user_media", path=str(dest), source="seu arquivo", license="próprio",
              meta={"kind": kind, "filename": name, "description": description.strip(), "duration": duration,
                    "preview": str(preview) if preview else None, **sound})
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def manual_segments(ranges, duration: float | None = None) -> dict:
    """From–to ranges chosen by hand ([{start, end}] or [[start, end]], seconds) → the meta a video keeps: only those
    stretches play, in order, and the best-moments analysis never overwrites them (segments_auto False, like a
    Short pinned to one moment). Raises ValueError on an empty or impossible range."""
    out = []
    for r in ranges or []:
        start, end = (r.get("start"), r.get("end")) if isinstance(r, dict) else (r[0], r[1])
        start, end = float(start), float(end)
        if duration:
            end = min(end, float(duration))
        if start < 0 or end - start < 1:
            raise ValueError(f"trecho inválido: {start:g}–{end:g} s (o fim vem depois do início, com 1 s ou mais)")
        out.append({"start": round(start, 2), "end": round(end, 2)})
    if not out:
        raise ValueError("escolha pelo menos um trecho")
    out.sort(key=lambda g: g["start"])
    return {"segments": out, "segments_auto": False, "whole": False, "moments": []}


def refresh_highlights(project_id: int, force: bool = False) -> None:
    """Recount the best moments of a project's videos in the background (ffmpeg scans take a few seconds per
    clip). force: after a file was added/removed, since repeats are judged across the whole set."""
    import threading

    from cvm.visuals.highlights import refresh_project

    threading.Thread(target=refresh_project, args=(project_id, force), daemon=True).start()


def _after_change(session: Session, project_id: int) -> None:
    p = session.get(Project, project_id)
    if p and p.media_cut == "highlights":
        refresh_highlights(project_id, force=True)


def listing(session: Session, project_id: int) -> list[dict]:
    rows = session.exec(select(Asset).where(Asset.project_id == project_id, Asset.kind == "user_media")
                        .order_by(Asset.id)).all()
    return [{"id": a.id, **{k: v for k, v in a.meta.items() if k != "preview"}, "file_url": f"/assets/{a.id}/file"} for a in rows]


@router.get("/{project_id}/media")
def list_media(project_id: int, session: Session = Depends(get_session)):
    return listing(session, project_id)


@router.post("/{project_id}/media")
def upload_media(project_id: int, file: UploadFile = File(...), description: str = Form(""),
                 session: Session = Depends(get_session)):
    if not session.get(Project, project_id):
        raise HTTPException(404)
    items = save_upload(session, project_id, file, description)
    _after_change(session, project_id)
    a = items[0]
    return {"id": a.id, **a.meta, "file_url": f"/assets/{a.id}/file", "items": len(items)}


@router.patch("/{project_id}/media/{media_id}")
def describe_media(project_id: int, media_id: int, body: dict, session: Session = Depends(get_session)):
    a = session.get(Asset, media_id)
    if not a or a.project_id != project_id or a.kind != "user_media":
        raise HTTPException(404)
    meta = dict(a.meta)
    if "description" in body:
        meta["description"] = str(body.get("description") or "").strip()
    if "keep_audio" in body and meta.get("kind") == "video":
        meta["keep_audio"] = bool(body["keep_audio"])
    if "segments" in body and meta.get("kind") == "video":
        if body["segments"] is None:  # back to automatic (best moments worked out again when the project uses them)
            for k in ("segments", "segments_auto", "whole", "moments"):
                meta.pop(k, None)
        else:
            try:
                meta.update(manual_segments(body["segments"], meta.get("duration")))
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
    a.meta = meta
    session.add(a)
    session.commit()
    return {"id": a.id, **a.meta}


@router.delete("/{project_id}/media/{media_id}")
def delete_media(project_id: int, media_id: int, session: Session = Depends(get_session)):
    a = session.get(Asset, media_id)
    if not a or a.project_id != project_id or a.kind != "user_media":
        raise HTTPException(404)
    shared = session.exec(select(Asset).where(Asset.path == a.path, Asset.id != a.id)).first()
    if not shared:  # a live's recording is shared by its best-moments video and every Short (cvm/lives/package.py)
        Path(a.path).unlink(missing_ok=True)
        if a.meta.get("preview"):
            Path(a.meta["preview"]).unlink(missing_ok=True)
    session.delete(a)
    session.commit()
    _after_change(session, project_id)
    return {"deleted": media_id}


def use_on_scene(session: Session, project_id: int, scene: int, media: Asset) -> Asset:
    """Make a library item the visual of one scene (the composer picks it up on the next composing)."""
    v = session.exec(select(Asset).where(Asset.project_id == project_id, Asset.kind == "visual", Asset.scene == scene)).first()
    if not v:
        v = Asset(project_id=project_id, kind="visual", scene=scene, path="", meta={})
    v.path, v.source, v.license = media.path, "seu arquivo", "próprio"
    v.meta = {**(v.meta or {}), "kind": media.meta["kind"], "media_id": media.id, "credit": None,
              "hold_last_frame": media.meta["kind"] == "video", "fit": media.meta["kind"] == "video", "start": 0}
    session.add(v)
    session.commit()
    session.refresh(v)
    return v


@router.post("/{project_id}/visuals/{scene}/upload")
def upload_to_scene(project_id: int, scene: int, file: UploadFile = File(...), description: str = Form(""),
                    session: Session = Depends(get_session)):
    media = save_media(session, project_id, file, description)
    return use_on_scene(session, project_id, scene, media)
