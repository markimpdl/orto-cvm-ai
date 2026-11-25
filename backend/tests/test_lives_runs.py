"""A pasted link (the user's own video/live or another creator's) → the package, recorded per video id."""
import subprocess
from datetime import timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from cvm.config import settings
from cvm.lives import ingest, runs
from cvm.models import Channel, Setting, now

OWN = {"video_id": "AAAAAAAAAA1", "title": "Minha live", "url": "https://www.youtube.com/watch?v=AAAAAAAAAA1", "duration": 4.0,
       "channel_id": "UCabc", "uploader": "Cozinha da Vila"}
OTHER = {**OWN, "video_id": "ZZZZZZZZZZ9", "url": "https://www.youtube.com/watch?v=ZZZZZZZZZZ9", "channel_id": "UCother", "uploader": "Fulano"}


@pytest.fixture
def s(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    sess = Session(engine)
    sess.add(Channel(id=4, name="Studio", platform="youtube", language="pt-BR", youtube_channel_id="UCabc"))
    sess.commit()
    return sess


def _run(s, info, made, url=None, fetch=None):
    c = s.get(Channel, 4)
    return runs.process_link(s, c, url or info["url"], probe=lambda u: dict(info),
                             fetch=fetch or (lambda live, d: d / "v.mp4"),
                             package=lambda session, channel, live, video, third_party=None: made.append(third_party) or
                             {"long": 1, "shorts": [2], "moments": []})


def test_video_id_of_common_links():
    assert runs.video_id_of("https://www.youtube.com/watch?v=VNrA2RUoRKw&t=10") == "VNrA2RUoRKw"
    assert runs.video_id_of("https://youtu.be/VNrA2RUoRKw") == "VNrA2RUoRKw"
    assert runs.video_id_of("https://www.youtube.com/live/VNrA2RUoRKw?si=x") == "VNrA2RUoRKw"
    assert runs.video_id_of("https://www.youtube.com/shorts/VNrA2RUoRKw") == "VNrA2RUoRKw"
    assert runs.video_id_of("not a link") is None


def test_own_video_packages_without_third_party(s):
    made = []
    item = _run(s, OWN, made)
    assert made == [None] and item["status"] == "ok" and item["own"] is True


def test_other_creator_gets_credit_and_risk_tag(s):
    made = []
    item = _run(s, OTHER, made)
    assert made[0] == {"uploader": "Fulano", "url": OTHER["url"], "channel_id": "UCother"}
    assert item["own"] is False


def test_same_video_twice_is_refused(s):
    made = []
    _run(s, OWN, made)
    with pytest.raises(runs.AlreadyDone):
        runs.start_item(s, s.get(Channel, 4), "AAAAAAAAAA1")
    assert len(made) == 1


def test_working_video_blocks_until_its_lease_ends(s):
    c = s.get(Channel, 4)
    runs.start_item(s, c, "AAAAAAAAAA1")
    with pytest.raises(runs.AlreadyDone):
        runs.start_item(s, c, "AAAAAAAAAA1")
    row = s.get(Setting, "lives:4")
    v = dict(row.value)
    v["items"] = [{**v["items"][0], "at": (now() - timedelta(hours=runs.LEASE_H + 1)).isoformat()}]
    row.value = v
    s.add(row)
    s.commit()
    runs.start_item(s, c, "AAAAAAAAAA1")  # stale "working" (crashed run): allowed again


def test_not_ready_waits_without_error(s):
    def fetch(live, d):
        raise ingest.NotReady("YouTube ainda processa a gravação")
    item = _run(s, OWN, [], fetch=fetch)
    assert item["status"] == "waiting"
    runs.start_item(s, s.get(Channel, 4), "AAAAAAAAAA1")  # can be sent again later


def test_failure_is_recorded(s):
    def fetch(live, d):
        raise RuntimeError("download da live falhou: Private video")
    item = _run(s, OWN, [], fetch=fetch)
    assert item["status"] == "error" and "Private video" in item["error"]


def test_paused_channel_refused_before_anything(s):
    c = s.get(Channel, 4)
    c.status = "paused"
    s.add(c)
    s.commit()
    from cvm import guards
    with pytest.raises(guards.ChannelClosed):
        runs.check_channel(c)


def test_probe_refuses_unfinished_lives():
    for status in ("is_live", "is_upcoming", "post_live"):
        run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, f'{{"id": "a1", "live_status": "{status}"}}', "")  # noqa: E731
        with pytest.raises(ingest.NotReady):
            ingest.probe("https://youtu.be/a1", run=run)


def test_probe_reads_owner():
    out = '{"id": "a1", "title": "T", "live_status": "was_live", "duration": 7200, "channel_id": "UCx", "uploader": "Fulano"}'
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, out, "")  # noqa: E731
    info = ingest.probe("https://youtu.be/a1", run=run)
    assert info == {"video_id": "a1", "platform": "youtube", "title": "T", "url": "https://www.youtube.com/watch?v=a1",
                    "original_url": "https://youtu.be/a1", "duration": 7200.0, "channel_id": "UCx",
                    "uploader": "Fulano", "language": None}


def test_status_lists_items(s):
    _run(s, OWN, [])
    st = runs.status(s, s.get(Channel, 4))
    assert st["items"][0]["video_id"] == "AAAAAAAAAA1"


def test_bot_check_is_waiting_not_error():
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies")  # noqa: E731
    with pytest.raises(ingest.NotReady, match="YouTube"):
        ingest.probe("https://youtu.be/AAAAAAAAAA1", run=run)


def test_retry_tick_requeues_waiting_items(s, monkeypatch):
    c = s.get(Channel, 4)
    for vid in ("AAAAAAAAAA1", "BBBBBBBBBB2", "CCCCCCCCCC3"):
        runs._put(s, c, {"video_id": vid, "url": f"https://youtu.be/{vid}", "title": vid, "status": "waiting", "error": "x"})
    queued = []
    monkeypatch.setattr(runs, "_requeue", lambda session, channel, item: queued.append(item["video_id"]))
    runs.retry_waiting(s, c, limit=2)
    assert len(queued) == 2


def test_downloaded_video_skips_youtube(s, tmp_path):
    """A recording already on disk with known title/creator is packaged without asking YouTube again (anti-bot)."""
    c = s.get(Channel, 4)
    d = runs.lives_dir(4)
    d.mkdir(parents=True, exist_ok=True)
    (d / "live_ZZZZZZZZZZ9.mp4").write_bytes(b"x")
    runs._put(s, c, {"video_id": "ZZZZZZZZZZ9", "url": OTHER["url"], "title": "T", "uploader": "Fulano",
                     "own": False, "status": "waiting", "duration": 7200.0})

    def no_probe(url):
        raise AssertionError("YouTube asked again")
    made = []
    item = runs.process_link(s, c, OTHER["url"], probe=no_probe, fetch=lambda live, d: d / "live_ZZZZZZZZZZ9.mp4",
                             package=lambda session, channel, live, video, third_party=None: made.append((live, third_party)) or
                             {"long": None, "shorts": [1], "moments": []})
    assert item["status"] == "ok" and made[0][1]["uploader"] == "Fulano"
