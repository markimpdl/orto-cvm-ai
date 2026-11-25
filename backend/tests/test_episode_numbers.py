from huey import MemoryHuey
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from cvm import gpu
from cvm.channels import next_episode_number, number_episode
from cvm.models import Channel, Job, Project, Script
from cvm.workers import tasks


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def _episode(s, ch, title, lang="ar-AE", state="scripting", script=False):
    p = Project(topic=title, title=title, style="kids", channel_id=ch.id, languages=[lang])
    s.add(p)
    s.commit()
    job = Job(project_id=p.id, state=state)
    s.add(job)
    if script:
        s.add(Script(project_id=p.id, language=lang, content={"scenes": []}))
    s.commit()
    return p, job


def test_unnumbered_episode_gets_the_next_number():
    with Session(_db()) as s:
        ch = Channel(name="حكايات النجوم", platform="youtube", language="ar-AE", prefs={"last_episode": 14, "episode_max": {"ar-AE": 14}},
                     profile={"series_names": {"ar-AE": "Amigos do Cacau"}})
        s.add(ch)
        s.commit()
        _episode(s, ch, "Amigos do Cacau – Ep. 14")
        _episode(s, ch, "uma ideia sem número")  # an old unnumbered one does not push the count
        assert next_episode_number(s, ch, "ar-AE") == 15
        p = Project(topic="O lixo que o vento trouxe", title="O lixo que o vento trouxe", style="kids",
                    channel_id=ch.id, languages=["ar-AE"])
        number_episode(s, p)
        assert p.title == "Amigos do Cacau – Ep. 15: O lixo que o vento trouxe"
        assert p.topic.startswith("Episódio 15 da série animada Amigos do Cacau.")
        kept = Project(topic="x", title="Amigos do Cacau – Ep. 40", style="kids", channel_id=ch.id, languages=["ar-AE"])
        number_episode(s, kept)
        assert kept.title == "Amigos do Cacau – Ep. 40"  # a number set by hand stays
        other = Project(topic="x", title="x", style="explainer", channel_id=ch.id, languages=["ar-AE"])
        number_episode(s, other)
        assert other.title == "x"


def test_episode_script_waits_for_the_previous_one(monkeypatch):
    monkeypatch.setattr(gpu, "lock", MemoryHuey("test").lock_task("gpu"))
    engine = _db()
    monkeypatch.setattr(tasks, "engine", engine)
    monkeypatch.setattr(tasks.settings, "claude_auto_run", True)
    scheduled = []
    monkeypatch.setattr(tasks, "run_job", type("T", (), {"schedule": staticmethod(lambda args, delay: scheduled.append(delay))}))
    with Session(engine) as s:
        ch = Channel(name="Cacau", platform="youtube", language="pt-BR")
        s.add(ch)
        s.commit()
        _, j8 = _episode(s, ch, "Cacau – Ep. 8", "pt-BR", script=True)
        p9, j9 = _episode(s, ch, "Cacau – Ep. 9", "pt-BR")
        p10, j10 = _episode(s, ch, "Cacau – Ep. 10", "pt-BR")
        _episode(s, ch, "Cacau – Ep. 9", "en-US")  # another language: its own sequence
        assert tasks.previous_episode_pending(s, j9) is None
        assert tasks.previous_episode_pending(s, j10) == 9
        jid10, pid9 = j10.id, p9.id
    called = []
    monkeypatch.setitem(tasks.HANDLERS, "scripting", lambda job, session: called.append(job.id))
    tasks._drive(jid10, set())
    with Session(engine) as s:
        job = s.get(Job, jid10)
        assert not called and job.progress["waiting"] == "episode" and job.progress["after_episode"] == 9
        s.add(Script(project_id=pid9, language="pt-BR", content={"scenes": []}))  # Ep. 9 got its script
        s.commit()
    tasks._drive(jid10, set())
    assert called == [jid10]


def test_old_shared_episodes_do_not_hold_the_series():
    """The online cluster is gone (2026-11-24): a SharedEpisode row left from it, even one still "scripting" on
    another PC, never makes a local episode wait. Only the local Ep. N-1 counts."""
    from cvm.models import SharedEpisode

    with Session(_db()) as s:
        ch = Channel(name="Cacau", platform="youtube", language="pt-BR", uid="cacau-uid")
        s.add(ch)
        s.commit()
        _, j10 = _episode(s, ch, "Cacau – Ep. 10", "pt-BR")
        s.add(SharedEpisode(uid="mac-9", series_uid="cacau-uid", language="pt-BR", number=9, title="Cacau – Ep. 9",
                            state="scripting", machine="mac", ending=[]))
        s.commit()
        assert tasks.previous_episode_pending(s, j10) is None


