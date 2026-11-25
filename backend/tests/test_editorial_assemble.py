"""Task 7 of the editorial format: the long news-desk video assembled from plan.json (segments, bumper, 20 s end
screen, audio mix, sweeps) and compose() branching to it. Chromium/ffmpeg are faked; the real thing runs in
scripts/editorial_smoke.py."""
import json
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from cvm.models import Asset, Channel, Job, Project, Render, Script
from cvm.render.editorial import assemble, cards
from cvm.render.editorial.timeline import EditorialError


def _plan(tmp_path: Path) -> list[dict]:
    clip = tmp_path / "clip.mp4"
    photo = tmp_path / "foto.jpg"
    bg = tmp_path / "bg.jpg"
    for p in (clip, photo, bg):
        p.write_bytes(b"x")
    return [
        {"em": "Olha", "tipo": "pergunta", "texto": "Isso é *real*?", "secao": ["01", "O *CARRO*"], "secao_nova": True,
         "som": "riser", "_w": 0, "start": 0.0, "end": 3.0, "bg": str(bg)},
        {"em": "carro", "tipo": "video", "secao": ["01", "O *CARRO*"], "mov": "in", "_w": 3, "start": 3.0, "end": 6.0,
         "fonte": "Maker", "bg": str(bg),
         "media": {"kind": "video", "file": str(clip), "start": 2.0, "window_end": 5.5, "credit": "Maker",
                   "source": "https://yt/1", "own": False}},
        {"em": "custa", "tipo": "foto", "secao": ["02", "O *PREÇO*"], "secao_nova": True, "varre": True, "_w": 6,
         "start": 6.0, "end": 9.0, "bg": str(bg),
         "media": {"kind": "photo", "file": str(photo), "start": 0.0, "window_end": None, "credit": "Wikimedia",
                   "own": False}, "fonte": "Wikimedia"},
        {"em": "fim", "tipo": "prova", "secao": ["02", "O *PREÇO*"], "_w": 9, "start": 9.0, "end": 12.6,
         "fonte": "news.example.com", "img": str(photo), "size": [1200, 600],
         "boxes": [{"rects": [[10, 20, 300, 40]]}], "_hl": [0.6], "bg": str(bg), "flash": True},
    ]


# --- layout: end screen -------------------------------------------------------------------------------------------

def test_end_screen_is_20s_and_clean(tmp_path):
    plan = _plan(tmp_path)
    scenes, end_from, bumper = assemble.layout(plan, narration_s=11.3, bumper_s=0.0)
    assert bumper is None
    assert end_from == pytest.approx(11.3, abs=0.04)
    assert scenes[-1]["end"] == pytest.approx(11.3 + 20, abs=0.1)
    content = [s for s in scenes if not s.get("end_screen")]
    ends = [s for s in scenes if s.get("end_screen")]
    assert len(content) == 4 and ends
    assert content[-1]["end"] == pytest.approx(end_from)
    assert sum(s["end"] - s["start"] for s in ends) == pytest.approx(20, abs=0.05)
    # back to back, frame aligned
    for a, b in zip(scenes, scenes[1:]):
        assert a["end"] == pytest.approx(b["start"])
        assert round(b["start"] * 30, 6) == pytest.approx(round(b["start"] * 30))
    brand = {"logo": None, "name": "Mesa", "colours": {}}
    for s in ends:
        spec = cards.page_spec(s, brand, "pt-BR")
        assert not spec.get("secao") and not spec.get("fonte") and spec.get("fim") or spec["tipo"] == "fim"
        assert not any(s.get(k) for k in ("som", "varre", "flash", "texto"))
    # footage the video already used: the clip's window (slowed at most 1.7×) and the photo, dimmed by the page
    used = {(s.get("media") or {}).get("file") for s in ends}
    assert str(tmp_path / "clip.mp4") in used
    for s in ends:
        m = s.get("media") or {}
        if m.get("kind") == "video":
            assert (s["end"] - s["start"]) / (m["window_end"] - m["start"]) <= cards.MAX_SLOW


