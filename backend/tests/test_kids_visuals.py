"""Kids episodes are 100% drawn: a failed drawing never becomes a stock photo or a blank card (506, 2026-11-18)."""
from pathlib import Path

import pytest

from cvm.visuals import select


def _scene(kids=True, t="ai_image"):
    v = {"type": t, "image_prompt": "a fairy in a meadow", "search_terms": ["children playing"]}
    if kids:
        v["_kids"] = True
    return {"index": 7, "narration": "x", "visual": v}


def test_kids_scene_never_falls_back_to_stock(monkeypatch, tmp_path):
    monkeypatch.setattr(select, "_ai_image", lambda *a, **k: None)
    stock = []
    monkeypatch.setattr(select, "_broll", lambda *a, **k: stock.append(1) or {"kind": "image", "path": "p.jpg", "source": "wikimedia"})
    with pytest.raises(RuntimeError, match="cena 7"):
        select.pick(_scene(), [], False, Path(tmp_path))
    with pytest.raises(RuntimeError):
        select.pick(_scene(t="broll"), [], False, Path(tmp_path))
    assert stock == []


def test_kids_scene_keeps_its_drawing(monkeypatch, tmp_path):
    monkeypatch.setattr(select, "_ai_image", lambda *a, **k: {"kind": "image", "path": "d.png", "source": "flux-schnell (local)"})
    assert select.pick(_scene(), [], False, Path(tmp_path))["source"] == "flux-schnell (local)"


def test_other_channels_still_use_stock_then_card(monkeypatch, tmp_path):
    monkeypatch.setattr(select, "_ai_image", lambda *a, **k: None)
    monkeypatch.setattr(select, "_broll", lambda *a, **k: None)
    assert select.pick(_scene(kids=False), [], False, Path(tmp_path))["kind"] == "card"
