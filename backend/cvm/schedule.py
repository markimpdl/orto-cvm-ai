"""Publishing calendar per channel (docs/plano-canais-e-voz.md, Phases 2–3): the week's goal per format, the weekly
calendar and the next free publishing slots, all in the audience's timezone (zoneinfo, so US DST is right) with the
studio's own time beside it (settings.default_tz / CVM_DEFAULT_TZ, else the machine's timezone).

`Channel.profile.formats[{kind, per_week}]` sets the goal and `publish_slots[{format, days[], time, tz}]` the times.
A publication fills the slot of its format on the same audience-local day (the nearest one when there are several),
so a video scheduled at a custom time still counts for that day's slot."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from cvm.config import home_tz
from cvm.models import Channel, Project, Publication, Render

DEFAULT_TZ = {"pt-BR": "America/Sao_Paulo", "en-US": "America/New_York", "ar-AE": "Asia/Dubai"}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
LEAD = timedelta(minutes=15)  # a slot closer than this is not offered any more
COUNTED = ("scheduled", "ready_for_manual_publish", "published")
SHORT_PROFILES = ("youtube_shorts", "instagram", "tiktok")


def _now(now: datetime | None) -> datetime:
    now = now or datetime.now(UTC)
    return now if now.tzinfo else now.replace(tzinfo=UTC)


def _utc(d: datetime) -> datetime:
    """DB datetimes are naive UTC (SQLite drops the offset)."""
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _profile(channel: Channel) -> dict:
    return channel.profile or {}


def audience_tz(channel: Channel) -> ZoneInfo:
    for s in _profile(channel).get("publish_slots") or []:
        if s.get("tz"):
            try:
                return ZoneInfo(s["tz"])
            except Exception:  # noqa: BLE001  (typo in the form: fall back to the language's zone)
                break
    return ZoneInfo(DEFAULT_TZ.get(channel.language, "America/Sao_Paulo"))


def _slot_tz(channel: Channel, slot: dict) -> ZoneInfo:
    try:
        return ZoneInfo(slot["tz"]) if slot.get("tz") else audience_tz(channel)
    except Exception:  # noqa: BLE001
        return audience_tz(channel)


def _kinds(channel: Channel) -> list[str]:
    return [f.get("kind") for f in _profile(channel).get("formats") or [] if f.get("kind")]


def kind_of_profile(channel: Channel, profile: str, style: str | None = None) -> str:
    """Render profile → format kind: vertical renders and cuts are Shorts; the 16:9 render is the channel's long
    format ("episode" on series channels, "long" otherwise)."""
    if profile.endswith("_cuts") or profile in SHORT_PROFILES:
        return "short"
    kinds = _kinds(channel)
    if "episode" in kinds and ("long" not in kinds or style == "kids"):
        return "episode"
    return "long"


def resolve_kind(channel: Channel, kind: str) -> str:
    """"long" and "episode" stand for each other when the channel only has the other one."""
    have = {s.get("format") for s in _profile(channel).get("publish_slots") or []} | set(_kinds(channel))
    if kind not in have:
        if kind == "long" and "episode" in have:
            return "episode"
        if kind == "episode" and "long" in have:
            return "long"
    return kind


def _slots(channel: Channel, kind: str | None = None) -> list[dict]:
    return [s for s in _profile(channel).get("publish_slots") or []
            if s.get("time") and s.get("days") and (kind is None or s.get("format") == kind)]


def _hm(s: str) -> time:
    h, _, m = str(s).partition(":")
    return time(int(h or 0), int(m or 0))


def _occurrences(channel: Channel, start: date, end: date, kind: str | None = None) -> list[dict]:
    """Every slot between two audience-local dates (inclusive): {kind, at (aware, slot tz), day (audience date)}."""
    tz = audience_tz(channel)
    out = []
    for slot in _slots(channel, kind):
        stz, days = _slot_tz(channel, slot), [str(d).lower()[:3] for d in slot["days"]]
        d = start - timedelta(days=1)  # a slot in another zone can land on the neighbouring audience day
        while d <= end + timedelta(days=1):
            if DAYS[d.weekday()] in days:
                at = datetime.combine(d, _hm(slot["time"]), tzinfo=stz)
                day = at.astimezone(tz).date()
                if start <= day <= end:
                    out.append({"kind": slot.get("format"), "at": at, "day": day})
            d += timedelta(days=1)
    return sorted(out, key=lambda o: o["at"])


def _publications(session: Session, channel: Channel, start: datetime, end: datetime) -> list[dict]:
    """The channel's counted publications in its language between two UTC instants: {pub, kind, at (UTC), title}."""
    rows = session.exec(select(Publication, Render).join(Render, Render.id == Publication.render_id)
                        .where(Publication.channel_id == channel.id)).all()
    out = []
    for pub, render in rows:
        if pub.status not in COUNTED or (render.language and render.language != channel.language):
            continue  # other languages: posts from before "one channel = one language" (they went elsewhere)
        when = pub.published_at if pub.status == "published" and pub.published_at else pub.scheduled_at
        if not when:
            continue
        when = _utc(when)
        if not start <= when < end:
            continue
        project = session.get(Project, render.project_id)
        out.append({"pub": pub, "at": when, "project_id": render.project_id,
                    "kind": kind_of_profile(channel, render.profile, project.style if project else None),
                    "title": (render.seo or {}).get("title") or (project.title if project else None)})
    return sorted(out, key=lambda p: p["at"])


