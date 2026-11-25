"""User's rule (2026-11-22): "terminar é mais importante que começar" — with too many unfinished videos, nothing new
is created on its own (daily auto cuts, autopilot); suggestions are still saved and the user's own picks still go in."""
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm import queueing
from cvm.models import Channel, Job, Project


def _s(n_unfinished, extra_states=()):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    s.add(Channel(id=4, name="Studio", platform="youtube", language="pt-BR"))
    states = ["voicing"] * n_unfinished + list(extra_states)
    for i, st in enumerate(states, 1):
        s.add(Project(id=i, channel_id=4, title=f"p{i}", topic="t", languages=["pt-BR"]))
        s.add(Job(id=i, project_id=i, state=st))
    s.commit()
    return s


def test_unfinished_counts_only_videos_in_production():
    s = _s(3, ["published", "scheduled", "awaiting_publish_review", "ready_for_manual_publish", "failed",
               "awaiting_approval", "scripting"])
    assert queueing.unfinished(s) == 5  # 3 voicing + awaiting_approval + scripting
    assert queueing.too_busy(s) is False


def test_too_busy_over_the_limit():
    assert queueing.too_busy(_s(queueing.WIP_MAX)) is True
    assert queueing.too_busy(_s(queueing.WIP_MAX - 1)) is False


def test_daily_keeps_suggestions_but_queues_no_cut_when_busy(monkeypatch):
    from datetime import UTC, datetime

    from cvm.grow import daily

    s = _s(queueing.WIP_MAX)
    c = s.get(Channel, 4)
    c.prefs = {"viral": {"topics": ["GTA 6"], "daily_credits": 40, "auto_cuts": 2}}
    s.add(c)
    s.commit()
    queued = []
    monkeypatch.setattr(daily, "_queue_cut", lambda session, channel, item: queued.append(item) or True)
    item = {"video_id": "abcdefghijk", "url": "u", "vph": 100, "duration": 60}
    daily.run_channel(s, c, search=lambda t, l: {"topic": t, "items": [item]},
                      now=datetime(2026, 11, 22, 4, 0, tzinfo=UTC))
    assert queued == []


def test_autopilot_creates_nothing_when_busy(monkeypatch):
    from cvm import autopilot

    s = _s(queueing.WIP_MAX)
    monkeypatch.setattr(autopilot, "get", lambda session: {"enabled": True})
    assert autopilot.enqueue(s) == []
