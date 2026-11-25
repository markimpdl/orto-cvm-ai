import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from cvm.api import projects
from cvm.config import settings
from cvm.models import Asset, Channel, Job, Platform, Project, Publication, Render, Script, Setting


def _setup(tmp_path, monkeypatch, state: str):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    ch = Channel(name="c", platform=Platform.youtube_shorts)
    keep, pr = Project(title="keep", topic="k"), Project(title="t", topic="t")
    s.add_all([ch, keep, pr])
    s.commit()
    r = Render(project_id=pr.id, language="pt-BR", profile="youtube_shorts", path="/x.mp4", status="done")
    s.add_all([r, Job(project_id=pr.id, state=state), Job(project_id=keep.id),
               Script(project_id=pr.id, language="pt-BR", content={}), Asset(project_id=pr.id, kind="visual", path="")])
    s.commit()
    s.add(Publication(render_id=r.id, channel_id=ch.id))
    s.commit()
    (tmp_path / "projects" / str(pr.id)).mkdir(parents=True)
    return s, pr, keep


def test_delete_removes_project_rows_and_files(tmp_path, monkeypatch):
    s, pr, keep = _setup(tmp_path, monkeypatch, "awaiting_approval")
    projects.delete_project(pr.id, session=s)
    for model in (Publication, Render, Asset, Script):
        assert not s.exec(select(model)).all()
    assert [j.project_id for j in s.exec(select(Job)).all()] == [keep.id]
    assert s.get(Project, keep.id) and not (tmp_path / "projects" / str(pr.id)).exists()


def test_busy_project_needs_force(tmp_path, monkeypatch):
    s, pr, _ = _setup(tmp_path, monkeypatch, "lipsyncing")
    with pytest.raises(HTTPException) as e:
        projects.delete_project(pr.id, session=s)
    assert e.value.status_code == 409
    projects.delete_project(pr.id, force=True, session=s)
    assert s.get(Project, pr.id) is None


def test_default_avatar_prefers_channel_then_single_ready(tmp_path, monkeypatch):
    from cvm.models import Avatar
    from cvm.suggest import default_avatar

    s, _, _ = _setup(tmp_path, monkeypatch, "awaiting_approval")
    assert default_avatar(s, None) is None
    me = Avatar(name="me", status="ready")
    s.add_all([me, Avatar(name="draft")])
    s.commit()
    assert default_avatar(s, None) == me.id
    other = Avatar(name="other", status="ready")
    s.add(other)
    s.commit()
    assert default_avatar(s, None) is None  # ambiguous: let the user pick
    ch = s.exec(select(Channel)).first()
    ch.avatar_id = other.id
    s.commit()
    assert default_avatar(s, ch) == other.id


def test_autopilot_queues_best_new_ideas_within_daily_limit(tmp_path, monkeypatch):
    from cvm import autopilot, suggest
    from cvm.models import Trend, TrendAnalysis

    s, _, _ = _setup(tmp_path, monkeypatch, "awaiting_approval")
    started = []
    monkeypatch.setattr(suggest, "new_project", lambda session, data: started.append(data) or _fake_project(session, data))
    trends = [Trend(source="google", region="BR", key=f"k{i}", title=f"T{i}") for i in range(4)]
    s.add_all(trends)
    s.commit()
    ideas = [{"trend_ids": [t.id], "topic": f"idea {i}", "style": "explainer", "potential": p}
             for i, (t, p) in enumerate(zip(trends, [90, 80, 78, 60]))]
    s.add(TrendAnalysis(status="done", items=ideas))
    s.commit()
    assert autopilot.enqueue(s) == []  # off by default
    autopilot.put(s, {"enabled": True, "max_per_day": 2})
    assert len(autopilot.enqueue(s)) == 2
    assert [d["title"] for d in started] == ["idea 0", "idea 1"] and all(d["autopilot"] for d in started)
    assert autopilot.enqueue(s) == []  # daily limit reached


def _fake_project(session, data):
    from cvm.models import Project

    p = Project.model_validate(data)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def test_presenter_overlay_needs_channel_option_avatar_and_matting(tmp_path, monkeypatch):
    from cvm.avatar import matting
    from cvm.models import Avatar
    from cvm.render import compose

    s, pr, _ = _setup(tmp_path, monkeypatch, "awaiting_approval")
    ch = s.exec(select(Channel)).first()
    av = Avatar(name="me", status="ready", base_video_dir=str(tmp_path))
    s.add(av)
    s.commit()
    pr.channel_id, pr.avatar_id = ch.id, av.id
    monkeypatch.setattr(matting, "matting_ready", lambda: True)
    assert not compose.presenter_overlay(s, pr)  # channel still on "scenes"
    ch.presenter = "overlay"
    assert compose.presenter_overlay(s, pr)
    monkeypatch.setattr(matting, "matting_ready", lambda: False)
    assert not compose.presenter_overlay(s, pr)  # RVM not installed: fall back to avatar scenes


def test_auto_approve_is_opt_in_per_gate(tmp_path, monkeypatch):
    from cvm import autopilot

    s, pr, _ = _setup(tmp_path, monkeypatch, "awaiting_approval")
    pr.channel_id = s.exec(select(Channel)).first().id
    s.add(pr)
    s.commit()
    job = s.exec(select(Job).where(Job.project_id == pr.id)).one()
    assert not autopilot.auto_approve(s, job) and job.state == "awaiting_approval"  # off by default
    autopilot.put(s, {"auto_approve_script": True})
    s.add(Setting(key="manual_review", value={"projects": [pr.id]}))
    s.commit()
    assert not autopilot.auto_approve(s, job) and job.state == "awaiting_approval"  # held for the user's review
    s.delete(s.get(Setting, "manual_review"))
    s.commit()
    assert autopilot.auto_approve(s, job) and job.state == "voicing"
    job.state = "awaiting_publish_review"
    s.add(job)
    s.commit()
    assert not autopilot.auto_approve(s, job)  # render approval still manual
    before = len(s.exec(select(Publication)).all())
    autopilot.put(s, {"auto_approve_render": True})
    assert autopilot.auto_approve(s, job) and job.state == "scheduled"
    assert len(s.exec(select(Publication)).all()) == before + 1


def test_channel_niche_ideas_become_projects_once(tmp_path, monkeypatch):
    from cvm import suggest
    from cvm.api import channels
    from cvm.models import TrendAnalysis

    s, _, _ = _setup(tmp_path, monkeypatch, "awaiting_approval")
    monkeypatch.setattr(suggest, "new_project", _fake_project)
    ch = s.exec(select(Channel)).first()
    ideas = [{"topic": f"ideia {i}", "style": "explainer", "trend_ids": [], "channel_id": ch.id, "potential": 90 - i} for i in range(3)]
    s.add(TrendAnalysis(channel_id=ch.id, status="done", items=ideas))
    s.commit()
    out = channels.produce_channel_ideas(ch.id, {"best": 2}, s)
    assert len(out["created"]) == 2
    made = s.exec(select(Project).where(Project.channel_id == ch.id)).all()
    assert {p.title for p in made} == {"ideia 0", "ideia 1"} and all(p.languages == [ch.language] for p in made)
    assert channels.produce_channel_ideas(ch.id, {"indexes": [0, 2]}, s)["created"] and \
        len(s.exec(select(Project).where(Project.channel_id == ch.id)).all()) == 3  # idea 0 not produced twice
    from cvm.api.trends import analyses
    assert analyses(session=s) == []  # niche ideas stay off the trends page