def _assign(channel: Channel, occs: list[dict], pubs: list[dict]) -> list[dict]:
    """Put each publication in the nearest free slot of its kind on its audience-local day; returns the leftovers."""
    tz, extra = audience_tz(channel), []
    for p in pubs:
        day = p["at"].astimezone(tz).date()
        free = [o for o in occs if o["day"] == day and resolve_kind(channel, o["kind"]) == resolve_kind(channel, p["kind"])
                and "pub" not in o]
        if free:
            min(free, key=lambda o: abs(o["at"] - p["at"]))["pub"] = p
        else:
            extra.append(p)
    return extra


def _week_bounds(channel: Channel, now: datetime) -> tuple[date, datetime, datetime]:
    tz = audience_tz(channel)
    monday = now.astimezone(tz).date() - timedelta(days=now.astimezone(tz).weekday())
    start = datetime.combine(monday, time(0), tzinfo=tz)
    end = datetime.combine(monday + timedelta(days=7), time(0), tzinfo=tz)
    return monday, start.astimezone(UTC), end.astimezone(UTC)


def week_goal(session: Session, channel: Channel, now: datetime | None = None) -> list[dict]:
    """Per format: {kind, target (per_week), scheduled, published, remaining} for this Mon–Sun in the audience tz."""
    _, start, end = _week_bounds(channel, _now(now))
    pubs = _publications(session, channel, start, end)
    out = []
    for f in _profile(channel).get("formats") or []:
        kind = f.get("kind")
        if not kind:
            continue
        mine = [p for p in pubs if resolve_kind(channel, p["kind"]) == resolve_kind(channel, kind)]
        published = sum(p["pub"].status == "published" for p in mine)
        scheduled = len(mine) - published
        target = f.get("per_week") or 0
        out.append({"kind": kind, "target": target, "scheduled": scheduled, "published": published,
                    "remaining": max(0, target - scheduled - published)})
    return out


def _item(channel: Channel, at: datetime, kind: str | None) -> dict:
    tz, home = audience_tz(channel), home_tz()
    return {"kind": kind, "utc": at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "local": at.astimezone(tz).isoformat(), "tz": tz.key,
            "home": at.astimezone(home).isoformat(), "home_tz": home.key}


def _fallback(channel: Channel, n: int, now: datetime) -> list[datetime]:
    """The old behaviour: `schedule.slots` ("Mon 18:00", machine time), else tonight 20:00 / tomorrow 12:00."""
    local_now = now.astimezone()  # the machine's zone
    out = []
    for d in range(15):
        day = local_now.date() + timedelta(days=d)
        for slot in (channel.schedule or {}).get("slots") or []:
            name, _, hm = str(slot).partition(" ")
            if name.lower()[:3] == DAYS[day.weekday()]:
                at = datetime.combine(day, _hm(hm or "0:00")).astimezone()
                if at > now + LEAD:
                    out.append(at)
    if out:
        return sorted(out)[:n]
    today = local_now.date()
    tonight = datetime.combine(today, time(20)).astimezone()
    tomorrow = datetime.combine(today + timedelta(days=1), time(12)).astimezone()
    first = tonight if tonight > now + LEAD else datetime.combine(today + timedelta(days=1), time(20)).astimezone()
    return sorted([first, tomorrow])


