"""Channel numbers for the Monetização tab from vidIQ (the user's channel is connected there), used while YouTube
Analytics is not connected (needs a Google OAuth client the user creates). ~10 credits; refreshed by the daily run.
Saved in the Setting `vidiq_stats:{channel_id}`.
"""

from datetime import UTC, datetime

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from cvm.models import Setting

TOOLS = ["mcp__vidiq__vidiq_channel_analytics", "mcp__vidiq__vidiq_user_channels"]

PROMPT = """Use o vidIQ para o canal {cid}:
1. vidiq_user_channels → subscriberCount desse canal;
2. vidiq_channel_analytics com report "shorts_vs_longform_split", startDate {y365} e endDate {today}, metrics
   ["estimatedMinutesWatched"] → some só videoOnDemand e liveStream (Shorts NÃO contam) e converta para horas;
3. vidiq_channel_analytics sem dimensions, startDate {d28}, endDate {today}, metrics ["estimatedMinutesWatched",
   "subscribersGained"];
4. vidiq_channel_analytics com report "shorts_vs_longform_split", metrics ["views"], uma vez de {d90} a {today} e outra
   de {d28} a {today} → as views da linha "shorts" de cada período.
Se nenhuma ferramenta do vidIQ funcionar, responda {{"used_vidiq": false}}.
Responda SÓ com JSON: {{"used_vidiq": true, "subscribers": <int>, "hours_365_long": <número>, "minutes_28d": <int>,
"subs_gained_28d": <int>, "shorts_views_90d": <int>, "shorts_views_28d": <int>}}"""


def _num(v):
    return v if isinstance(v, int | float) and not isinstance(v, bool) and v >= 0 else None


def fetch(youtube_channel_id: str, run=None) -> dict | None:
    from datetime import date, timedelta

    from cvm.grow.viral_search import DENY, _json_object, mcp_config_with_vidiq

    if run is None:
        from cvm.scripting.agents import light

        run = light("vidiq")  # the chosen AI (Claude as before, or Codex with only vidIQ)
    today = date.today()
    try:
        res = run(PROMPT.format(cid=youtube_channel_id, today=today.isoformat(), y365=(today - timedelta(days=364)).isoformat(),
                                d28=(today - timedelta(days=27)).isoformat(), d90=(today - timedelta(days=89)).isoformat()),
                  timeout=600, step="research", mcp_config=mcp_config_with_vidiq(), allowed_tools=TOOLS, disallowed_tools=DENY)
        d = _json_object(str(res.get("result", "")))
    except Exception:  # noqa: BLE001
        return None
    if d.get("used_vidiq") is not True:
        return None
    return {"subscribers": _num(d.get("subscribers")), "hours_365_long": _num(d.get("hours_365_long")),
            "minutes_28d": _num(d.get("minutes_28d")), "subs_gained_28d": _num(d.get("subs_gained_28d")),
            "shorts_views_90d": _num(d.get("shorts_views_90d")), "shorts_views_28d": _num(d.get("shorts_views_28d")),
            "at": datetime.now(UTC).isoformat()}


def save(session: Session, channel_id: int, snap: dict) -> None:
    row = session.get(Setting, f"vidiq_stats:{channel_id}") or Setting(key=f"vidiq_stats:{channel_id}", value={})
    row.value = snap
    flag_modified(row, "value")
    session.add(row)
    session.commit()


def load(session: Session, channel_id: int) -> dict | None:
    row = session.get(Setting, f"vidiq_stats:{channel_id}")
    return dict(row.value) if row and row.value else None


SYNC_LEASE_S = 900  # a sync that died frees the button after this
CREDITS = 10


def sync_state(session: Session, channel_id: int) -> dict:
    row = session.get(Setting, f"vidiq_sync:{channel_id}")
    v = dict(row.value or {}) if row else {}
    since = v.get("running_since")
    v["running"] = bool(since) and (datetime.now(UTC) - datetime.fromisoformat(since)).total_seconds() < SYNC_LEASE_S
    return v


def _set_sync(session: Session, channel_id: int, **fields) -> None:
    row = session.get(Setting, f"vidiq_sync:{channel_id}") or Setting(key=f"vidiq_sync:{channel_id}", value={})
    row.value = {**(row.value or {}), **fields}
    flag_modified(row, "value")
    session.add(row)
    session.commit()


def start_sync(session: Session, channel_id: int) -> bool:
    """Mark a manual sync (Monetização > "Sincronizar com o vidIQ") as running; False when one already is."""
    if sync_state(session, channel_id)["running"]:
        return False
    _set_sync(session, channel_id, running_since=datetime.now(UTC).isoformat(), error=None)
    return True


def run_sync(session: Session, channel_id: int, youtube_channel_id: str, fetch_fn=None) -> None:
    try:
        snap = (fetch_fn or fetch)(youtube_channel_id)
        if not snap:
            raise RuntimeError("o vidIQ não devolveu os números do canal agora; tente de novo mais tarde")
        save(session, channel_id, snap)
        _set_sync(session, channel_id, running_since=None, error=None)
    except Exception as e:  # noqa: BLE001 — shown on the Monetização tab
        _set_sync(session, channel_id, running_since=None, error=str(e)[:300])
