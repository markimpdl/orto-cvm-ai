"""Weekly data cycle (docs/plano-canais-e-voz.md, Phase 8): every Monday 08:00 in the studio's timezone
(settings.default_tz, else the machine's), for each active channel with
YouTube Analytics connected, pull 7/28-day numbers, find the most viewed and the best-retaining video, and have the
trend-analyst (Claude subscription via cvm.scripting.agents: obeys Claude's limit, Codex fallback) derive 3 ideas from each plus 1 title pattern. The ideas land in the
channel's idea queue (a TrendAnalysis with channel_id); the pattern waits for the user's approval
(POST /channels/{id}/title-patterns/approve → profile.title_patterns, which brief_text feeds to the agents).

State lives in Setting rows: "weekly_cycle" (last ISO week run) and "channel_weekly:<id>" (analysis id + pattern)."""

import logging
import re
from datetime import UTC, datetime, timedelta
from sqlmodel import Session, col, select

from cvm.config import home_tz
from cvm.models import Channel, Project, Setting, TrendAnalysis, now

log = logging.getLogger(__name__)
RUN_AT_HOUR = 8
PATTERN_RX = re.compile(r"^\s*TITLE_PATTERN:\s*(.+?)\s*$", re.M)

WEEKLY_PROMPT = (
    "Use the trend-analyst subagent in WEEKLY mode for ONE channel: channel_id={channel_id} \"{name}\" ({language}). "
    "Read its brief with list_channels. Its YouTube Analytics for the last 28 days (last 7 days: {views_7d} views):\n"
    "- most viewed: {top_views}\n- best average view percentage: {top_retention}\n"
    "Propose exactly 6 ideas for this channel: the first 3 derived from the most viewed video, the next 3 from the "
    "best-retaining one (a follow-up, the same angle on a new subject, or the question its comments would ask; not a "
    "copy). In each `why`, say which video it derives from and what to reuse. Every item gets channel_id={channel_id}, "
    "trend_ids=[] and hooks only in {language}. Do not repeat these videos the channel already has: {recent}. Also "
    "write ONE title pattern in {language} that generalises what worked in those titles (e.g. \"<number> things "
    "nobody tells you about <topic>\"), and put it as the LAST line of the summary, exactly as "
    "`TITLE_PATTERN: <pattern>`. Save with save_trend_analysis(analysis_id={analysis_id}, summary=..., items=...)."
)


def _key(channel_id: int) -> str:
    return f"channel_weekly:{channel_id}"


def week_id(t: datetime) -> str:
    y, w, _ = t.astimezone(home_tz()).isocalendar()
    return f"{y}-W{w:02d}"


def due(session: Session, at: datetime | None = None) -> bool:
    """True on Monday from 08:00 (studio timezone) until the cycle has run this ISO week (a worker started later that Monday
    still runs it; a whole missed Monday waits for the next one)."""
    t = (at or datetime.now(UTC)).astimezone(home_tz())
    if t.weekday() != 0 or t.hour < RUN_AT_HOUR:
        return False
    row = session.get(Setting, "weekly_cycle")
    return not row or (row.value or {}).get("week") != week_id(t)


def mark_done(session: Session, at: datetime | None = None, channels: list[int] | None = None) -> None:
    row = session.get(Setting, "weekly_cycle") or Setting(key="weekly_cycle")
    row.value = {"week": week_id(at or datetime.now(UTC)), "at": now().isoformat(), "channels": channels or []}
    session.add(row)
    session.commit()


def _describe(v: dict | None, metric: str) -> str:
    if not v:
        return "(no data)"
    value = f"{v['views']} views" if metric == "views" else f"{v.get('avg_view_pct')}% average view"
    return f"\"{v['title']}\" ({v['url']}, {value})"


def prompt(analysis_id: int, channel: Channel, snap: dict, recent: list[str]) -> str:
    return WEEKLY_PROMPT.format(analysis_id=analysis_id, channel_id=channel.id, name=channel.name, language=channel.language,
                                views_7d=snap.get("views_7d"), top_views=_describe(snap.get("top_views"), "views"),
                                top_retention=_describe(snap.get("top_retention"), "retention"),
                                recent="; ".join(recent[:30]) or "(none yet)")