def test_end_screen_without_footage_uses_backgrounds_or_a_card(tmp_path):
    plan = [dict(s) for s in _plan(tmp_path) if s["tipo"] not in ("video", "foto")]
    scenes, end_from, _ = assemble.layout(plan, 11.3, 0.0)
    ends = [s for s in scenes if s.get("end_screen")]
    assert {s["media"]["file"] for s in ends} == {str(tmp_path / "bg.jpg")}
    plan = [{k: v for k, v in s.items() if k != "bg"} for s in plan]
    scenes, _, _ = assemble.layout(plan, 11.3, 0.0)
    ends = [s for s in scenes if s.get("end_screen")]
    assert [s["tipo"] for s in ends] == ["fim"] and ends[0]["end"] - ends[0]["start"] == pytest.approx(20, abs=0.05)


def test_bumper_takes_the_vinheta_and_pushes_the_rest(tmp_path):
    plan = _plan(tmp_path)
    plan.insert(1, {"tipo": "vinheta", "start": 3.0, "end": 3.6, "_w": 0})
    plan[2]["start"] = 3.6
    scenes, end_from, bumper = assemble.layout(plan, 11.3, bumper_s=2.8)
    vin = next(s for s in scenes if s["tipo"] == "vinheta")
    assert vin["bumper"] and vin["end"] - vin["start"] == pytest.approx(2.8, abs=0.04)
    assert bumper == {"at": 3.0, "seconds": pytest.approx(2.2, abs=0.04)}
    assert scenes[2]["start"] == pytest.approx(3.6 + 2.2, abs=0.04)
    assert end_from == pytest.approx(11.3 + 2.2, abs=0.04)
    assert scenes[-1]["end"] == pytest.approx(11.3 + 2.2 + 20, abs=0.1)


# --- chapters -----------------------------------------------------------------------------------------------------

def test_chapters_from_sections():
    def sc(t, sec=None, new=False):
        return {"tipo": "pergunta", "start": t, "end": t + 1, "secao": sec, "secao_nova": new}

    scenes = [sc(0.0), sc(4.0, ["01", "O *GANCHO*"], True), sc(20.0, ["02", "MUITA COISA *MUDOU*"], True),
              sc(25.0, ["03", "CURTO DEMAIS"], True), sc(45.0, ["04", "O BOOT"], True),
              sc(92.0, ["05", "SUA VEZ"], True), {"tipo": "fim", "start": 100.0, "end": 120.0, "end_screen": True}]
    got = assemble.chapters(scenes, end_from=100.0)
    assert got == [{"t": 0.0, "title": "O GANCHO"}, {"t": 20.0, "title": "MUITA COISA MUDOU"},
                   {"t": 45.0, "title": "O BOOT"}]  # 25 s is < 10 s after 20; 92 s is < 10 s before the end screen
    assert assemble.chapters(scenes[:3], end_from=100.0) == []  # YouTube needs 3


# --- audio events -------------------------------------------------------------------------------------------------

def test_mix_events_from_scenes():
    scenes = [
        {"tipo": "pergunta", "start": 0.0, "end": 3.0, "som": "riser"},
        {"tipo": "impacto", "start": 3.0, "end": 5.0, "som": "impacto", "impacto_t": 0.4},
        {"tipo": "vinheta", "start": 5.0, "end": 7.8, "bumper": True},
        {"tipo": "lista", "start": 7.8, "end": 12.0, "varre": True,
         "_items": [{"t": 0.12, "text": "A"}, {"t": 1.0, "text": "B"}, {"t": 2.0, "text": "C"}]},
        {"tipo": "prova", "start": 12.0, "end": 15.0, "_hl": [0.5, 1.5]},
        {"tipo": "vinheta", "start": 15.0, "end": 16.0},  # a vinheta drawn by the page (no bumper clip)
        {"tipo": "video", "start": 16.0, "end": 21.0, "fim": True, "end_screen": True},
    ]
    ev = assemble.events(scenes)
    assert ("riser", 0.0) in ev and ("impacto", pytest.approx(3.4)) in ev
    assert ("bumper", 5.0) in ev
    assert ("whoosh", pytest.approx(7.58)) in ev
    assert [t for n, t in ev if n == "pop"] == [pytest.approx(8.8), pytest.approx(9.8)]  # first item has no pop
    assert [t for n, t in ev if n == "marca"] == [pytest.approx(12.5), pytest.approx(13.5)]
    assert ("whoosh", 15.0) in ev and ("impacto", pytest.approx(15.18)) in ev
    assert not [e for e in ev if e[1] >= 16.0]


