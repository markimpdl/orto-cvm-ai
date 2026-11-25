"""Daily viral research (user's rule, 2026-11-21: "essas análises ocorram diariamente, para que tenhamos vídeos todo
dia"): from 07:00 in the studio's timezone (settings.default_tz), once a day, every active channel with `prefs.viral.topics` gets a fresh search per topic
(cvm/grow/viral_search.py) until `prefs.viral.daily_credits` (default 40, ~15 per search) is spent. Production still
waits for the user's "Cortar este". A search running for a channel is marked in the Setting `viral:{id}` → running_since
(one at a time, freed after RUN_LEASE_S).
"""

from datetime import UTC, datetime
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from cvm.config import home_tz
from cvm.models import Channel, Setting

HOUR = 7
CREDITS_PER_SEARCH = 15
RUN_LEASE_S = 30 * 60
AUTO_CUT_MAX_S = 2 * 3600


def _queue_cut(session: Session, channel: Channel, item: dict) -> bool:
    """Claim the video (no repeats) and package it in the worker; False when it was done or is being done."""
    from cvm.api import grow
    from cvm.lives import runs

    try:
        runs.check_channel(channel)
        runs.start_item(session, channel, item["video_id"], url=item["url"], title=item.get("title") or item["url"],
                        auto=True)
    except Exception:  # noqa: BLE001 — AlreadyDone / closed channel
        return False
    grow._in_background(grow._job_link, channel.id, item["url"], None)
    return True


def _row(session: Session, channel_id: int) -> Setting:
    return session.get(Setting, f"viral:{channel_id}") or Setting(key=f"viral:{channel_id}", value={})


def _set(session: Session, channel_id: int, **fields) -> None:
    row = _row(session, channel_id)
    row.value = {**(row.value or {}), **fields}
    flag_modified(row, "value")
    session.add(row)
    session.commit()


def prefs(channel: Channel) -> dict:
    v = (channel.prefs or {}).get("viral", {})
    return {"topics": [t for t in v.get("topics", []) if str(t).strip()], "language": v.get("language", "pt"),
            "daily_credits": int(v.get("daily_credits", 40)), "auto_cuts": int(v.get("auto_cuts", 2))}


def due(session: Session, channel: Channel, now: datetime | None = None) -> bool:
    local = (now or datetime.now(UTC)).astimezone(home_tz())
    if channel.status != "active" or not prefs(channel)["topics"] or local.hour < HOUR:
        return False
    return (_row(session, channel.id).value or {}).get("last_daily") != local.date().isoformat()


def running(session: Session, channel_id: int) -> bool:
    since = (_row(session, channel_id).value or {}).get("running_since")
    return bool(since) and (datetime.now(UTC) - datetime.fromisoformat(since)).total_seconds() < RUN_LEASE_S


def start(session: Session, channel_id: int) -> bool:
    """Mark a search as running; False when one already is."""
    if running(session, channel_id):
        return False
    _set(session, channel_id, running_since=datetime.now(UTC).isoformat())
    return True


def finish(session: Session, channel_id: int) -> None:
    _set(session, channel_id, running_since=None)


def run_channel(session: Session, channel: Channel, search=None, now: datetime | None = None) -> None:
    from cvm.grow import viral_search

    search = search or viral_search.search
    p = prefs(channel)
    _set(session, channel.id, last_daily=(now or datetime.now(UTC)).astimezone(home_tz()).date().isoformat())
    budget = p["daily_credits"]
    found: list[dict] = []
    from cvm.publish import analytics

    if channel.youtube_channel_id and not analytics.connected(channel) and budget >= 10:
        from cvm.grow import vidiq_stats  # monetization numbers while Google Analytics is not connected

        if snap := vidiq_stats.fetch(channel.youtube_channel_id):
            vidiq_stats.save(session, channel.id, snap)
        budget -= 10
    topics = p["topics"]
    start_at = int((_row(session, channel.id).value or {}).get("next_topic", 0)) % max(1, len(topics))
    done_n = 0
    for entry in topics[start_at:] + topics[:start_at]:  # rotation: a short budget still covers every topic in turn
        if budget < CREDITS_PER_SEARCH:
            break
        done_n += 1
        topic, _, lang = (x.strip() for x in str(entry).partition("|"))  # "IShowSpeed GTA 6 | en": own language
        result = {**search(topic, lang if lang in ("pt", "en") else p["language"]), "daily": True}
        viral_search.save(session, channel.id, result)
        budget -= CREDITS_PER_SEARCH
        found.extend(result.get("items", []))
    if topics:
        _set(session, channel.id, next_topic=(start_at + done_n) % len(topics))
    # the day's best suggestions become cuts on their own (user, 2026-11-21: "o que tiver em alta você que deve me
    # fornecer"); they still wait for his approval like everything else
    from cvm import queueing

    if queueing.too_busy(session):  # finish before starting: suggestions stay in the Cortes tab, no new cuts today
        return
    queued = 0
    for item in sorted(found, key=lambda i: -(i.get("vph") or 0)):
        if queued >= p["auto_cuts"]:
            break
        if (item.get("duration") or 0) > AUTO_CUT_MAX_S:
            continue  # multi-hour streams: too big to fetch blind every day
        if _queue_cut(session, channel, item):
            queued += 1


def run_all(session: Session | None = None) -> None:
    """Never raises: one channel's failure doesn't skip the others."""
    import logging

    from cvm.db import engine

    s = session or Session(engine)
    try:
        for c in s.exec(select(Channel)).all():
            if due(s, c) and start(s, c.id):
                try:
                    run_channel(s, c)
                except Exception as e:  # noqa: BLE001
                    logging.getLogger(__name__).warning("daily viral research of channel %s failed: %s", c.id, e)
                finally:
                    finish(s, c.id)
    finally:
        if session is None:
            s.close()
