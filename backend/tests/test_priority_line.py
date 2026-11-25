"""A video the user puts ahead (Setting "priority_projects", e.g. a trend with a short window) goes first in every
line, before videos further along (2026-11-22, Bistecone Shorts)."""
from types import SimpleNamespace

from cvm.workers import tasks


def _job(pid, state, at):
    return SimpleNamespace(id=pid, project_id=pid, state=state, progress={},
                           history=[{"to": state, "at": at}])


def test_priority_video_goes_first(monkeypatch):
    monkeypatch.setattr(tasks, "priority_projects", lambda: {9})
    late = _job(9, "voicing", "2026-11-22T12:00:00")
    ahead = _job(1, "lipsyncing", "2026-11-21T10:00:00")
    assert sorted([ahead, late], key=tasks.line_key)[0] is late


def test_order_unchanged_without_priority(monkeypatch):
    monkeypatch.setattr(tasks, "priority_projects", lambda: set())
    late = _job(9, "voicing", "2026-11-22T12:00:00")
    ahead = _job(1, "lipsyncing", "2026-11-21T10:00:00")
    assert sorted([late, ahead], key=tasks.line_key)[0] is ahead
