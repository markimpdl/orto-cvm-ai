"""Chat replay as a signal for the best moments of a live (2026-11-22, IShowSpeed marathons): message rate per
second (superchats, gifts and new members weigh more), which raises the windows where the audience reacted most and
adds chat peaks the loudness/motion analysis missed."""
import json

from cvm.lives import chat


def _line(ms, kind="liveChatTextMessageRenderer"):
    return json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(ms),
                                                "actions": [{"addChatItemAction": {"item": {kind: {}}}}]}})


def test_density_counts_messages_and_weighs_paid_ones(tmp_path):
    f = tmp_path / "c.live_chat.json"
    f.write_text("\n".join([_line(1000), _line(1500), _line(2200, "liveChatPaidMessageRenderer"), "garbage"]))
    d = chat.density(f)
    assert d[1] == 2 and d[2] == chat.PAID_WEIGHT


def test_boost_raises_chat_heavy_moments_and_adds_peaks():
    dens = {s: 1 for s in range(0, 600)}
    for s in range(300, 330):
        dens[s] = 40  # the audience exploded here
    moments = [{"start": 0.0, "end": 30.0, "score": 1.0}, {"start": 100.0, "end": 130.0, "score": 1.2}]
    out = chat.boost(moments, dens, duration=600.0)
    top = max(out, key=lambda m: m["score"])
    assert top["start"] <= 300 < top["end"]
    assert all("chat_rate" in m for m in out)


def test_candidate_count_grows_with_live_length():
    from cvm.lives import viral

    assert viral.candidate_limit(600) == viral.LIMIT
    assert viral.candidate_limit(24 * 3600) == viral.MAX_LIMIT
    assert viral.LIMIT < viral.candidate_limit(6 * 3600) < viral.MAX_LIMIT


def test_chat_rate_survives_moment_selection():
    from cvm.visuals import highlights

    got = highlights.top_moments([{"start": 0.0, "end": 20.0, "score": 2.0, "chat_rate": 900.0, "chat_peak": True}],
                                 1)
    assert got[0]["chat_rate"] == 900.0 and got[0]["chat_peak"] is True


def test_package_uses_the_chat_when_the_live_has_one(tmp_path, monkeypatch):
    import subprocess

    from sqlmodel import Session, SQLModel, create_engine

    from cvm.config import settings
    from cvm.lives import package
    from cvm.models import Channel
    from cvm.visuals import highlights

    import cvm.workers.tasks as tasks
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(tasks, "run_job", lambda job_id: None)
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    s = Session(e)
    c = Channel(id=4, name="Studio", platform="youtube", language="pt-BR")
    s.add(c)
    s.commit()
    video = tmp_path / "v.mp4"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "4", "-shortest", str(video)], check=True)
    monkeypatch.setattr(highlights, "analyse", lambda paths, durs: [
        {"segments": [{"start": 0.0, "end": 4.0, "score": 1.0}], "whole": True,
         "moments": [{"start": 0.0, "end": 30.0, "score": 1.0}], "dropped": {"dull": 0, "repeat": 0}}])
    monkeypatch.setattr(chat, "fetch", lambda url, dest, vid, timeout=0: tmp_path / "c.json")
    monkeypatch.setattr(chat, "density", lambda p: {s_: 5.0 for s_ in range(0, 30)})
    seen = {}

    def pick(v, meta, title, own, language, **k):
        seen.update(meta=meta, duration=k.get("duration"))
        return [], "viral"

    live = {"video_id": "a1", "title": "Live", "url": "https://www.youtube.com/watch?v=a1", "duration": 7200.0}
    package.make_package(s, c, live, video, pick=pick,
                         third_party={"uploader": "IShowSpeed", "url": live["url"], "channel_id": "UCx"})
    assert all("chat_rate" in m for m in seen["meta"]["moments"])
    assert seen["duration"] == 7200.0


def test_viral_scoring_falls_back_to_codex_at_claudes_limit(monkeypatch):
    """At Claude's limit the viral score goes to Codex (user, 2026-11-22: "se o limite claude bater, use o codex"),
    instead of falling back to the loudness rule."""
    from datetime import UTC, datetime, timedelta

    from cvm import claude_limit
    from cvm.lives import viral

    def limited(*a, **k):
        raise claude_limit.ClaudeLimit(datetime.now(UTC) + timedelta(hours=1), "limit")

    monkeypatch.setattr(viral, "_codex", lambda prompt: {"result": '[{"i": 0, "score": 9, "hook": "x", "why": "y"}]'})
    rows = viral.score([{"start": 0.0, "end": 20.0, "text": "uau"}], "t", False, run=limited)
    assert rows and rows[0]["viral"] == 9
