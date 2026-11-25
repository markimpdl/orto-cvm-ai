"""The project page shows the voice the narration really uses: a video without its own choice gets the channel's
default (the trained voice), not the avatar's first listed voice (2026-11-22: it showed "Voz clonada (antiga)")."""
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm.api.projects import get_project
from cvm.models import Avatar, Channel, Job, Platform, Project, VoiceProfile


def test_project_shows_the_channel_voice_it_will_use():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        a = Avatar(id=1, name="Rafa", status="ready")
        s.add(a)
        s.add(Channel(id=4, name="pt", platform=Platform.youtube, language="pt-BR", avatar_id=1,
                      prefs={"voice_variant": "real"}))
        s.add_all([VoiceProfile(id=9, avatar_id=1, language="ar-AE", ref_audio_path="/a.wav", params={"variant": "clone"}),
                   VoiceProfile(id=23, avatar_id=1, language="pt-BR", ref_audio_path="/b.wav",
                                params={"variant": "real", "mlx_model": "/m"})])
        s.add(Project(id=1, channel_id=4, avatar_id=1, title="t", topic="t", languages=["pt-BR"]))
        s.add(Job(project_id=1, state="voicing"))
        s.commit()
        out = get_project(1, s)
        assert out["voice_variant"] is None and out["voice_effective"] == "real"
