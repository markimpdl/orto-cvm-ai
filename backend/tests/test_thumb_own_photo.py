"""A thumbnail background that already shows the presenter (his selfie with the giraffe, 2026-11-22) skips the
cut-out: packaging.thumbnail_presenter = false."""
from pathlib import Path
from types import SimpleNamespace

from cvm.render import thumbnail


def _run(monkeypatch, tmp_path, pkg):
    seen = []
    bg = tmp_path / "selfie.jpg"
    bg.write_bytes(b"x")
    monkeypatch.setattr(thumbnail, "presenter_cutout", lambda aid: tmp_path / "cut.png")
    monkeypatch.setattr(thumbnail, "background", lambda *a, **k: bg)
    monkeypatch.setattr(thumbnail, "_sticker_style", lambda p: None)
    monkeypatch.setattr(thumbnail, "build", lambda bg, t, out, landscape, cut, side, sticker=None: seen.append(cut) or out)
    content = {"packaging": {"thumbnail_text": "DÁ PRA TOCAR", "thumbnail_scene": 3, **pkg}, "scenes": []}
    thumbnail.make_thumbnails(SimpleNamespace(id=1, avatar_id=1), content, "pt-BR", Path("clip.mp4"), tmp_path, True,
                              variants=False, scene_media={3: bg})
    return seen


def test_own_photo_skips_the_cutout(tmp_path, monkeypatch):
    assert _run(monkeypatch, tmp_path, {"thumbnail_presenter": False}) == [None]


def test_cutout_by_default(tmp_path, monkeypatch):
    assert _run(monkeypatch, tmp_path, {}) == [tmp_path / "cut.png"]
