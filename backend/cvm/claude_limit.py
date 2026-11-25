"""Subscription limits of the writing agents (Claude, and Codex as its fallback — cvm/scripting/agents.py): when a
CLI says the usage limit was reached, that provider is not called again until the reset; a step no provider can take
waits instead of failing; the rest of the queue (voice, visuals, render) goes on.

State in Setting "claude_limit": {until (UTC ISO), since, message}. The worker's `claude_limit_tick` (every minute)
clears it once `until` has passed and puts the waiting videos back in line. If Claude is still limited at that point,
the next call simply sets a new `until`.
"""

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session

from cvm.models import Setting

KEY = "claude_limit"  # Setting key per provider: "claude_limit", "codex_limit"
NAMES = {"claude": "Claude", "codex": "Codex"}
FALLBACK = timedelta(hours=1)  # reset time not in the message: try again in an hour
MARGIN = timedelta(minutes=2)  # start a little after the announced reset
_SIGNS = ("usage limit", "limit reached", "hit your limit", "limit will reset", "rate limit", "rate_limit",
          "session limit", "weekly limit", "out of extra usage")


class ClaudeLimit(RuntimeError):
    """`claude -p` refused: subscription limit reached (or still in effect)."""

    def __init__(self, until: datetime, message: str, provider: str = "claude"):
        super().__init__(f"limite do {NAMES.get(provider, provider)} até {until.isoformat(timespec='minutes')}: "
                         f"{message[:200]}")
        self.until, self.message, self.provider = until, message, provider


def is_limit(text: str) -> bool:
    t = (text or "").lower()
    # "You've hit your 5-hour limit · resets 10pm", "api_error":"usage_limit_reached", "hit your weekly limit"…
    return any(s in t for s in _SIGNS) or bool(re.search(r"hit your [\w -]*limit|usage_limit|api_error_status\D{0,3}429", t))


def reset_time(text: str, now: datetime | None = None) -> datetime:
    """When the limit resets (UTC), from the CLI's message: "…limit reached|1731600000", "resets 3pm",
    "resets 3:30pm (America/Sao_Paulo)", "reset at 15:00 (Europe/Lisbon)", "resets Mon 9am". Fallback: now + 1 h."""
    now = now or datetime.now(UTC)
    if m := re.search(r"\|(\d{10})\b", text or ""):
        return datetime.fromtimestamp(int(m[1]), UTC) + MARGIN
    # Codex: "…try again in 2 days 3 hours 5 minutes" / "try again in 45 minutes"
    if m := re.search(r"try again in\s+((?:\d+\s*(?:days?|hours?|hrs?|minutes?|mins?|seconds?|secs?)[\s,and]*)+)", text or "", re.I):
        delta = timedelta()
        units = {"day": "days", "hour": "hours", "hr": "hours", "minute": "minutes", "min": "minutes",
                 "second": "seconds", "sec": "seconds"}
        for n, unit in re.findall(r"(\d+)\s*(day|hour|hr|minute|min|second|sec)", m[1], re.I):
            delta += timedelta(**{units[unit.lower()]: int(n)})
        return now + delta + MARGIN
    text = re.sub(r"try again at", "resets", text or "", flags=re.I)  # Codex: "try again at 5:20 PM"
    m = re.search(r"reset[s]?(?:\s+at)?\s+(?:(mon|tue|wed|thu|fri|sat|sun)\w*\s+)?(?:\w{3,9}\s+\d{1,2},?\s+)?"
                  r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([^)]+)\))?", text or "", re.I)
    if not m:
        return now + FALLBACK
    day, hour, minute, ampm, tzname = m.groups()
    hour, minute = int(hour), int(minute or 0)
    if ampm:
        hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        return now + FALLBACK
    try:
        tz = ZoneInfo(tzname.strip()) if tzname else datetime.now().astimezone().tzinfo
    except Exception:  # noqa: BLE001 — unknown zone name: the machine's own
        tz = datetime.now().astimezone().tzinfo
    local = now.astimezone(tz)
    at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day:
        wd = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(day[:3].lower())
        at += timedelta(days=(wd - at.weekday()) % 7)
    while at <= local:
        at += timedelta(days=7 if day else 1)
    return at.astimezone(UTC) + MARGIN


def _key(provider: str) -> str:
    return f"{provider}_limit"


def state(session: Session, provider: str = "claude") -> dict | None:
    row = session.get(Setting, _key(provider))
    return dict(row.value) if row and row.value.get("until") else None


def blocked_until(session: Session, now: datetime | None = None, provider: str = "claude") -> datetime | None:
    st = state(session, provider)
    if not st:
        return None
    until = datetime.fromisoformat(st["until"])
    return until if until > (now or datetime.now(UTC)) else None


def set_limit(session: Session, until: datetime, message: str, provider: str = "claude") -> None:
    row = session.get(Setting, _key(provider)) or Setting(key=_key(provider))
    since = (row.value or {}).get("since") or datetime.now(UTC).isoformat(timespec="seconds")
    row.value = {"until": until.isoformat(timespec="seconds"), "since": since, "message": (message or "")[:300]}
    session.add(row)
    session.commit()


def clear(session: Session, provider: str = "claude") -> None:
    if row := session.get(Setting, _key(provider)):
        session.delete(row)
        session.commit()


def label(until: datetime, who: str = "Claude") -> str:
    local = until.astimezone()
    return f"Limite do {who}: retoma às {local:%H:%M}" + ("" if local.date() == datetime.now().date() else f" de {local:%d/%m}")
