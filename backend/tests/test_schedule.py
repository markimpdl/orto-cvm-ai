from datetime import UTC, datetime

from sqlmodel import Session, SQLModel, create_engine

from cvm import schedule
from cvm.models import Channel, Job, Platform, Project, Publication, Render

PROFILE = {
    "formats": [{"kind": "long", "min_s": 480, "max_s": 900, "per_week": 3},
                {"kind": "short", "min_s": 35, "max_s": 50, "per_week": 7}],
    "publish_slots": [{"format": "long", "days": ["mon", "wed", "fri"], "time": "15:00", "tz": "America/New_York"},
                      {"format": "short", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "time": "10:00",
                       "tz": "America/New_York"}],
}


def _db(profile=PROFILE, language="en-US"):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    ch = Channel(name="c", platform=Platform.youtube, language=language, profile=profile)
    pr = Project(title="Proj", topic="t")
    s.add_all([ch, pr])
    s.commit()
    s.add(Job(project_id=pr.id, state="scheduled"))
    s.commit()
    return s, ch, pr


def _pub(s, ch, pr, profile, at, status="scheduled", language="en-US", title=None):
    r = Render(project_id=pr.id, language=language, profile=profile, status="done", seo={"title": title} if title else {})
    s.add(r)
    s.commit()
    p = Publication(render_id=r.id, channel_id=ch.id, scheduled_at=at, status=status,
                    published_at=at if status == "published" else None)
    s.add(p)
    s.commit()
    return r, p


def test_us_dst_shifts_utc_and_home_time():
    s, ch, _ = _db()
    # Fri 31 Oct 2026 (EDT, UTC-4) → next Mon 10:00 short; after 1 Nov the US is on EST (UTC-5)
    before = schedule.next_slots(s, ch, "short", n=3, now=datetime(2026, 10, 31, 12, 0, tzinfo=UTC))
    assert [x["utc"] for x in before] == ["2026-10-31T14:00:00Z", "2026-11-01T15:00:00Z", "2026-11-02T15:00:00Z"]
    assert before[0]["home"].startswith("2026-10-31T18:00") and before[1]["home"].startswith("2026-11-01T19:00")
    assert before[1]["local"] == "2026-11-01T10:00:00-05:00" and before[0]["tz"] == "America/New_York"


def test_brazil_has_no_dst_and_lead_time():
    prof = {"formats": [{"kind": "long", "per_week": 2}],
            "publish_slots": [{"format": "long", "days": ["wed", "sat"], "time": "19:00", "tz": "America/Sao_Paulo"}]}
    s, ch, _ = _db(prof, "pt-BR")
    # Wed 18 Nov 2026 21:50 UTC = 18:50 in São Paulo: the 19:00 slot is 10 min away → skipped
    out = schedule.next_slots(s, ch, "long", n=2, now=datetime(2026, 11, 18, 21, 50, tzinfo=UTC))
    assert [x["local"] for x in out] == ["2026-11-21T19:00:00-03:00", "2026-11-25T19:00:00-03:00"]
    assert out[0]["home"] == "2026-11-22T02:00:00+04:00"


def test_taken_slots_are_skipped_per_format():
    s, ch, pr = _db()
    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)  # Mon 07:00 New York
    _pub(s, ch, pr, "youtube_shorts", datetime(2026, 11, 16, 15, 0, tzinfo=UTC))  # Mon 10:00 short
    _pub(s, ch, pr, "youtube_shorts", datetime(2026, 11, 17, 18, 30, tzinfo=UTC))  # Tue 13:30: custom time, same day
    shorts = schedule.next_slots(s, ch, "short", n=2, now=now)
    assert [x["local"][:10] for x in shorts] == ["2026-11-18", "2026-11-19"]
    longs = schedule.next_slots(s, ch, "long", n=1, now=now)  # shorts don't take the long slot
    assert longs[0]["local"] == "2026-11-16T15:00:00-05:00"


