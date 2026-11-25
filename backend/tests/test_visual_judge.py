from pathlib import Path

from cvm.visuals import judge


def test_review_redraws_swaps_and_reverts(monkeypatch, tmp_path):
    scores = {"a.png": 3, "b.png": 9, "strip_02.jpg": 2, "c.png": 8, "a_1001.png": 4, "a_2001.png": 5}

    def fake_judge(items, cartoon=False):
        return [{"score": scores[Path(it["image"]).name], "problems": [], "ok": scores[Path(it["image"]).name] >= 6}
                for it in items]

    monkeypatch.setattr(judge, "judge", fake_judge)
    monkeypatch.setattr(judge, "strip", lambda v, out: out)
    chosen = [
        {"_scene": 1, "kind": "image", "path": "a.png", "source": "flux-schnell (local)"},  # bad, redraws bad → stock
        {"_scene": 3, "kind": "image", "path": "b.png", "source": "flux-schnell (local)"},  # good
        {"_scene": 2, "kind": "video", "path": "clip.mp4", "still": "c.png", "source": "ltx-2.3 (local)",
         "motion_prompt": "m"},  # morphing clip → back to its (good) still
        {"_scene": 4, "kind": "video", "path": "stock.mp4", "source": "pexels"},  # not AI: untouched
    ]
    scenes = {i: {"index": i, "narration": "n", "visual": {}} for i in (1, 2, 3, 4)}
    redo = lambda sc, seed: {"path": f"a_{seed}.png", "kind": "image", "source": "flux-schnell (local)"}  # noqa: E731
    out = judge.review(chosen, scenes, redo, lambda sc: {"kind": "video", "path": "pexels.mp4", "source": "pexels"},
                       False, tmp_path)
    assert out[0]["path"] == "pexels.mp4" and out[0]["judge"]["action"].startswith("trocada")
    assert out[1]["path"] == "b.png" and out[1]["judge"]["ok"]
    assert out[2]["path"] == "c.png" and out[2]["kind"] == "image"
    assert out[3] == chosen[3]


def test_kids_keep_best_drawn_take(monkeypatch, tmp_path):
    scores = {"a.png": 3, "a_1001.png": 5, "a_2001.png": 4}
    monkeypatch.setattr(judge, "judge", lambda items, cartoon=False: [
        {"score": scores[Path(it["image"]).name], "problems": [], "ok": False} for it in items])
    chosen = [{"_scene": 1, "kind": "image", "path": "a.png", "source": "flux-schnell (local)"}]
    redo = lambda sc, seed: {"path": f"a_{seed}.png", "kind": "image", "source": "flux-schnell (local)"}  # noqa: E731
    out = judge.review(chosen, {1: {"index": 1, "visual": {}}}, redo, lambda sc: {"path": "stock"}, True, tmp_path)
    assert out[0]["path"] == "a_1001.png" and "mantida" in out[0]["judge"]["action"]


def test_sharpen_swaps_only_flux_stills(tmp_path, monkeypatch):
    from cvm.visuals import select, upscale

    a, b, c = (tmp_path / n for n in ("a.png", "b.mp4", "c.jpg"))
    for f in (a, b, c):
        f.write_bytes(b"x")
    monkeypatch.setattr(upscale, "ready", lambda: True)
    seen = []

    def fake(paths, short):
        seen.extend(paths)
        return {str(p): str(p) + ".sr" for p in paths}

    monkeypatch.setattr(upscale, "upscale", fake)
    picked = [{"kind": "image", "source": "flux-schnell (local)", "path": str(a), "_scene": 0},
              {"kind": "video", "source": "ltx", "path": str(b), "_scene": 1},
              {"kind": "image", "source": "pexels", "path": str(c), "_scene": 2}]
    out = select._sharpen(picked, 3)
    assert seen == [a]
    assert out[0]["path"] == str(a) + ".sr" and out[0]["original"] == str(a)
    assert out[1] == picked[1] and out[2] == picked[2]


def test_image_engine_choice(monkeypatch):
    from cvm import models_registry
    from cvm.config import settings
    from cvm.visuals import sources

    ready = {"z-image-turbo": True, "flux-schnell": True}
    monkeypatch.setattr(models_registry, "is_ready", lambda n: ready[n])
    monkeypatch.setattr(models_registry, "MLX", True)
    monkeypatch.setattr(settings, "image_engine", "z-image")
    assert sources.image_engine() == "z-image"
    assert sources.image_engine(kids=True) == "flux"  # series look stays the same
    ready["z-image-turbo"] = False
    assert sources.image_engine() == "flux"
    ready["flux-schnell"] = False
    assert sources.image_engine() is None
    assert sources.is_ai_still("z-image-turbo (local)") and sources.is_ai_still("flux-schnell (local)")
    assert not sources.is_ai_still("pexels")


def test_kids_channel_can_choose_zimage(monkeypatch):
    from cvm import models_registry
    from cvm.visuals import sources

    monkeypatch.setattr(models_registry, "is_ready", lambda n: True)
    monkeypatch.setattr(models_registry, "MLX", True)
    assert sources.image_engine(kids=True, prefer="z-image") == "z-image"
    assert sources.image_engine(kids=False, prefer="flux") == "flux"


def test_ai_drawn_flag_goes_straight_to_real_footage(monkeypatch, tmp_path):
    monkeypatch.setattr(judge, "judge", lambda items, cartoon=False: [
        {"score": 4, "problems": ["flag-like banner with wrong colours (pole) [3]"], "ok": False} for _ in items])
    redraws = []
    chosen = [{"_scene": 1, "kind": "image", "path": "a.png", "source": "z-image-turbo (local)"}]
    out = judge.review(chosen, {1: {"index": 1, "visual": {}}}, lambda sc, seed: redraws.append(seed),
                       lambda sc: {"kind": "video", "path": "real.mp4", "source": "pexels"}, False, tmp_path)
    assert redraws == [] and out[0]["path"] == "real.mp4"
