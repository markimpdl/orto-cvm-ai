"""Kids series on FLUX.2 Klein: each character's reference sheet is drawn once and guides every scene."""
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from cvm.models import Channel, Platform
from cvm.visuals import cast, select, sources
from cvm.voice import characters


def _series(tmp_path, monkeypatch, **kw):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    ch = Channel(name="Turma", platform=Platform.youtube, prefs={"image_engine": "klein"}, **kw)
    s.add(ch)
    s.commit()
    monkeypatch.setattr(cast.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cast.settings, "visual_judge", False)
    return s, ch


def _fake_klein(drawn):
    def klein(prompt, out, portrait=True, steps=4, seed=None, refs=None, size=None):
        drawn.append({"prompt": prompt, "out": Path(out).name, "seed": seed, "refs": refs, "size": size})
        Path(out).write_bytes(b"png")
        return out
    return klein


def test_klein_only_when_the_series_asks_and_the_model_is_here(monkeypatch):
    monkeypatch.setattr(sources, "MLX", True, raising=False)
    monkeypatch.setattr("cvm.models_registry.MLX", True)
    ready = {"flux2-klein", "flux-schnell", "z-image-turbo"}
    monkeypatch.setattr("cvm.models_registry.is_ready", lambda name: name in ready)
    assert sources.image_engine(kids=True, prefer="klein") == "klein"
    assert sources.image_engine(kids=True) == "flux"  # a series already running keeps its look
    ready.discard("flux2-klein")
    assert sources.image_engine(kids=True, prefer="klein") == "flux"  # not downloaded on this machine


def test_sheet_is_drawn_once_and_survives_a_new_voice(tmp_path, monkeypatch):
    s, ch = _series(tmp_path, monkeypatch, characters={"Cacau": {"description": "turtle girl", "voice": "/v/cacau.wav"}})
    drawn = []
    monkeypatch.setattr(sources, "klein_image", _fake_klein(drawn))
    looks = {"cacau": "a small yellow turtle with a red cap, 3D cartoon", "Gil": "a grey hedgehog, 3D cartoon", "Zeca": " "}
    assert cast.ensure_sheets(s, ch, looks) == ["cacau", "Gil"]
    cacau = s.get(Channel, ch.id).characters["Cacau"]  # stored under the name the series already uses
    assert cacau["voice"] == "/v/cacau.wav" and cacau["look"].startswith("a small yellow turtle") and Path(cacau["sheet"]).is_file()
    assert drawn[0]["size"] == cast.SHEET_SIZE and "yellow turtle" in drawn[0]["prompt"] and drawn[0]["refs"] is None
    assert cast.ensure_sheets(s, ch, {"Cacau": "a blue turtle"}) == [] and len(drawn) == 2  # the first look is the look
    monkeypatch.setattr(characters, "design_voice", lambda c, n, d, lang: {"description": d, "voice": "/v/new.wav"})
    entry = characters.save_voice(s, ch, "Cacau", "higher voice", "pt-BR")
    assert entry["voice"] == "/v/new.wav" and entry["sheet"] == cacau["sheet"] and entry["look"] == cacau["look"]


def test_judge_refusal_tries_other_seeds_and_keeps_the_best(tmp_path, monkeypatch):
    from cvm.visuals import judge

    s, ch = _series(tmp_path, monkeypatch)
    drawn = []
    monkeypatch.setattr(sources, "klein_image", _fake_klein(drawn))
    monkeypatch.setattr(cast.settings, "visual_judge", True)
    monkeypatch.setattr(judge, "ready", lambda: True)
    verdicts = iter([{"score": 4, "problems": ["two tails"], "ok": False}, {"score": 6, "problems": ["extra paw"], "ok": False},
                     {"score": 5, "problems": ["blurry"], "ok": False}])
    monkeypatch.setattr(judge, "judge", lambda items, cartoon=False: [next(verdicts)])
    entry = cast.save_sheet(s, ch, "Cacau", "a turtle", seed=10)
    assert [d["seed"] for d in drawn] == [10, 11, 12] and entry["sheet_seed"] == 11 and entry["sheet_note"] == "extra paw"
    assert [p.name for p in Path(entry["sheet"]).parent.iterdir()] == ["cacau.png"]  # the other takes are removed


def test_scene_cast_and_prompt(tmp_path, monkeypatch):
    s, ch = _series(tmp_path, monkeypatch)
    monkeypatch.setattr(sources, "klein_image", _fake_klein([]))
    cast.ensure_sheets(s, ch, {"Cacau": "turtle", "Gil": "hedgehog", "Zeca": "bird"})
    ch.characters = {**ch.characters, "Narradora": {"voice": "/v/n.wav"}}  # a voice without a drawing
    by_text = cast.scene_cast(ch, {"speaker": "Zeca", "visual": {"image_prompt": "3D cartoon. Cacau, a small turtle, by the river", "motion_prompt": "cacau waves"}})
    assert [n for n, _ in by_text] == ["Cacau", "Zeca"]
    listed = cast.scene_cast(ch, {"speaker": "Zeca", "visual": {"image_prompt": "Cacau and Gil", "characters": ["gil"]}})
    assert [n for n, _ in listed] == ["Gil"]  # the script's own list wins
    assert cast.scene_cast(ch, {"visual": {"image_prompt": "Cacauland castle at night"}}) == []  # whole names only
    p = cast.prompt_with_refs("Cacau and Zeca wave.", by_text)
    assert p.startswith("Cacau is the character in image 1. Zeca is the character in image 2. Cacau and Zeca wave. Keep each")
    assert cast.prompt_with_refs("a river", []) == "a river"


def test_scene_is_drawn_from_the_sheets(tmp_path, monkeypatch):
    sheet = tmp_path / "cacau.png"
    sheet.write_bytes(b"png")
    calls = []
    monkeypatch.setattr(sources, "image_engine", lambda kids=False, prefer=None: prefer or "flux")

    def ai_image(prompt, out, portrait=True, seed=None, kids=False, prefer=None, refs=None):
        calls.append((prompt, refs, Path(out).name))
        Path(out).write_bytes(b"png")
        return "flux2-klein (local)"

    monkeypatch.setattr(sources, "ai_image", ai_image)
    scene = {"index": 3, "visual": {"type": "ai_image", "image_prompt": "Cacau jumps", "_kids": True, "_engine": "klein",
                                    "_seed": 7, "_cast": [("Cacau", str(sheet))]}}
    got = select._ai_image(scene, False, tmp_path)
    assert got["source"] == "flux2-klein (local)" and calls[0][1] == [sheet] and calls[0][0].startswith("Cacau is the character in image 1.")
    assert got["prompt"].startswith("Cacau is the character")
    select._ai_image(scene, False, tmp_path)
    assert len(calls) == 1  # cached
    import os
    os.utime(sheet, ns=(1, 1))  # the sheet was redrawn: the scene is a new image
    select._ai_image(scene, False, tmp_path)
    assert len(calls) == 2 and calls[1][2] != calls[0][2]
    flux = {"index": 3, "visual": {"type": "ai_image", "image_prompt": "Cacau jumps", "_kids": True, "_cast": [("Cacau", str(sheet))]}}
    select._ai_image(flux, False, tmp_path)
    assert calls[2][0] == "Cacau jumps" and calls[2][1] is None  # a FLUX.1 series never gets references
