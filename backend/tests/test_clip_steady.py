"""An animated clip that loses its subject (the character jumps out of the frame, the drawing becomes another
scene) is made again with another seed, and if that fails too the scene keeps its still."""

from pathlib import Path

from cvm.visuals import clip_rank, select, sources


def _setup(monkeypatch, scores):
    made = []

    def fake_animate(image, prompt, out, seconds, portrait, steps, seed=42, purpose="realistic"):
        made.append((Path(out).name, seed, prompt))
        Path(out).write_bytes(b"x")
        return Path(out), "ltx-2.5"

    monkeypatch.setattr(sources, "animate_clip", fake_animate)
    monkeypatch.setattr(sources, "ltx_ready", lambda: True)
    monkeypatch.setattr(sources, "ltx_version", lambda: "2.5")
    monkeypatch.setattr(clip_rank, "available", lambda: True)
    it = iter(scores)
    monkeypatch.setattr(clip_rank, "steadiness", lambda video: next(it))
    return made


SCENE = {"index": 3, "visual": {"motion_prompt": "banknotes slide down the pile"}}
STILL = {"path": "still.png", "source": "z-image-turbo (local)", "kind": "image"}


def test_steady_clip_is_kept(monkeypatch, tmp_path):
    made = _setup(monkeypatch, [0.93])
    out = select._animate(SCENE, STILL, 4.0, False, tmp_path)
    assert out["kind"] == "video" and out["steady"] == 0.93 and len(made) == 1 and made[0][1] == 42


def test_drifting_clip_is_made_again_with_another_seed(monkeypatch, tmp_path):
    made = _setup(monkeypatch, [0.62, 0.91])
    out = select._animate(SCENE, STILL, 4.0, False, tmp_path)
    assert len(made) == 2 and made[1][1] != 42 and made[1][0] != made[0][0]
    assert "stays" in made[1][2] and "stays" not in made[0][2]  # the second take is told to hold the subject
    assert out["path"].endswith(made[1][0]) and out["steady"] == 0.91


def test_two_drifting_takes_keep_the_still(monkeypatch, tmp_path):
    made = _setup(monkeypatch, [0.62, 0.60])
    assert select._animate(SCENE, STILL, 4.0, False, tmp_path) is None and len(made) == 2


def test_unmeasurable_clip_is_kept(monkeypatch, tmp_path):
    made = _setup(monkeypatch, [None])
    assert select._animate(SCENE, STILL, 4.0, False, tmp_path)["kind"] == "video" and len(made) == 1


def test_without_clip_model_nothing_is_checked(monkeypatch, tmp_path):
    _setup(monkeypatch, [])
    monkeypatch.setattr(clip_rank, "available", lambda: False)
    assert select._animate(SCENE, STILL, 4.0, False, tmp_path)["kind"] == "video"
