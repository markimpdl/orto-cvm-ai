"""A finished video is also saved as a readable folder (2026-11-23, like the user's friend's clips): one folder per
video, named by date + id + title, with the video, the cover and publicacao.md (title, description, tags, chapters,
credit, schedule), so publishing by hand needs only Finder."""
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from cvm import export_folder
from cvm.models import Channel, Platform, Project, Render


def _db(tmp_path):
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    s = Session(e)
    s.add(Channel(id=1, name="Cozinha da Vila", platform=Platform.youtube, language="pt-BR"))
    s.add(Project(id=7, channel_id=1, title="🟡 Short 2: Bolo de caneca em 3 minutos DEU CERTO!", topic="t", languages=["pt-BR"]))
    video = tmp_path / "7.mp4"
    video.write_bytes(b"video")
    cover = tmp_path / "thumbnail.jpg"
    cover.write_bytes(b"jpg")
    s.add(Render(id=1, project_id=7, language="pt-BR", profile="youtube_shorts", path=str(video), status="done",
                 meta={"thumbnail": str(cover), "chapters": [{"t": 0, "title": "Abertura"}]},
                 seo={"title": "Bolo de caneca em 3 minutos deu certo #shorts", "description": "Desc.\n\nCrédito: Canal Parceiro",
                      "tags": ["bolo de caneca", "receita rapida"], "hashtags": ["#shorts", "#receita"]}))
    s.commit()
    return s


def test_export_writes_video_cover_and_publishing_text(tmp_path):
    s = _db(tmp_path)
    folder = export_folder.export_project(s, s.get(Project, 7), root=tmp_path / "out")
    assert folder.parent.name == "Cozinha da Vila"
    assert "7-bolo-de-caneca-em-3-minutos-deu-certo" in folder.name and "🟡" not in folder.name
    files = {p.name for p in folder.iterdir()}
    assert any(n.endswith(".mp4") for n in files) and "capa.jpg" in files and "publicacao.md" in files
    md = (folder / "publicacao.md").read_text()
    assert "Bolo de caneca em 3 minutos deu certo #shorts" in md and "bolo de caneca, receita rapida" in md and "Canal Parceiro" in md


def test_export_is_idempotent(tmp_path):
    s = _db(tmp_path)
    a = export_folder.export_project(s, s.get(Project, 7), root=tmp_path / "out")
    b = export_folder.export_project(s, s.get(Project, 7), root=tmp_path / "out")
    assert a == b and len(list(a.parent.iterdir())) == 1


def test_video_reaching_review_is_exported(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import cvm.workers.tasks as tasks

    s = _db(tmp_path)
    seen = []
    monkeypatch.setattr(export_folder, "export_project", lambda session, project, root=None: seen.append(project.id))
    tasks.export_ready(s, SimpleNamespace(project_id=7, state="awaiting_publish_review"))
    tasks.export_ready(s, SimpleNamespace(project_id=7, state="awaiting_approval"))  # script gate: nothing yet
    assert seen == [7]


def test_export_failure_never_stops_the_queue(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import cvm.workers.tasks as tasks

    s = _db(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(export_folder, "export_project", boom)
    tasks.export_ready(s, SimpleNamespace(project_id=7, state="awaiting_publish_review"))  # no exception
