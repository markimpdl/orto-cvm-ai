"""The review page plays the render file while a re-render runs (2026-11-22: "fica carregando"): the new file is
written aside and swapped in whole, so the player never reads a half-written mp4."""
import pytest

from cvm.render import compose


def test_written_aside_and_swapped_in_whole(tmp_path):
    final = tmp_path / "r.mp4"
    final.write_bytes(b"old")
    with compose.written_aside(final) as tmp:
        assert tmp != final and tmp.parent == final.parent
        tmp.write_bytes(b"new, half")
        assert final.read_bytes() == b"old"  # what the player sees while encoding
    assert final.read_bytes() == b"new, half" and not tmp.exists()


def test_failed_encode_keeps_the_old_file(tmp_path):
    final = tmp_path / "r.mp4"
    final.write_bytes(b"old")
    with pytest.raises(RuntimeError), compose.written_aside(final) as tmp:
        tmp.write_bytes(b"broken")
        raise RuntimeError("ffmpeg died")
    assert final.read_bytes() == b"old" and not tmp.exists()
