"""A caption line never bridges a pause in the speech: words before and after a silence (or the source's own voice)
never share a line, so nothing stays frozen on screen (2026-11-22, Short 580: "DEU NISSO. GTA" for 30 s)."""
from cvm.render.captions import write_ass


def test_caption_line_breaks_at_a_pause(tmp_path):
    words = [{"w": "deu", "s": 3.0, "e": 3.3}, {"w": "nisso.", "s": 3.3, "e": 3.8}, {"w": "GTA", "s": 37.6, "e": 38.0}]
    text = write_ass(words, tmp_path / "c.ass", 1080, 1920, 300, big=True).read_text()
    caps = [l for l in text.splitlines() if l.startswith("Dialogue") and ",Cap," in l]
    assert not any("NISSO" in l and "GTA" in l for l in caps)
    assert all(not l.split(",")[2].startswith("0:00:3") or "GTA" in l for l in caps)  # nothing of 3 s reaches 37 s
