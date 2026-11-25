from pathlib import Path

from cvm.config import settings


def project_dir(project_id: int, language: str | None = None) -> Path:
    d = settings.data_dir / "projects" / str(project_id)
    if language:
        d = d / language
    d.mkdir(parents=True, exist_ok=True)
    return d


def avatar_dir(avatar_id: int) -> Path:
    d = settings.data_dir / "avatars" / str(avatar_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ids are reused after a project is deleted, so the browser must revalidate files (ETag) instead of
# showing a cached file of a deleted project under the same URL
NO_CACHE = {"Cache-Control": "no-cache"}
