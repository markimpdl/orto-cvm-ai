"""Video lessons: PDF slides become ordered images, recorded parts keep their own sound, no bumper."""

import io
import shutil
import subprocess

import numpy as np
import pytest
from fastapi import UploadFile
from sqlmodel import Session, SQLModel, create_engine

from cvm.api import media
from cvm.config import settings
from cvm.models import Asset, Channel, Platform, Project, Style

needs_poppler = pytest.mark.skipif(not shutil.which("pdftoppm"), reason="poppler not installed")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    p = Project(title="Aula", topic="Excel — Objetivo da aula: fazer um orçamento", style=Style.lesson)
    s.add(p)
    s.commit()
    return s, p


@needs_poppler
def test_pdf_becomes_one_image_per_slide_in_order(db, tmp_path):
    from PIL import Image

    s, p = db
    pages = [Image.new("RGB", (800, 450), c) for c in ("red", "green", "blue")]
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:])
    buf.seek(0)
    items = media.save_upload(s, p.id, UploadFile(buf, filename="Aula 1.pdf"))
    assert [a.meta["slide"] for a in items] == [1, 2, 3]
    assert all(a.meta["kind"] == "image" and a.path.endswith(".png") for a in items)
    assert items[1].meta["description"].startswith("Slide 2/3 de Aula 1.pdf")
    assert [m["id"] for m in media.listing(s, p.id)] == [a.id for a in items]


def _clip(path, seconds=3, audio=True):
    cmd = [str(settings.ffmpeg_bin), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s=320x180:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest"]
    subprocess.run([*cmd, str(path)], check=True)


def test_recorded_part_keeps_its_own_audio(db, tmp_path):
    from cvm.voice.narrate import SR, original_audio

    s, p = db
    clip = tmp_path / "aula.mp4"
    _clip(clip, 4)
    with clip.open("rb") as f:
        a = media.save_media(s, p.id, UploadFile(f, filename="aula.mp4"))
    assert a.meta["has_audio"] and a.meta["keep_audio"]  # lesson project: kept by default
    scene = {"index": 1, "narration": "", "target_seconds": 2.0, "visual": {"type": "user_media", "media_id": a.id, "media_start": 1.0}}
    audio = original_audio(s, scene)
    assert audio is not None and abs(len(audio) / SR - 2.0) < 0.1 and np.abs(audio).max() > 0.01
    assert original_audio(s, {**scene, "visual": {**scene["visual"], "keep_audio": False}}) is None  # the scene says no
    a.meta = {**a.meta, "keep_audio": False}
    s.add(a)
    s.commit()
    assert original_audio(s, scene) is None  # the user unticked it: narrated as usual


def test_silent_clip_is_not_marked_and_other_styles_stay_muted(db, tmp_path):
    s, p = db
    clip = tmp_path / "mudo.mp4"
    _clip(clip, 2, audio=False)
    with clip.open("rb") as f:
        a = media.save_media(s, p.id, UploadFile(f, filename="mudo.mp4"))
    assert not a.meta["has_audio"] and not a.meta["keep_audio"]
    other = Project(title="v", topic="v", style=Style.explainer)
    s.add(other)
    s.commit()
    loud = tmp_path / "som.mp4"
    _clip(loud, 2)
    with loud.open("rb") as f:
        b = media.save_media(s, other.id, UploadFile(f, filename="som.mp4"))
    assert b.meta["has_audio"] and not b.meta["keep_audio"]


def test_lesson_channel_has_no_bumper():
    from cvm.render import bumper

    ch = Channel(id=1, name="Aulas Hub", platform=Platform.youtube, prefs={"bumper_mode": "none"})
    assert bumper.mode(ch) == "none"
    assert not bumper.wants_bumper(ch, "youtube", None)


def test_recorded_scene_may_pass_45_seconds():
    from cvm.scripting.schema import ScriptContent

    base = {"hook": "Nesta aula você vai aprender a fazer um orçamento.", "blocks": [{"title": "Parte 1", "scenes": [0, 1]}]}
    rec = {"index": 1, "narration": "", "target_seconds": 120, "visual": {"type": "user_media", "media_id": 5, "keep_audio": True}}
    slide = {"index": 0, "narration": "Olha esse slide.", "target_seconds": 10, "visual": {"type": "user_media", "media_id": 4}}
    ScriptContent.model_validate({**base, "scenes": [slide, rec]})
    with pytest.raises(ValueError):
        ScriptContent.model_validate({**base, "scenes": [slide, {**rec, "visual": {"type": "user_media", "media_id": 5}}]})


def test_opening_choices_per_video_over_channel_default(tmp_path, monkeypatch):
    from cvm.render import bumper

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    off = Channel(id=1, name="Aulas Hub", platform=Platform.youtube, prefs={"bumper_mode": "none", "hook": False})
    std = Channel(id=2, name="Studio", platform=Platform.youtube, prefs={})
    assert not bumper.hook_on(Project(title="a", topic="a"), off) and not bumper.bumper_on(Project(title="a", topic="a"), off)
    assert bumper.hook_on(Project(title="a", topic="a"), std) and bumper.bumper_on(Project(title="a", topic="a"), std)
    on = Project(title="a", topic="a", hook=True, bumper=True)
    assert bumper.hook_on(on, off) and bumper.bumper_on(on, off)
    assert not bumper.wants_bumper(off, "youtube", None, on)  # on, but no logo anywhere yet
    logo = tmp_path / "channels" / "2" / "brand" / "logo.png"
    logo.parent.mkdir(parents=True)
    from PIL import Image

    Image.new("RGBA", (64, 64), "white").save(logo)
    borrow = Channel(id=1, name="Aulas Hub", platform=Platform.youtube, prefs={"bumper_mode": "none", "bumper_from": 2})
    assert bumper.brand_id(borrow) == 2 and bumper.wants_bumper(borrow, "youtube", None, on)  # another channel's bumper
    assert not bumper.wants_bumper(borrow, "youtube", None, Project(title="a", topic="a"))  # default stays off