def test_stuck_previous_episode_stops_holding_after_a_day():
    from datetime import datetime, timedelta, timezone

    with Session(_db()) as s:
        ch = Channel(name="Linha do Tempo", platform="youtube", language="pt-BR")
        s.add(ch)
        s.commit()
        _episode(s, ch, "Linha do Tempo – Ep. 25", "pt-BR")
        _, j26 = _episode(s, ch, "Linha do Tempo – Ep. 26", "pt-BR")
        assert tasks.previous_episode_pending(s, j26) == 25
        j26.progress = {"waiting": "episode", "since": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()}
        assert tasks.previous_episode_pending(s, j26) is None  # Ep. 25 never got written: stop waiting


def test_numbering_continues_without_any_sync():
    """Numbers never go backwards: next = 1 + max(local numbered titles, prefs.episode_max (left by the old
    cluster), prefs.last_episode (the series' own language)). Numbering writes nothing back to the channel."""
    with Session(_db()) as s:
        fresh = Channel(name="Dino Curioso", platform="youtube", language="pt-BR")
        s.add(fresh)
        s.commit()
        assert next_episode_number(s, fresh, "pt-BR") == 1
        _episode(s, fresh, "Dino Curioso – Ep. 1", "pt-BR")
        _episode(s, fresh, "Dino Curioso – Ep. 2", "pt-BR")
        assert next_episode_number(s, fresh, "pt-BR") == 3  # local titles only

        old = Channel(name="Cacau", platform="youtube", language="pt-BR",
                      prefs={"episode_max": {"pt-BR": 12, "en-US": 4}, "last_episode": 7})
        s.add(old)
        s.commit()
        _episode(s, old, "Cacau – Ep. 5", "pt-BR")
        assert next_episode_number(s, old, "pt-BR") == 13  # episode_max beats the local Ep. 5
        assert next_episode_number(s, old, "en-US") == 5  # per language
        assert next_episode_number(s, old, "ar-AE") == 1  # last_episode only counts in the series' language
        old.prefs = {"last_episode": 20}
        s.add(old)
        s.commit()
        assert next_episode_number(s, old, "pt-BR") == 21  # set by hand on the other machine
        _episode(s, old, "Cacau – Ep. 30", "pt-BR")
        assert next_episode_number(s, old, "pt-BR") == 31  # a higher local title wins
        p = Project(topic="O vento", title="O vento", style="kids", channel_id=old.id, languages=["pt-BR"])
        number_episode(s, p)
        assert p.title == "Cacau – Ep. 31: O vento"
        assert s.get(Channel, old.id).prefs == {"last_episode": 20}  # nothing written back


def test_packaging_titles_carry_the_projects_episode_number():
    from cvm.pipeline import episode_titles, fix_episode_number

    p = Project(topic="x", title="Amigos do Cacau – Ep. 13", style="kids")
    content = {"packaging": {"title": "أصدقاء الغابة – الحلقة 27: ليش ما يطير البطريق؟", "thumbnail_text": "ليش؟",
                             "alternatives": [{"title": "Ep. 33: A flauta", "thumbnail_text": "x"}, {"title": "sem número"}]}}
    episode_titles(None, p, content)
    assert content["packaging"]["title"] == "أصدقاء الغابة – الحلقة 13: ليش ما يطير البطريق؟"
    assert [a["title"] for a in content["packaging"]["alternatives"]] == ["Ep. 13: A flauta", "sem número"]
    # an unnumbered (standalone) episode: the number the scriptwriter made up goes away
    assert fix_episode_number("Amigos do Cacau – Ep. 10: O Gil e os sons da noite", None) == "O Gil e os sons da noite"
    other = {"packaging": {"title": "Top 10: Ep. 4 da novela"}}
    episode_titles(None, Project(topic="x", title="x", style="top-list"), other)
    assert other["packaging"]["title"] == "Top 10: Ep. 4 da novela"  # only kids episodes


def test_failed_previous_episode_does_not_block():
    with Session(_db()) as s:
        ch = Channel(name="Cacau", platform="youtube", language="pt-BR")
        s.add(ch)
        s.commit()
        _episode(s, ch, "Cacau – Ep. 9", "pt-BR", state="failed")
        _, j10 = _episode(s, ch, "Cacau – Ep. 10", "pt-BR")
        assert tasks.previous_episode_pending(s, j10) is None
