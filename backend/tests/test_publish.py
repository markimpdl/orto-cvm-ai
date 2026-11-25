from datetime import timedelta

from sqlmodel import Session, SQLModel, create_engine

from cvm.models import Channel, Job, Platform, Project, Publication, PublishMode, Render, now
from cvm.publish import service


def _setup(mode: PublishMode):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    ch = Channel(name="c", platform=Platform.youtube_shorts, publish_mode=mode)
    pr = Project(title="t", topic="t")
    s.add_all([ch, pr])
    s.commit()
    job = Job(project_id=pr.id, state="awaiting_publish_review")
    r = Render(project_id=pr.id, language="pt-BR", profile="youtube_shorts", path="/nonexistent.mp4", status="done")
    s.add_all([job, r])
    s.commit()
    return s, ch, pr, job, r


def test_manual_flow_reaches_published():
    s, ch, pr, job, r = _setup(PublishMode.manual)
    service.schedule(s, pr.id, [{"render_id": r.id, "channel_id": ch.id, "scheduled_at": (now() - timedelta(minutes=1)).isoformat()}])
    assert job.state == "scheduled"
    notes = []
    assert service.release_due(s, lambda *a: notes.append(a)) == 1
    s.refresh(job)
    assert job.state == "ready_for_manual_publish" and notes
    pub = s.get(Publication, 1)
    service.mark_published(s, pub, "https://youtube.com/shorts/x")
    s.refresh(job)
    assert job.state == "published" and pub.url.endswith("/x")
    service.unpublish(s, pub)
    s.refresh(job)
    assert job.state == "ready_for_manual_publish" and pub.status == "ready_for_manual_publish" and pub.url is None


def test_api_mode_without_credentials_falls_back_to_manual():
    s, ch, pr, job, r = _setup(PublishMode.api)
    service.schedule(s, pr.id, [{"render_id": r.id, "channel_id": ch.id}])
    service.release_due(s, lambda *a: None)
    pub = s.get(Publication, 1)
    assert pub.mode == PublishMode.manual and pub.status == "ready_for_manual_publish"


def test_future_publication_not_released():
    s, ch, pr, job, r = _setup(PublishMode.manual)
    service.schedule(s, pr.id, [{"render_id": r.id, "channel_id": ch.id, "scheduled_at": (now() + timedelta(hours=2)).isoformat()}])
    assert service.release_due(s, lambda *a: None) == 0


def test_publish_now_opens_the_kit_early(tmp_path, monkeypatch):
    from sqlmodel import Session, SQLModel, create_engine, select

    from cvm.models import Channel, Job, Platform, Project, Publication, Render
    from cvm.publish import service

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    ch, pr = Channel(name="c", platform=Platform.youtube_shorts), Project(title="t", topic="t")
    s.add_all([ch, pr])
    s.commit()
    r = Render(project_id=pr.id, language="pt-BR", profile="youtube_shorts", path="/x.mp4", status="done")
    s.add_all([r, Job(project_id=pr.id, state="scheduled")])
    s.commit()
    pub = Publication(render_id=r.id, channel_id=ch.id, status="scheduled")
    s.add(pub)
    s.commit()
    service.publish_now(s, pub)
    assert pub.status == "ready_for_manual_publish"
    assert s.exec(select(Job)).one().state == "ready_for_manual_publish"
