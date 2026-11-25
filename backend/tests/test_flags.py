"""National flags never come from an AI model: official Wikimedia Commons flag, or real stock / a video frame."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from cvm.config import settings
from cvm.render import thumbnail
from cvm.scripting.schema import Visual
from cvm.visuals import flags, judge, select, sources


def _uae_png(path: Path) -> Path:
    """A 2:1 UAE-like flag: red hoist band + green/white/black stripes."""
    img = Image.new("RGB", (800, 400), (255, 255, 255))
    img.paste((0, 115, 47), (200, 0, 800, 133))
    img.paste((0, 0, 0), (200, 267, 800, 400))
    img.paste((200, 16, 46), (0, 0, 200, 400))
    img.save(path)
    return path


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """data_dir in tmp; the Commons download returns a local PNG (no network). Records the requested codes."""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    asked = []

    def fake_get(url, **kw):
        asked.append(url)
        content = (_uae_png(tmp_path / "src.png")).read_bytes()
        return SimpleNamespace(content=content, raise_for_status=lambda: None)

    monkeypatch.setattr(flags.httpx, "get", fake_get)
    return asked


@pytest.fixture
def no_ai(monkeypatch):
    """Any AI drawing fails the test."""
    def boom(*a, **k):
        raise AssertionError("AI model asked to draw a flag")

    monkeypatch.setattr(sources, "ai_image", boom)
    monkeypatch.setattr(sources, "image_engine", lambda *a, **k: "flux")
    monkeypatch.setattr(thumbnail, "presenter_cutout", lambda *_: None)


# ---------- detection ----------

@pytest.mark.parametrize("text", [
    "UAE flag waving over the Dubai skyline", "bandeira dos Emirados Árabes Unidos", "las banderas de México",
    "national colors of Brazil", "Saudi banner on a pole", "علم الإمارات يرفرف", "العلم الإماراتي", "أعلام الدول",
    "رفع علم الدولة", "flags of the world",
])
def test_mentions_flag(text):
    assert flags.mentions_flag(text)


@pytest.mark.parametrize("text", [
    "flagship store at night", "red flags in a relationship", "bandeira vermelha no namoro", "a desert at dawn",
    "العلم والتكنولوجيا في الإمارات", "طالب العلم", "", None, "banner ad on a website",
])
def test_no_false_flag(text):
    assert not flags.mentions_flag(text)


@pytest.mark.parametrize("text,code", [
    ("UAE flag", "AE"), ("bandeira dos Emirados", "AE"), ("Emirati national day", "AE"), ("علم الإمارات", "AE"),
    ("بالإمارات", "AE"), ("Saudi Arabia", "SA"), ("bandeira do Brasil", "BR"), ("the US flag", "US"),
    ("Estados Unidos", "US"), ("bandeira do Irã", "IR"), ("Omã", "OM"), ("UK flag", "GB"), ("Reino Unido", "GB"),
    ("Coreia do Sul", "KR"), ("África do Sul", "ZA"), ("علم مصر", "EG"), ("الهند", "IN"), ("Palestina", "PS"),
])
def test_country_of(text, code):
    assert flags.country_of(text) == code


@pytest.mark.parametrize("text", ["ele usa a bandeira", "a ira dele", "América Latina", "North Korea", "South Sudan",
                                  "مصرف", "هند", "a desert at dawn"])
def test_country_not_guessed(text):
    assert flags.country_of(text) is None


def test_every_country_has_a_commons_file():
    assert set(flags._LATIN) == set(flags.COMMONS_FILES) == set(flags._ARABIC)
    assert flags.COMMONS_FILES["AE"] == "Flag_of_the_United_Arab_Emirates.svg"
    assert flags.COMMONS_FILES["US"] == "Flag_of_the_United_States.svg"


def test_scene_flag():
    sc = lambda v, **kw: {"index": 1, "narration": kw.get("narration", ""), "visual": {"type": "ai_image", **v}}  # noqa: E731
    assert flags.scene_flag(sc({"flag": "ae"})) == (True, "AE")
    assert flags.scene_flag(sc({"image_prompt": "UAE flag on a tower"})) == (True, "AE")
    assert flags.scene_flag(sc({"image_prompt": "a flag on a tower"}, narration="Os Emirados celebram")) == (True, "AE")
    # narration naming two countries: unknown, never a guess
    assert flags.scene_flag(sc({"image_prompt": "a flag"}, narration="Brasil e Portugal")) == (True, None)
    assert flags.scene_flag(sc({"image_prompt": "desert at dawn"})) == (False, None)
    assert Visual(type="ai_image", flag="AE").flag == "AE"  # allowed by the script schema


# ---------- official flag frame ----------

@pytest.mark.parametrize("portrait,size", [(True, (1080, 1920)), (False, (1920, 1080))])
def test_official_flag_frame(offline, tmp_path, portrait, size):
    out = flags.official_flag("AE", tmp_path / "f.png", portrait)
    img = Image.open(out).convert("RGB")
    assert img.size == size
    assert "Flag_of_the_United_Arab_Emirates.svg" in offline[0]
    assert (settings.data_dir / "flags" / "AE.png").exists()  # cached
    W, H = size
    # the flag sits flat in the centre at 2:1 — red hoist on the left, white middle stripe, never stretched
    fw = round(min(W * (0.86 if portrait else 0.7), H * 0.7 * 2))
    x0 = (W - fw) // 2
    r, g, b = img.getpixel((x0 + fw // 10, H // 2))
    assert r > 150 and g < 80
    assert min(img.getpixel((x0 + fw * 3 // 4, H // 2))) > 230
    top = (H - fw // 2) // 2
    assert img.getpixel((x0 + fw * 3 // 4, top + fw // 2 // 6))[1] > 90  # green on top
    assert sum(img.getpixel((W // 2, 10))) < 200  # dark backdrop
    flags.official_flag("AE", tmp_path / "g.png", portrait)
    assert len(offline) == 1  # second frame uses the cache


def test_official_flag_offline_or_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")

    def down(*a, **k):
        raise flags.httpx.ConnectError("offline")

    monkeypatch.setattr(flags.httpx, "get", down)
    assert flags.official_flag("AE", tmp_path / "f.png", True) is None
    assert flags.official_flag("XX", tmp_path / "f.png", True) is None
    assert flags.official_flag(None, tmp_path / "f.png", True) is None


# ---------- scenes ----------

def _scene(visual, **kw):
    return {"index": 3, "narration": kw.get("narration", "Hoje é o Dia Nacional"), "on_screen_text": kw.get("ost"),
            "visual": {"type": "ai_image", "search_terms": [], **visual}}


def test_ai_image_uses_the_official_flag(offline, no_ai, tmp_path):
    got = select._ai_image(_scene({"image_prompt": "UAE flag waving over Dubai, golden hour"}), True, tmp_path)
    assert got["source"] == flags.SOURCE and got["flag"] == "AE" and got["license"] == "public domain"
    assert Image.open(got["path"]).size == (1080, 1920)
    assert not sources.is_ai_still(got["source"])  # so it is never animated, upscaled or judged as AI
    got = select._ai_image(_scene({"flag": "AE", "_kids": True}), False, tmp_path)  # kids: same rule
    assert got["flag"] == "AE" and Image.open(got["path"]).size == (1920, 1080)


def test_unknown_flag_falls_to_stock(offline, no_ai, monkeypatch, tmp_path):
    sc = _scene({"image_prompt": "a flag waving in the wind", "search_terms": ["flag waving"]}, narration="Brasil e Portugal")
    assert select._ai_image(sc, True, tmp_path) is None
    monkeypatch.setattr(select, "_broll", lambda *a, **k: {"kind": "video", "path": "stock.mp4", "source": "pexels"})
    assert select.pick(sc, [], True, tmp_path)["source"] == "pexels"


def test_pick_prefers_the_official_flag_over_stock(offline, no_ai, monkeypatch, tmp_path):
    monkeypatch.setattr(select, "_broll", lambda *a, **k: pytest.fail("stock searched for a known flag"))
    got = select.pick(_scene({"type": "broll", "search_terms": ["UAE flag waving"]}), [], True, tmp_path)
    assert got["type"] == "broll" and got["flag"] == "AE"


def test_non_flag_scene_still_uses_ai(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "image_engine", lambda *a, **k: "flux")
    monkeypatch.setattr(sources, "ai_image", lambda prompt, out, *a, **k: "flux-schnell (local)")
    got = select._ai_image(_scene({"image_prompt": "desert dunes at dawn"}), True, tmp_path)
    assert sources.is_ai_still(got["source"])


def test_judge_never_reviews_the_official_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "judge", lambda items, cartoon=False: pytest.fail("official flag sent to the judge"))
    chosen = [{"_scene": 1, "kind": "image", "path": "f.png", "source": flags.SOURCE, "flag": "AE"}]
    out = judge.review(chosen, {1: {"index": 1, "visual": {}}}, lambda sc, seed: None, lambda sc: None, False, tmp_path)
    assert out == chosen
    assert not judge.verdict_ok({"score": 7, "problems": ["a flag with wrong colours"]})


# ---------- thumbnails ----------

def test_thumbnail_flag_prompt_uses_official_flag(offline, no_ai, monkeypatch, tmp_path):
    monkeypatch.setattr(thumbnail, "project_dir", lambda pid: tmp_path / f"p{pid}")
    bg = thumbnail.background(1, "giant UAE flag over the Burj Khalifa", True, 101, "left", tmp_path / "x.png")
    assert bg.name.startswith("flag_AE") and Image.open(bg).size == (1920, 1080)


def test_thumbnail_unknown_flag_uses_video_frame(offline, no_ai, monkeypatch, tmp_path):
    monkeypatch.setattr(thumbnail, "project_dir", lambda pid: tmp_path / f"p{pid}")
    still = _uae_png(tmp_path / "scene.png")
    assert thumbnail.background(1, "a flag waving in the wind", True, 101, "left", still) == still


def test_make_thumbnails_never_draws_a_flag(offline, no_ai, monkeypatch, tmp_path):
    monkeypatch.setattr(thumbnail, "project_dir", lambda pid: tmp_path / f"p{pid}")
    project = SimpleNamespace(id=2, avatar_id=None)
    content = {"packaging": {"title": "t", "thumbnail_text": "DIA NACIONAL", "thumbnail_prompt": "bandeira dos Emirados"},
               "scenes": [{"on_screen_text": "x"}], "hook": "h"}
    outs = thumbnail.make_thumbnails(project, content, "pt-BR", _uae_png(tmp_path / "scene.png"), tmp_path, True, False)
    assert Image.open(outs[0]).size == (1280, 720)
