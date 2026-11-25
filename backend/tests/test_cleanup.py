import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from cvm import cleanup
from cvm.config import settings
from cvm.models import Asset, Project, Publication, Render, Script


@pytest.fixture
def s(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _file(path, size=2_000_000):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def _project(s, tmp_path, published_days_ago, second_render_published=True):
    p = Project(title="v", topic="t")
    s.add(p)
    s.commit()
    root = tmp_path / "projects" / str(p.id)
    when = datetime.now(timezone.utc) - timedelta(days=published_days_ago)
    for lang, published in (("pt-BR", True), ("en-US", second_render_published)):
        r = Render(project_id=p.id, language=lang, profile="youtube", status="done",
                   path=str(_file(root / lang / "youtube" / f"{p.id}_{lang}.mp4")),
                   meta={"thumbnails": [str(_file(root / lang / "youtube" / "thumbnail.jpg", 300_000))]},
                   seo={"title": "T", "tags": ["a"]})
        s.add(r)
        s.commit()
        s.add(Publication(render_id=r.id, channel_id=1, status="published" if published else "ready_for_manual_publish",
                          url="https://youtu.be/x", published_at=when if published else None))
    _file(root / "pt-BR" / "voice" / "narration.wav", 5_000_000)
    clip = _file(root / "pt-BR" / "avatar" / "avatar_01.mp4")
    s.add(Asset(project_id=p.id, kind="lipsync", path=str(clip)))
    _file(root / "visuals" / "ltx_scene_01.mp4")
    _file(root / "visuals" / "flux_scene_01.png", 3_000_000)
    _file(root / "media" / "meu_video.mp4")
    _file(root / "visuals" / "tiny.mp4", 1000)
    s.add(Script(project_id=p.id, language="pt-BR", content={"scenes": [{"narration": "oi"}]}))
    s.commit()
    return p, root


def test_only_big_videos_of_old_published_projects_go(s, tmp_path):
    p, root = _project(s, tmp_path, published_days_ago=30)
    assert cleanup.preview(s, 14)["projects"][0]["bytes"] == 4 * 2_000_000  # 2 renders + avatar + ltx clip
    out = cleanup.run(s, [p.id], 14)
    assert out["removed_bytes"] == 8_000_000
    assert not (root / "pt-BR" / "youtube" / f"{p.id}_pt-BR.mp4").exists()
    assert not (root / "visuals" / "ltx_scene_01.mp4").exists()
    for kept in ("pt-BR/voice/narration.wav", "pt-BR/youtube/thumbnail.jpg", "visuals/flux_scene_01.png",
                 "media/meu_video.mp4", "visuals/tiny.mp4"):
        assert (root / kept).exists(), kept
    hist = json.loads((root / "historico.json").read_text())
    assert hist["renders"][0]["seo"]["tags"] == ["a"] and hist["renders"][0]["publications"][0]["url"]
    assert hist["scripts"]["pt-BR"]["content"]["scenes"][0]["narration"] == "oi"
    s.expire_all()
    assert all("video_removed" in r.meta for r in s.exec(select(Render)).all())
    assert cleanup.preview(s, 14)["projects"] == []


def test_recent_or_unpublished_videos_stay(s, tmp_path):
    _project(s, tmp_path, published_days_ago=2)
    assert cleanup.preview(s, 14)["projects"] == []
    p, root = _project(s, tmp_path, published_days_ago=30, second_render_published=False)
    plan = cleanup.preview(s, 14)["projects"]
    assert [f["kind"] for f in plan[0]["files"]] == ["render"]  # the pending render keeps the project's clips
    assert plan[0]["kept_renders"][0]["reason"] == "publicação pendente"
    cleanup.run(s, [p.id], 14)
    assert (root / "en-US" / "youtube" / f"{p.id}_en-US.mp4").exists()
    assert (root / "pt-BR" / "avatar" / "avatar_01.mp4").exists()
