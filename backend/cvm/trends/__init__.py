from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_
from sqlmodel import Session, col, select

from cvm.models import Trend

FRESH = timedelta(minutes=20)  # collections run every 30 min; a trend still trending is seen again each time
FOLLOW_FRESH = timedelta(hours=2)  # followed matches (cvm/trends/follow.py) are refreshed on their own clock


def current(session: Session, query):
    """Limit a Trend query to what the latest collection saw: older trends drop off the list (kept in the DB
    for the projects and analyses that used them). Followed matches (source "follow") are stamped by their own tick,
    so they neither move the "latest collection" nor drop off with it."""
    latest = session.exec(select(func.max(Trend.last_seen)).where(Trend.source != "follow")).one()
    follow = and_(col(Trend.source) == "follow",
                  col(Trend.last_seen) >= datetime.now(UTC) - FOLLOW_FRESH)
    if not latest:
        return query.where(or_(col(Trend.source) != "follow", follow))
    return query.where(or_(and_(col(Trend.source) != "follow", col(Trend.last_seen) >= latest - FRESH), follow))