def test_mix_graph_inserts_the_bumper_gap(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(assemble, "run_ffmpeg", lambda *a: calls.append(a))
    monkeypatch.setattr(assemble, "make_sfx", lambda d: {k: d / f"{k}.wav" for k in assemble.SFX})
    scenes = [{"tipo": "pergunta", "start": 0.0, "end": 3.0, "som": "pop"},
              {"tipo": "vinheta", "start": 3.0, "end": 5.8, "bumper": True}]
    assemble.mix_audio(Path("n.wav"), scenes, 30.0, tmp_path / "a.wav", music=Path("m.mp3"), sfx_dir=tmp_path,
                       bumper_audio=Path("b.mp4"), insert=(3.0, 2.2), end_from=10.0)
    args = calls[0]
    graph = args[args.index("-filter_complex") + 1]
    assert "atrim=0:3.000" in graph and "adelay=2200:all=1" in graph and "sidechaincompress" in graph
    assert "loudnorm=I=-14" in graph
    calls.clear()
    assemble.mix_audio(Path("n.wav"), scenes[:1], 30.0, tmp_path / "a.wav", music=None, sfx_dir=tmp_path,
                       bumper_audio=None, insert=None, end_from=10.0)
    graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "sidechaincompress" not in graph and "concat" not in graph


# --- render() with fake segments ----------------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch, tmp_path):
    import cvm.db
    from cvm.config import settings

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(cvm.db, "engine", eng)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with Session(eng) as s:
        s.add(Channel(id=1, name="Mesa", platform="youtube", language="pt-BR", presenter="editorial"))
        s.add(Project(id=10, title="t", topic="t", channel_id=1, languages=["pt-BR"], platforms=["youtube"],
                      style="explainer"))
        s.add(Script(project_id=10, language="pt-BR", version=1,
                     content={"hook": "h", "scenes": [{"index": 1, "narration": "x"}],
                              "packaging": {"title": "T", "thumbnail_text": "OLHA"}}))
        s.add(Asset(project_id=10, kind="narration", language="pt-BR", path=str(tmp_path / "narr.wav"),
                    meta={"duration": 11.3, "scenes": []}))
        s.commit()
    from cvm.paths import project_dir

    (tmp_path / "narr.wav").write_bytes(b"wav")
    d = project_dir(10, "pt-BR") / "editorial"
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps(_plan(tmp_path)))
    return eng


def _fake_pipeline(monkeypatch, made):
    def segments(scenes, brand, lang, dest, bumper_clip, page_factory=None):
        made["scenes"] = scenes
        made["specs"] = [cards.page_spec(s, brand, lang) for s in scenes if not s.get("bumper")]
        return [dest / f"seg{k:03d}.mp4" for k in range(len(scenes))]

    monkeypatch.setattr(assemble, "_segments", segments)
    monkeypatch.setattr(assemble, "_concat", lambda segs, out: made.setdefault("concat", segs) and out)
    monkeypatch.setattr(assemble, "_sweep_png", lambda colour, dest: dest / "sweep.png")
    monkeypatch.setattr(assemble, "mix_audio", lambda *a, **k: made.setdefault("mix", (a, k)))

    def sweeps(scenes, video, audio, out, sweep, total):
        made["total"] = total
        Path(out).write_bytes(b"mp4")

    monkeypatch.setattr(assemble, "sweeps_and_flash", sweeps)
    monkeypatch.setattr(assemble, "pick_music", lambda project, prefs, session: None)


def test_render_with_fake_segments(env, monkeypatch, tmp_path):
    made = {}
    _fake_pipeline(monkeypatch, made)
    with Session(env) as s:
        res = assemble.render(s, s.get(Project, 10), "pt-BR", tmp_path / "out.mp4")
    assert res.path == tmp_path / "out.mp4" and res.path.exists()
    assert made["total"] == pytest.approx(11.3 + 20, abs=0.1)
    assert res.end_screen_from == pytest.approx(11.3, abs=0.04)
    assert len(made["concat"]) == len(made["scenes"])
    assert res.credits == ["Maker", "Wikimedia"]
    assert made["mix"][0][0] == tmp_path / "narr.wav"  # the narration asset the timeline used
    # the video language, not the page's: pt footer labels
    assert all(sp["labels"]["fonte"] == "FONTE" for sp in made["specs"])


