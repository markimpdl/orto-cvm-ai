"""User's rule (2026-11-22): the avatar never shows his photo's white background; scenes use the cut-out."""
from pathlib import Path

import pytest


def test_cutout_refuses_white_background_when_matting_missing(tmp_path, monkeypatch):
    from cvm.avatar import matting
    from cvm.render import compose

    monkeypatch.setattr(matting, "matting_ready", lambda: False)
    clip = tmp_path / "avatar_01.mp4"
    clip.write_bytes(b"v")
    with pytest.raises(RuntimeError, match="fundo branco"):
        compose._cutout(clip)


def test_cutout_is_made_once_and_reused(tmp_path, monkeypatch):
    from cvm.avatar import matting
    from cvm.render import compose

    calls = []
    monkeypatch.setattr(matting, "matting_ready", lambda: True)
    def fake(v, out):
        calls.append(v)
        out.write_bytes(b"a")
        return out

    monkeypatch.setattr(matting, "matte", fake)
    clip = tmp_path / "avatar_01.mp4"
    clip.write_bytes(b"v")
    a = compose._cutout(clip)
    b = compose._cutout(clip)
    assert a == b and a.suffix == ".mov" and calls == [clip]


@pytest.mark.parametrize("big", [True, False])
def test_overlay_cutout_has_no_frame(tmp_path, monkeypatch, big):
    from cvm.render import compose

    seen = []
    monkeypatch.setattr(compose, "run_ffmpeg", lambda *a: seen.append(" ".join(map(str, a))))
    compose._overlay_cutout(Path("bg.mp4"), Path("a.mov"), 5.0, 1080, 1920, 30, 300, tmp_path / "o.mp4", big=big)
    cmd = seen[0]
    assert "a.mov" in cmd and "white" not in cmd and "format=yuva420p" in cmd
