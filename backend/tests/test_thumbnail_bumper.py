import subprocess
from types import SimpleNamespace

from PIL import Image

from cvm.config import settings
from cvm.media import duration
from cvm.render import bumper, thumbnail
from cvm.scripting.schema import ScriptContent


def _png(path, size=(1344, 768), colour=(30, 90, 160)):
    Image.new("RGB", size, colour).save(path)
    return path


def _cutout(path):
    img = Image.new("RGBA", (500, 700), (0, 0, 0, 0))
    img.paste((200, 150, 120, 255), (100, 100, 400, 700))
    img.save(path)
    return path


def test_thumbnail_with_presenter_and_text(tmp_path):
    out = thumbnail.build(_png(tmp_path / "bg.png"), "Voltou brutal 🔥", tmp_path / "t.jpg", True, _cutout(tmp_path / "c.png"))
    img = Image.open(out)
    assert img.size == (1280, 720)
    # presenter on the right (with its white rim), text side darkened on the left
    assert img.getpixel((1100, 600))[0] > 150
    assert sum(img.getpixel((5, 5))) < sum((30, 90, 160))


def test_thumbnail_vertical_and_arabic(tmp_path):
    v = thumbnail.build(_png(tmp_path / "bg.png", (768, 1344)), "ninguém viu isso", tmp_path / "v.jpg", False, None)
    assert Image.open(v).size == (1080, 1920)
    a = thumbnail.build(_png(tmp_path / "bg2.png"), "رجعت أقوى", tmp_path / "a.jpg", True, None)
    assert Image.open(a).size == (1280, 720)


def test_packaging_contract():
    c = ScriptContent.model_validate({"hook": "h", "intro_end": 2, "scenes": [{"index": 1, "narration": "n", "visual": {"type": "broll"}}],
                                      "packaging": {"title": "T", "thumbnail_text": "BIG", "alternatives": [{"title": "T2", "thumbnail_text": "B2"}]}})
    assert c.packaging.thumbnail_side == "right" and c.intro_end == 2


def test_bumper_only_on_long_youtube_not_kids(monkeypatch):
    monkeypatch.setattr("cvm.render.bumper_brand.brand_files", lambda cid: (1, 2) if cid == 4 else None)
    monkeypatch.setattr("cvm.render.bumper_brand.logo_file", lambda cid: 2 if cid in (4, 6) else None)
    ch = SimpleNamespace(id=4, made_for_kids=False)
    assert not bumper.wants_bumper(SimpleNamespace(id=5, made_for_kids=False), "youtube", None)
    assert bumper.wants_bumper(ch, "youtube", None)
    assert not bumper.wants_bumper(ch, "youtube_shorts", None)
    assert not bumper.wants_bumper(ch, "youtube", (1, [1, 2]))
    assert bumper.wants_bumper(SimpleNamespace(id=4, made_for_kids=True), "youtube", None)  # kids too (user's choice)
    # short bumper (default) needs only the logo; the old spoken one needs banner + logo
    assert bumper.wants_bumper(SimpleNamespace(id=6, prefs={}), "youtube", None)
    assert not bumper.wants_bumper(SimpleNamespace(id=6, prefs={"bumper_mode": "full"}), "youtube", None)
    custom = SimpleNamespace(prefs={"bumper": {"pt-BR": {"say": "Bora!"}}})
    assert bumper.texts(custom, "pt-BR")["say"] == "Bora!" and bumper.texts(custom, "pt-BR")["like"] == "DEIXE SEU LIKE"


def _clip(path, seconds, colour):
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s=320x180:r=30:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(path)], check=True)
    return path


def test_splice_inserts_bumper(tmp_path):
    final, b = _clip(tmp_path / "f.mp4", 6, "red"), _clip(tmp_path / "b.mp4", 2, "blue")
    out, added = bumper.splice(final, b, 2.5, tmp_path / "o.mp4", "2M", 30)
    assert abs(added - (2 - 0.35 - 0.45)) < 0.05
    assert abs(duration(out) - (6 + added)) < 0.3


def test_send_to_revision_feeds_the_scriptwriter():
    from cvm import pipeline
    from cvm.models import Job
    from cvm.scripting.claude_runner import build_prompt

    j = Job(id=1, project_id=1, state="voicing", history=[])
    pipeline.send_to_revision(j, "add packaging")
    assert j.state == "scripting" and "REVISION" in build_prompt(j) and "add packaging" in build_prompt(j)
    done = Job(id=2, project_id=2, state="published", history=[])
    try:
        pipeline.send_to_revision(done, "x")
        raise AssertionError
    except pipeline.InvalidTransition:
        pass


def test_bumper_point():
    scenes = [{"index": i, "start": s} for i, s in enumerate([0, 8, 19, 30, 52, 70])]
    assert bumper.bumper_point(scenes, 1, 100) == 19  # right after the intro
    assert bumper.bumper_point(scenes, None, 100) == 19  # no intro marked: first boundary in 15–45 s
    assert bumper.bumper_point(scenes, 5, 100) is None  # nothing after the intro


def test_background_uses_a_still_scene_image_as_is(tmp_path, monkeypatch):
    from cvm.visuals import sources

    monkeypatch.setattr(sources, "image_engine", lambda *a, **k: None)
    still = _png(tmp_path / "scene.jpg")
    assert thumbnail.background(1, None, True, 101, "left", still) == still