def test_render_without_plan_goes_back_to_visuals(env, tmp_path):
    from cvm.paths import project_dir

    (project_dir(10, "pt-BR") / "editorial" / "plan.json").unlink()
    with Session(env) as s, pytest.raises(EditorialError) as e:
        assemble.render(s, s.get(Project, 10), "pt-BR", tmp_path / "out.mp4")
    assert e.value.fix_state == "sourcing_visuals"


# --- compose() branch ---------------------------------------------------------------------------------------------

class OldPath(Exception):
    pass


@pytest.fixture
def compose_spy(env, monkeypatch, tmp_path):
    from cvm.render import compose as compose_mod
    from cvm.render import thumbnail

    calls = []

    def fake_render(session, project, language, out):
        calls.append((language, out))
        Path(out).write_bytes(b"mp4")
        return assemble.Assembled(path=Path(out), duration=31.3, end_screen_from=11.3,
                                  chapters=[{"t": 0.0, "title": "A"}], music=None, credits=["Maker"],
                                  first_segment=None, bumper=None)

    monkeypatch.setattr(assemble, "render", fake_render)
    monkeypatch.setattr(compose_mod, "duration", lambda p: 31.3)
    thumb_kw = []
    monkeypatch.setattr(thumbnail, "make_thumbnails", lambda *a, **k: thumb_kw.append(k) or [tmp_path / "thumbnail.jpg"])
    monkeypatch.setattr(compose_mod, "_thumb_kw", thumb_kw, raising=False)  # read by the tests below

    def old(*a, **k):
        raise OldPath

    monkeypatch.setattr(compose_mod, "narration_asset", old)
    return compose_mod, calls


def test_compose_branches_only_for_editorial_long(env, compose_spy):
    compose_mod, calls = compose_spy
    with Session(env) as s:
        p = s.get(Project, 10)
        r = compose_mod.compose(s, p, "pt-BR", "youtube")
        assert calls and calls[0][0] == "pt-BR"
        assert r.path.endswith("10_pt-BR_youtube.mp4") and Path(r.path).exists()
        assert r.meta["duration"] == 31.3 and r.meta["end_screen_s"] == 20.0
        assert r.meta["end_screen_from"] == pytest.approx(11.3)
        assert r.meta["chapters"] == [{"t": 0.0, "title": "A"}] and r.meta["editorial"]
        assert r.meta["thumbnails"] and r.meta["avatar_on_screen"] is False
        assert s.exec(select(Render)).one().id == r.id

        for lang, profile in (("pt-BR", "youtube_shorts"), ("ar-AE", "youtube")):
            with pytest.raises(OldPath):
                compose_mod.compose(s, p, lang, profile)
        with pytest.raises(OldPath):  # cuts never take the editorial path
            compose_mod.compose(s, p, "pt-BR", "youtube", part=(1, [1]))
        p.presenter = "scenes"
        s.add(p)
        s.commit()
        with pytest.raises(OldPath):
            compose_mod.compose(s, p, "pt-BR", "youtube")
        p.presenter, p.style = None, "kids"
        s.add(p)
        s.commit()
        with pytest.raises(OldPath):
            compose_mod.compose(s, p, "pt-BR", "youtube")
    assert len(calls) == 1


# --- the composing handler routes EditorialError ------------------------------------------------------------------

def _compose_handler(eng, monkeypatch, err, history=None):
    from cvm.render import compose as compose_mod
    from cvm.workers import tasks

    def boom(*a, **k):
        raise err

    monkeypatch.setattr(compose_mod, "compose", boom)
    with Session(eng) as s:
        s.add(Job(id=5, project_id=10, state="composing", history=history or []))
        s.commit()
        job = s.get(Job, 5)
        nxt = tasks.HANDLERS["composing"](job, s)
        s.refresh(job)
        return nxt, job


def _err(fix):
    e = EditorialError("A cena em 12.0s precisa de 4.00s, mas o trecho do vídeo tem 2.00s.")
    e.fix_state = fix
    return e