def test_week_goal_counts_scheduled_and_published_in_audience_week():
    s, ch, pr = _db()
    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)  # Wed; week = Mon 16 – Sun 22 Nov (New York)
    _pub(s, ch, pr, "youtube", datetime(2026, 11, 16, 20, 0, tzinfo=UTC), status="published")
    _pub(s, ch, pr, "youtube", datetime(2026, 11, 20, 20, 0, tzinfo=UTC))
    _pub(s, ch, pr, "youtube_shorts", datetime(2026, 11, 23, 3, 0, tzinfo=UTC))  # Sun 22:00 NY: still this week
    _pub(s, ch, pr, "youtube_shorts_cuts", datetime(2026, 11, 23, 6, 0, tzinfo=UTC))  # Mon 01:00 NY: next week
    _pub(s, ch, pr, "youtube_shorts", datetime(2026, 11, 16, 5, 0, tzinfo=UTC))  # Mon 00:00 NY: this week
    _pub(s, ch, pr, "youtube_shorts", datetime(2026, 11, 17, 15, 0, tzinfo=UTC), status="failed")
    _pub(s, ch, pr, "youtube_shorts", datetime(2026, 11, 17, 15, 0, tzinfo=UTC), language="pt-BR")  # other language
    goal = {g["kind"]: g for g in schedule.week_goal(s, ch, now)}
    assert goal["long"] == {"kind": "long", "target": 3, "scheduled": 1, "published": 1, "remaining": 1}
    assert goal["short"]["scheduled"] == 2 and goal["short"]["remaining"] == 5


def test_week_calendar_fills_slots():
    s, ch, pr = _db()
    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    _pub(s, ch, pr, "youtube", datetime(2026, 11, 18, 20, 0, tzinfo=UTC), title="Long one")
    _pub(s, ch, pr, "youtube", datetime(2026, 11, 17, 20, 0, tzinfo=UTC), title="Off-slot")  # Tue: no long slot
    cal = schedule.week_calendar(s, ch, now)
    assert [d["weekday"] for d in cal] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] and cal[0]["date"] == "2026-11-16"
    wed = {x["kind"]: x for x in cal[2]["slots"]}
    assert wed["long"]["publication"]["title"] == "Long one" and wed["short"]["publication"] is None
    assert cal[1]["extra"][0]["title"] == "Off-slot"
    assert len(cal[0]["slots"]) == 2 and len(cal[1]["slots"]) == 1


def test_episode_channels_and_profile_kinds():
    prof = {"formats": [{"kind": "episode", "per_week": 1}, {"kind": "short", "per_week": 3}],
            "publish_slots": [{"format": "episode", "days": ["thu"], "time": "17:00", "tz": "Asia/Dubai"}]}
    s, ch, pr = _db(prof, "ar-AE")
    assert schedule.kind_of_profile(ch, "youtube") == "episode"
    assert schedule.kind_of_profile(ch, "youtube_shorts_cuts") == "short"
    out = schedule.next_slots(s, ch, "long", n=1, now=datetime(2026, 11, 13, 0, 0, tzinfo=UTC))  # long → episode
    assert out[0]["local"] == "2026-11-19T17:00:00+04:00" and out[0]["kind"] == "episode"
    r, _ = _pub(s, ch, pr, "youtube", datetime(2026, 11, 19, 13, 0, tzinfo=UTC), language="ar-AE")
    r2 = Render(project_id=pr.id, language="ar-AE", profile="youtube", status="done")
    s.add(r2)
    s.commit()
    assert schedule.slot_for(s, ch, r2, now=datetime(2026, 11, 13, tzinfo=UTC)) == datetime(2026, 11, 26, 13, 0, tzinfo=UTC)
    r3 = Render(project_id=pr.id, language="ar-AE", profile="youtube_shorts", status="done")
    s.add(r3)
    s.commit()
    assert schedule.slot_for(s, ch, r3) is None  # no short slots: goes now, as before


def test_fallback_without_publish_slots():
    s, ch, _ = _db({}, "pt-BR")
    ch.schedule = {"slots": ["Mon 18:00"]}
    out = schedule.next_slots(s, ch, "short", n=2, now=datetime(2026, 11, 13, 12, 0, tzinfo=UTC))
    assert len(out) == 2 and all(datetime.fromisoformat(x["utc"].replace("Z", "+00:00")).astimezone().weekday() == 0
                                 for x in out)
    ch.schedule = {}
    assert len(schedule.next_slots(s, ch, "short", now=datetime(2026, 11, 13, 12, 0, tzinfo=UTC))) == 2
    assert schedule.week(s, ch)["goal"] == []


def test_service_schedule_without_time_takes_next_free_slots():
    from cvm.publish import service

    s, ch, pr = _db()
    s.get(Job, 1).state = "awaiting_publish_review"
    r1 = Render(project_id=pr.id, language="en-US", profile="youtube_shorts", status="done")
    r2 = Render(project_id=pr.id, language="en-US", profile="youtube_shorts", status="done")
    s.add_all([r1, r2])
    s.commit()
    p1, p2 = service.schedule(s, pr.id, [{"render_id": r1.id, "channel_id": ch.id}, {"render_id": r2.id, "channel_id": ch.id}])
    a, b = sorted([schedule._utc(p1.scheduled_at), schedule._utc(p2.scheduled_at)])
    assert a.astimezone(schedule.ZoneInfo("America/New_York")).hour == 10 and (b - a).days == 1
