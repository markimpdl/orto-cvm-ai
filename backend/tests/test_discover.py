"""Crescer → "Em alta": classify YouTube search results and rank them (cvm/grow/discover.py)."""
from cvm.grow import discover


def e(i, status=None, dur=None, views=None, viewers=None):
    return {"id": f"{i:011d}", "title": f"t{i}", "channel": "c", "live_status": status, "duration": dur,
            "view_count": views, "concurrent_view_count": viewers}


def test_item_kinds():
    assert discover._item(e(1, "is_live"))["kind"] == "live"
    assert discover._item(e(2, "was_live", 9000))["kind"] == "was_live"
    assert discover._item(e(3, None, 45))["kind"] == "short"
    assert discover._item(e(4, None, 600))["kind"] == "video"
    assert discover._item({"id": "UCchannel123456", "title": "a channel"}) is None


def test_discover_splits_and_ranks(monkeypatch):
    calls = {"live": [e(1, "is_live", viewers=10), e(2, "is_live", viewers=900)],
             "week": [e(3, "was_live", 9000, views=5), e(4, None, 600, views=50), e(2, "is_live", viewers=900)],
             "today": [e(5, "was_live", 9000, views=99), e(6, None, 30, views=7)]}
    rev = {v: k for k, v in discover.SP.items()}
    monkeypatch.setattr(discover, "_search", lambda topic, sp, region, n=25: calls[rev[sp]])
    d = discover.discover("gta 6", "BR")
    assert [x["viewers"] for x in d["live"]] == [900, 10]  # no duplicate of the live seen again in "week"
    assert [x["views"] for x in d["lives"]] == [99, 5]
    assert [x["views"] for x in d["videos"]] == [50, 7] and d["videos"][1]["kind"] == "short"