def test_composing_sends_slow_window_back_to_visuals(env, monkeypatch):
    nxt, job = _compose_handler(env, monkeypatch, _err("sourcing_visuals"))
    assert nxt is None and job.state == "sourcing_visuals" and "12.0s" in job.history[-1]["note"]


def test_composing_routes_script_and_voice(env, monkeypatch):
    nxt, job = _compose_handler(env, monkeypatch, _err("scripting"))
    assert nxt is None and job.state == "scripting" and job.history[-1]["revise"]


def test_composing_routes_voice(env, monkeypatch):
    nxt, job = _compose_handler(env, monkeypatch, _err("voicing"))
    assert nxt is None and job.state == "voicing"


def test_composing_send_backs_capped(env, monkeypatch):
    from cvm.workers import tasks

    old = [{"from": "composing", "to": "sourcing_visuals", "note": f"{tasks.EDITORIAL_NOTE} x"}] * 2
    nxt, job = _compose_handler(env, monkeypatch, _err("sourcing_visuals"), history=old)
    assert nxt is None and job.state == "failed" and "12.0s" in job.error


def test_make_sfx_writes_aside_and_moves_in(tmp_path, monkeypatch):
    """Review finding 7: an interrupted effect leaves no half .wav that the next run would take as done."""
    calls = []

    def fake(*args):
        out = Path(args[-1])
        assert out.name.endswith(".wav.part") and "-f" in args
        out.write_bytes(b"half")
        calls.append(out)
        if len(calls) == 2:
            raise RuntimeError("ffmpeg killed")

    monkeypatch.setattr(assemble, "run_ffmpeg", fake)
    with pytest.raises(RuntimeError):
        assemble.make_sfx(tmp_path)
    done = [p.name for p in tmp_path.glob("*.wav")]
    assert len(done) == 1 and done[0] == calls[0].name.removesuffix(".part")
    monkeypatch.setattr(assemble, "run_ffmpeg", lambda *a: Path(a[-1]).write_bytes(b"ok"))
    got = assemble.make_sfx(tmp_path)
    assert all(p.exists() and p.read_bytes() in (b"half", b"ok") for p in got.values())
    assert sorted(p.name for p in tmp_path.glob("*.wav")) == sorted(f"{k}.wav" for k in assemble.SFX)


def test_sweep_png_written_aside(tmp_path, monkeypatch):
    import sys
    import types

    shots = []

    class Page:
        def set_content(self, html):
            pass

        def screenshot(self, path, type=None, omit_background=False):
            assert path.endswith(".png.part") and type == "png"
            Path(path).write_bytes(b"png")
            shots.append(path)

    class Browser:
        def new_page(self, viewport):
            return Page()

        def close(self):
            pass

    class PW:
        chromium = types.SimpleNamespace(launch=lambda: Browser())

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake = types.ModuleType("playwright.sync_api")
    fake.sync_playwright = lambda: PW()
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)
    out = assemble._sweep_png("#ff0000", tmp_path)
    assert out.exists() and out.suffix == ".png" and not Path(shots[0]).exists()


def test_editorial_thumbnail_has_no_presenter(env, compose_spy):
    """Review finding 9: the editorial compose path asks for thumbnails without the avatar cut-out."""
    compose_mod, _ = compose_spy
    with Session(env) as s:
        compose_mod.compose(s, s.get(Project, 10), "pt-BR", "youtube")
    assert compose_mod._thumb_kw and compose_mod._thumb_kw[0]["presenter"] is False


def test_make_thumbnails_presenter_false_skips_cutout(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    from cvm.render import thumbnail

    monkeypatch.setattr(thumbnail, "presenter_cutout", lambda avatar_id: pytest.fail("no cut-out for editorial"))
    monkeypatch.setattr(thumbnail, "background", lambda *a, **k: tmp_path / "bg.jpg")
    monkeypatch.setattr(thumbnail, "_sticker_style", lambda project: None)
    built = []
    monkeypatch.setattr(thumbnail, "build", lambda bg, t, out, landscape, cut, side, sticker=None:
                        built.append(cut) or out)
    thumbnail.make_thumbnails(NS(id=1, avatar_id=3), {"hook": "Olha isso"}, "pt-BR", tmp_path / "x.mp4", tmp_path,
                              True, variants=False, presenter=False)
    assert built == [None]