def next_slots(session: Session, channel: Channel, kind: str, n: int = 6, now: datetime | None = None) -> list[dict]:
    """The next `n` free publishing times of that format: [{kind, utc, local, tz, home, home_tz}]. A slot is taken when a
    scheduled/published post of the same format already sits on it (see `_assign`)."""
    now = _now(now)
    kind = resolve_kind(channel, kind)
    if not _slots(channel, kind):
        return [_item(channel, at, kind) for at in _fallback(channel, n, now)]
    tz = audience_tz(channel)
    start = now.astimezone(tz).date()
    out: list[dict] = []
    for weeks in (2, 6, 26):  # widen until enough free slots (a weekly slot fully booked for weeks)
        end = start + timedelta(days=7 * weeks)
        occs = _occurrences(channel, start, end, kind)
        _assign(channel, occs, _publications(session, channel, datetime.combine(start, time(0), tzinfo=tz).astimezone(UTC),
                                             datetime.combine(end + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)))
        out = [_item(channel, o["at"], kind) for o in occs if "pub" not in o and o["at"] > now + LEAD]
        if len(out) >= n:
            break
    return out[:n]


def slot_for(session: Session, channel: Channel, render: Render, now: datetime | None = None) -> datetime | None:
    """When an approved render goes out if no time was picked: the next free slot of its format (UTC), or None when
    the channel has no publish_slots for it (then it goes now, as before)."""
    project = session.get(Project, render.project_id)
    kind = resolve_kind(channel, kind_of_profile(channel, render.profile, project.style if project else None))
    if not _slots(channel, kind):
        return None
    free = next_slots(session, channel, kind, n=1, now=now)
    return datetime.fromisoformat(free[0]["utc"].replace("Z", "+00:00")) if free else None


def week_calendar(session: Session, channel: Channel, now: datetime | None = None) -> list[dict]:
    """This week (Mon–Sun, audience tz): [{date, weekday, slots: [{kind, utc, local, tz, home, home_tz, publication}],
    extra: [publications outside any slot]}]."""
    monday, start, end = _week_bounds(channel, _now(now))
    tz = audience_tz(channel)
    occs = _occurrences(channel, monday, monday + timedelta(days=6))
    extra = _assign(channel, occs, _publications(session, channel, start, end))

    def pub(p: dict | None) -> dict | None:
        if not p:
            return None
        return {"id": p["pub"].id, "title": p["title"], "project_id": p["project_id"], "status": p["pub"].status, "url": p["pub"].url,
                "kind": p["kind"], "at": p["at"].isoformat().replace("+00:00", "Z")}

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        days.append({
            "date": d.isoformat(), "weekday": DAYS[i],
            "slots": [{**_item(channel, o["at"], o["kind"]), "publication": pub(o.get("pub"))} for o in occs if o["day"] == d],
            "extra": [pub(p) for p in extra if p["at"].astimezone(tz).date() == d],
        })
    return days


def week(session: Session, channel: Channel, now: datetime | None = None) -> dict:
    """Everything the channel panel shows: goal, calendar and the next free slots per format."""
    now = _now(now)
    monday, _, _ = _week_bounds(channel, now)
    # formats with slots; a channel without publish_slots gets the old suggestions under its first format
    kinds = list(dict.fromkeys(s.get("format") for s in _slots(channel))) or _kinds(channel)[:1] or ["long"]
    return {"tz": audience_tz(channel).key, "week_start": monday.isoformat(),
            "goal": week_goal(session, channel, now), "calendar": week_calendar(session, channel, now),
            "next_slots": {k: next_slots(session, channel, k, n=4, now=now) for k in kinds if k}}
