"""Background music source: none by default, own ACE-Step library when chosen, Openverse files named by their bytes."""
from types import SimpleNamespace

from cvm.render import music, music_gen


def test_jamendo_download_gets_its_audio_extension(tmp_path):
    f = tmp_path / "abc"  # a Jamendo "?trackid=…&format=mp32" link saves without an extension
    f.write_bytes(b"ID3\x04\x00" + b"\x00" * 20)
    assert music._with_audio_ext(f).name == "abc.mp3"
    g = tmp_path / "x.bin"
    g.write_bytes(b"OggS" + b"\x00" * 20)
    assert music._with_audio_ext(g).suffix == ".ogg"
    h = tmp_path / "y.bin"
    h.write_bytes(b"<html>")
    assert music._with_audio_ext(h).suffix == ".bin"  # not audio: left for the caller to drop


def test_source_none_keeps_videos_without_music(tmp_path, monkeypatch):
    monkeypatch.setattr(music.settings, "music_dir", tmp_path)
    monkeypatch.setattr(music.settings, "music_source", "none")
    assert music.pick_music(SimpleNamespace(id=1, channel_id=None, style="explainer"), {"music": True}) is None


def test_acestep_library_is_used_and_needs_no_credit(tmp_path, monkeypatch):
    monkeypatch.setattr(music.settings, "music_dir", tmp_path)
    monkeypatch.setattr(music.settings, "music_source", "acestep")
    monkeypatch.setattr(music_gen, "ready", lambda: True)

    def fake_grow(style, seed):
        folder = music_gen.library(style)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "lesson_1.wav").write_bytes(b"RIFF")
        (folder / "lesson_1.json").write_text('{"source": "acestep", "license": "own"}')
        return [folder / "lesson_1.wav"]

    monkeypatch.setattr(music_gen, "grow", fake_grow)
    proj = SimpleNamespace(id=7, channel_id=None, style="lesson")
    assert music.pick_music(proj) is None  # channel without prefs.music: no generated music
    track = music.pick_music(proj, {"music": True})
    assert track and track.parent == tmp_path / "acestep" / "lesson"
    assert music.credit_for(track) is None
