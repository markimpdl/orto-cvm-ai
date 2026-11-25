"""Em alta "Produzir quando acabar": wait for a live's recording, then package it through the Crescer flow."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from cvm.lives import after_live, platforms
from cvm.lives.ingest import NotReady


def test_live_channel_links():
    assert platforms.live_channel("https://www.twitch.tv/gaules")["archives"].endswith("/gaules/videos?filter=archives&sort=time")
    assert platforms.live_channel("https://kick.com/gaules?x=1")["url"] == "https://kick.com/gaules"
    assert platforms.live_channel("https://www.twitch.tv/videos/123") is None
    assert platforms.live_channel("https://www.youtube.com/watch?v=abcdefghijk") is None


def test_wait_url_round_trip():
    at = datetime(2026, 11, 24, 5, 0, tzinfo=UTC)
    u = after_live.wait_url("https://www.twitch.tv/gaules/", at)
    assert u == "https://www.twitch.tv/gaules#cvm-after=2026-11-24T05:00:00Z"
    assert after_live.since_of(u) == at and after_live.strip(u) == "https://www.twitch.tv/gaules"
    assert not after_live.expired(u, at + timedelta(hours=23)) and after_live.expired(u, at + timedelta(hours=25))


def _runner(on_air: dict | None, entries: list, vod: dict):
    def run(args, **_):
        url = args[-1]
        if "--flat-playlist" in args:
            out = {"entries": entries}
        elif "/videos/" in url:
            out = vod
        else:
            out = on_air
        return SimpleNamespace(returncode=0 if out is not None else 1, stdout=json.dumps(out or {}), stderr="")
    return run


def test_twitch_waits_while_on_air_then_takes_the_new_vod():
    at = datetime(2026, 11, 24, 5, 0, tzinfo=UTC)
    u = after_live.wait_url("https://www.twitch.tv/gaules", at)
    vod = {"url": "https://www.twitch.tv/videos/99"}
    with pytest.raises(NotReady):
        after_live.resolve(u, _runner({"is_live": True}, [vod], {}))
    started = (at - timedelta(hours=3)).timestamp()
    assert after_live.resolve(u, _runner(None, [vod], {"timestamp": started})) == "https://www.twitch.tv/videos/99"


def test_an_older_vod_is_never_taken():
    at = datetime(2026, 11, 24, 5, 0, tzinfo=UTC)
    u = after_live.wait_url("https://kick.com/gaules", at)
    old = (at - timedelta(days=2)).timestamp()
    with pytest.raises(NotReady):
        after_live.resolve(u, _runner(None, [{"url": "https://kick.com/gaules/videos/abc"}], {"timestamp": old}))


def test_youtube_link_passes_through():
    u = after_live.wait_url("https://www.youtube.com/watch?v=abcdefghijk")
    assert after_live.resolve(u, _runner(None, [], {})) == "https://www.youtube.com/watch?v=abcdefghijk"


def test_remove_viral_search(tmp_path, monkeypatch):
    from sqlmodel import Session, SQLModel, create_engine

    from cvm.grow import viral_search

    eng = create_engine(f"sqlite:///{tmp_path}/t.db")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        viral_search.save(s, 1, {"topic": "GTA 6", "at": "a"})
        viral_search.save(s, 1, {"topic": "Lisboa", "at": "b"})
        assert viral_search.remove(s, 1, "a") and not viral_search.remove(s, 1, "zz")
        from cvm.models import Setting
        assert [x["topic"] for x in s.get(Setting, "viral:1").value["searches"]] == ["Lisboa"]


def test_vidiq_manual_sync(tmp_path):
    from sqlmodel import Session, SQLModel, create_engine

    from cvm.grow import vidiq_stats

    eng = create_engine(f"sqlite:///{tmp_path}/t.db")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        assert vidiq_stats.start_sync(s, 4) and not vidiq_stats.start_sync(s, 4)
        vidiq_stats.run_sync(s, 4, "UC1", fetch_fn=lambda _: {"subscribers": 510, "at": "x"})
        assert vidiq_stats.load(s, 4)["subscribers"] == 510 and not vidiq_stats.sync_state(s, 4)["running"]
        assert vidiq_stats.start_sync(s, 4)
        vidiq_stats.run_sync(s, 4, "UC1", fetch_fn=lambda _: None)
        assert vidiq_stats.sync_state(s, 4)["error"]