def run_channel(session: Session, channel: Channel, claude=None, http=None) -> TrendAnalysis | None:
    """One channel's weekly pass. None when it has no analytics connection or no video data yet (no error)."""
    from cvm.publish import analytics

    if not analytics.connected(channel):
        return None
    snap = analytics.refresh(session, channel, **({"http": http} if http else {}))
    if not snap.get("top_views"):
        return None
    from cvm.setup import features

    if not features.ai_ideas(session):  # "Sugestões da IA" off: analytics refreshed, no AI ideas run
        return None
    if claude is None:
        from cvm.scripting import agents

        def claude(p, timeout):
            return agents.run(p, "ideas", timeout=timeout)
    a = TrendAnalysis(channel_id=channel.id)
    session.add(a)
    session.commit()
    session.refresh(a)
    recent = [p.title for p in session.exec(select(Project).where(Project.channel_id == channel.id)
                                            .order_by(col(Project.id).desc())).all()]
    error = None
    try:
        claude(prompt(a.id, channel, snap, recent), timeout=1200)
    except Exception as e:  # noqa: BLE001
        log.exception("weekly analysis failed for channel %s", channel.id)
        error = f"{type(e).__name__}: {e}"[:500]
    session.expire_all()
    a = session.get(TrendAnalysis, a.id)
    pattern = None
    if a.status == "running":  # the agent did not save
        a.status, a.error = "failed", error or "Claude terminou sem salvar a análise semanal"
    else:
        m = PATTERN_RX.search(a.summary or "")
        if m:
            pattern = m.group(1).strip().strip("`\"“”")
            a.summary = PATTERN_RX.sub("", a.summary).strip()
    session.add(a)
    row = session.get(Setting, _key(channel.id)) or Setting(key=_key(channel.id))
    old = (row.value or {}).get("title_pattern_suggestion")
    row.value = {"analysis_id": a.id, "week": week_id(datetime.now(UTC)), "top_views": snap.get("top_views"),
                 "top_retention": snap.get("top_retention"),
                 "title_pattern_suggestion": {"pattern": pattern, "status": "pending"} if pattern else old}
    session.add(row)
    session.commit()
    return a


def run_all(session: Session, claude=None, http=None) -> list[int]:
    """Every active channel with analytics connected; the others are skipped silently."""
    from cvm.channels import producing

    done = []
    for c in producing(session):
        try:
            if run_channel(session, c, claude, http):
                done.append(c.id)
        except Exception:  # noqa: BLE001 — one channel's failure must not stop the others
            log.exception("weekly cycle failed for channel %s", c.id)
            session.rollback()
    return done


# ---------------- what the channel panel shows ----------------

def suggestions(session: Session, channel: Channel) -> list[dict]:
    row = session.get(Setting, _key(channel.id))
    a = session.get(TrendAnalysis, (row.value or {}).get("analysis_id")) if row and (row.value or {}).get("analysis_id") else None
    if not a or a.status != "done":
        return []
    return [{"title": (it.get("hooks") or {}).get(channel.language) or it.get("topic"), "why": it.get("why"),
             "topic": it.get("topic"), "project_id": it.get("project_id")} for it in a.items]


def pattern(session: Session, channel: Channel) -> dict | None:
    row = session.get(Setting, _key(channel.id))
    return (row.value or {}).get("title_pattern_suggestion") if row else None


def decide_pattern(session: Session, channel: Channel, decision: str) -> dict:
    """approve → appended to profile.title_patterns (the agents read it via brief_text); reject → kept as rejected."""
    row = session.get(Setting, _key(channel.id))
    sug = dict((row.value or {}).get("title_pattern_suggestion") or {}) if row else {}
    if not sug.get("pattern") or sug.get("status") != "pending":
        raise LookupError("nenhum padrão de título pendente")
    if decision == "approve":
        profile = dict(channel.profile or {})
        patterns = list(profile.get("title_patterns") or [])
        if sug["pattern"] not in patterns:
            patterns.append(sug["pattern"])
        channel.profile = {**profile, "title_patterns": patterns}
        session.add(channel)
    sug["status"] = "approved" if decision == "approve" else "rejected"
    row.value = {**row.value, "title_pattern_suggestion": sug}
    session.add(row)
    session.commit()
    return sug
