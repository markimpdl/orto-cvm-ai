"""Phases 5–6: retention structure, short bumper, clean end screen, chapters, Shorts opening, thumbnails."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from cvm.config import settings
from cvm.media import duration
from cvm.render import bumper, chapters, compose, thumbnail
from cvm.render.captions import write_ass
from cvm.scripting.schema import Packaging, ScriptContent, fit_thumbnail_text, thumbnail_text_problems


def _scene(i, secs=6.0):
    return {"index": i, "narration": f"cena {i}", "visual": {"type": "broll"}, "target_seconds": secs}


OLD = {"hook": "h", "intro_end": 1, "scenes": [_scene(1), _scene(2)],
       "packaging": {"title": "GTA 6 voltou", "thumbnail_text": "GTA 6 VOLTOU MESMO",
                     "alternatives": [{"title": "x", "thumbnail_text": "um dois tres quatro"}]}}


def _new(**kw):
    c = {"hook": "O GTA 6 vai rodar a 30 FPS?", "hook_type": "question", "promise": "no final eu mostro", "intro_end": 2,
         "scenes": [_scene(i) for i in range(1, 7)],
         "blocks": [{"title": "O motor", "scenes": [3, 4], "question": "q", "reveal": "r", "continuity": "c"},
                    {"title": "O console", "scenes": [5, 6]}],
         "cta_points": [{"scene": 4, "at": "mid", "text": "se inscreve"}, {"scene": 6, "at": "end"}],
         "ending": {"answer": "a", "comment_question": "e você?", "next_video": "assista este"},
         "packaging": {"title": "GTA 6 voltou", "thumbnail_text": "30 FPS?"}}
    c.update(kw)
    return c


def test_old_scripts_stay_valid():
    c = ScriptContent.model_validate(OLD)
    assert c.blocks == [] and c.cta_points == [] and c.promise is None and c.ending is None
    assert c.packaging.thumbnail_text == "MESMO"  # fitted: title words dropped, ≤ 3 words
    assert c.packaging.alternatives[0].thumbnail_text == "um dois tres"


def test_new_structure_validated():
    c = ScriptContent.model_validate(_new())
    assert c.hook_type == "question" and c.blocks[0].title == "O motor" and c.cta_points[1].at == "end"
    with pytest.raises(ValidationError, match="repete o título"):
        ScriptContent.model_validate(_new(packaging={"title": "GTA 6 voltou", "thumbnail_text": "GTA 6 VOLTOU"}))
    with pytest.raises(ValidationError, match="palavras"):
        ScriptContent.model_validate(_new(packaging={"title": "T", "thumbnail_text": "um dois tres quatro"}))
    with pytest.raises(ValidationError, match="não existem"):
        ScriptContent.model_validate(_new(cta_points=[{"scene": 99}]))
    long = _new()
    long["scenes"][3]["target_seconds"] = 50
    with pytest.raises(ValidationError, match="45"):
        ScriptContent.model_validate(long)
    with pytest.raises(ValidationError):  # save_packaging (new packaging) is strict
        Packaging.model_validate({"title": "GTA 6 voltou", "thumbnail_text": "GTA 6"})


def test_thumbnail_text_rule_helpers():
    assert thumbnail_text_problems("NINGUÉM VIU ISSO", "GTA 6 voltou") == []
    assert thumbnail_text_problems("رجعت أقوى", "رجعت اللعبة أقوى") != []
    assert fit_thumbnail_text("VOLTOU BRUTAL DEMAIS AGORA", "GTA 6 voltou") == "BRUTAL DEMAIS AGORA"


def test_short_bumper_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    brand = tmp_path / "channels" / "7" / "brand"
    brand.mkdir(parents=True)
    Image.new("RGB", (200, 200), (200, 40, 40)).save(brand / "logo.png")
    ch = SimpleNamespace(id=7, prefs={}, made_for_kids=True)  # kids too: no spoken line, so no GenericBumper
    assert bumper.mode(ch) == "short" and bumper.wants_bumper(ch, "youtube", None)
    b = bumper.bumper_for(None, ch, "ar-AE", 320, 180, 30)
    assert b.name.startswith("short_") and duration(b) <= 3.0
    assert bumper.bumper_for(None, ch, "pt-BR", 320, 180, 30) == b  # same bumper in every language (cached)
    music = tmp_path / "tune.wav"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=8", str(music)], check=True)
    ch.prefs = {"bumper_music": str(music)}
    assert abs(duration(bumper.bumper_for(None, ch, "ar-AE", 320, 180, 30)) - bumper.MUSIC_MAX_S) < 0.15
    assert bumper.mode(SimpleNamespace(prefs={"bumper_mode": "full"})) == "full"


def test_end_screen_plan_keeps_last_20s_clean():
    overlays = [{"s": 50.0, "e": 60.4, "style": "Credit"}, {"s": 10.0, "e": 14.0, "style": "Title"}]
    tail = compose.end_screen_plan(60.4, 60.0, overlays)
    assert abs(tail - 19.6) < 1e-6 and overlays[0]["e"] == 60.0 and overlays[1]["e"] == 14.0
    assert compose.end_screen_plan(100.0, 70.0, []) == 0.0  # narration already ends 30 s before the end


def test_end_screen_tail_moves(tmp_path):
    src = tmp_path / "v.mp4"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=2",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    tail = compose._end_screen_tail(src, 6, 320, 180, 30, tmp_path / "t.mp4")
    assert abs(duration(tail) - 6) < 0.1
    log = subprocess.run([settings.ffmpeg_bin, "-hide_banner", "-i", str(tail), "-vf", "freezedetect=n=0.003:d=4",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    assert "freeze_duration" not in log  # render QA must not call it a frozen picture


def test_short_hook_text():
    c = {"hook": "uma frase de gancho bem curta", "packaging": {"thumbnail_text": "30 FPS?"}}
    assert compose.short_hook_text(c, {"on_screen_text": "VAI RODAR?"}, True) == "VAI RODAR?"
    assert compose.short_hook_text(c, {}, True) == "30 FPS?"
    assert compose.short_hook_text(c, {}, False) is None  # a cut uses its own first scene only


def test_chapters_from_blocks():
    starts = {1: 0, 2: 4, 3: 14, 4: 50, 5: 95, 6: 140, 7: 145}
    script = {"blocks": [{"title": "Motor", "scenes": [3, 4]}, {"title": "Console", "scenes": [5]},
                         {"title": "Curto", "scenes": [6]}, {"title": "Fim", "scenes": [7]}]}
    ch = chapters.from_blocks(script, starts, "pt-BR", {"at": 13.95, "seconds": 2.0}, end=200)
    assert [c["title"] for c in ch] == ["Introdução", "Motor", "Console", "Curto"]  # "Fim" < 10 s after "Curto"
    assert ch[1]["t"] == 16.0 and ch[2]["t"] == 97.0  # moved by the bumper
    assert chapters.as_text(ch).splitlines()[0] == "0:00 Introdução" and chapters.stamp(3725) == "1:02:05"
    assert chapters.from_blocks({"blocks": [{"title": "Só", "scenes": [3]}]}, starts, "pt-BR") == []
    assert chapters.from_blocks({}, starts, "pt-BR") == []


def test_presenter_fills_40_to_60_percent():
    for w, h, vertical in ((1280, 720, False), (1080, 1920, True)):
        for pw, ph in ((500, 700), (700, 700), (300, 900)):
            share = thumbnail.focus_share(thumbnail.focus_size(pw, ph, w, h, vertical), w, h)
            assert 0.35 <= share <= 0.62, (w, pw, ph, share)  # very narrow cut-outs hit the width/height caps
    s = thumbnail.focus_share(thumbnail.focus_size(500, 700, 1280, 720, False), 1280, 720)
    assert 0.40 <= s <= 0.60


def test_presenter_taller_than_frame_keeps_head(tmp_path):
    cut = tmp_path / "c.png"
    img = Image.new("RGBA", (300, 900), (0, 0, 0, 0))
    img.paste((250, 20, 20, 255), (0, 0, 300, 120))  # "head" in red at the top
    img.paste((20, 20, 250, 255), (0, 120, 300, 900))
    img.save(cut)
    out = thumbnail._place_presenter(Image.new("RGB", (1280, 720), (0, 0, 0)), cut, "right", False)
    assert out.getpixel((1200, 60))[0] > 200  # the head is in the frame


def test_kids_thumbnail_picks_another_scene(monkeypatch, tmp_path):
    from cvm.visuals import judge

    imgs = {}
    for i in (3, 4, 5):
        imgs[i] = tmp_path / f"s{i}.png"
        Image.new("RGB", (64, 64)).save(imgs[i])
    monkeypatch.setattr(judge, "ready", lambda: True)
    seen = []

    def fake(items, cartoon=False):
        seen.extend(it["rule"] for it in items)
        return [{"score": 1.0 if Path(it["image"]).name == "s4.png" else 9.0,
                 "problems": ["wide open mouth (face) [3]"] if Path(it["image"]).name == "s4.png" else []} for it in items]

    monkeypatch.setattr(judge, "judge", fake)
    assert thumbnail.kids_scene({"thumbnail_scene": 4}, imgs, "t") == imgs[3]  # 4 refused → nearest that passes
    assert set(seen) == {"kids_thumbnail"}
    monkeypatch.setattr(judge, "ready", lambda: False)
    assert thumbnail.kids_scene({"thumbnail_scene": 4}, imgs, "t") is None  # no judge: keep the chosen scene


def test_big_captions_for_shorts(tmp_path):
    words = [{"w": w, "s": i * 0.3, "e": i * 0.3 + 0.3} for i, w in enumerate("um dois tres quatro cinco".split())]
    small = write_ass(words, tmp_path / "a.ass", 1080, 1920, 400).read_text()
    big = write_ass(words, tmp_path / "b.ass", 1080, 1920, 400, [{"text": "GANCHO", "s": 0, "e": 1.2, "style": "Hook"}],
                    big=True).read_text()
    size = lambda t: int(next(ln for ln in t.splitlines() if ln.startswith("Style: Cap")).split(",")[2])  # noqa: E731
    assert size(big) > size(small) and "\\fscx118" in big and ",Hook,," in big
