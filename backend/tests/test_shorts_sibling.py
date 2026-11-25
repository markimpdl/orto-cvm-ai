"""A long script is never rendered as a Short: vertical profiles go to a sibling Short project (cvm/shorts.py)."""
from types import SimpleNamespace

from cvm import shorts


def _setup(monkeypatch, long=True, sibling=None):
    opened = []
    monkeypatch.setattr(shorts, "long_script", lambda s, p, lang: long)
    monkeypatch.setattr(shorts, "sibling_of", lambda s, pid: sibling)
    monkeypatch.setattr(shorts, "open_sibling", lambda s, p, plats: opened.append(plats))
    return opened


P = SimpleNamespace(id=643)
BOTH = [("pt-BR", "youtube"), ("pt-BR", "youtube_shorts")]


def test_long_script_drops_vertical_and_opens_sibling(monkeypatch):
    opened = _setup(monkeypatch)
    assert shorts.split_targets(None, P, BOTH) == [("pt-BR", "youtube")]
    assert opened == [["youtube_shorts"]]


def test_sibling_opened_only_once(monkeypatch):
    opened = _setup(monkeypatch, sibling=700)
    assert shorts.split_targets(None, P, BOTH) == [("pt-BR", "youtube")]
    assert opened == []


def test_short_script_keeps_its_vertical_render(monkeypatch):
    opened = _setup(monkeypatch, long=False)
    assert shorts.split_targets(None, P, BOTH) == BOTH and opened == []


def test_only_vertical_targets_render_as_before(monkeypatch):
    opened = _setup(monkeypatch)
    assert shorts.split_targets(None, P, [("pt-BR", "youtube_shorts")]) == [("pt-BR", "youtube_shorts")]
    assert opened == []


def test_cuts_profiles_become_a_written_short(monkeypatch):
    opened = _setup(monkeypatch)
    t = [("pt-BR", "youtube"), ("pt-BR", "youtube_shorts_cuts")]
    assert shorts.split_targets(None, P, t) == [("pt-BR", "youtube")] and opened == [["youtube_shorts"]]


def test_sibling_failure_does_not_block_the_long_video(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(shorts, "open_sibling", lambda *a: (_ for _ in ()).throw(RuntimeError("guard")))
    assert shorts.split_targets(None, P, BOTH) == [("pt-BR", "youtube")]
