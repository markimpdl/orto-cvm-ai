import logging
from datetime import UTC
from collections import defaultdict

import httpx
from sqlmodel import Session, select

from cvm.config import settings
from cvm.db import engine, init_db
from cvm.models import Trend, TrendSnapshot, now
from cvm.scoring.cluster import cluster
from cvm.scoring.viral import score
from cvm.trends.sources import FETCHERS, RawTrend, normalize_key

log = logging.getLogger(__name__)


def _aware(dt):
    """SQLite returns naive datetimes; they are stored as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _regions() -> list[str]:
    """The wizard's choice (Setting "features"); no choice or an unreadable DB = settings.regions."""
    from cvm.setup import features

    try:
        with Session(engine) as s:
            return features.regions(s)
    except Exception:  # noqa: BLE001
        return list(settings.regions)


def fetch_all() -> list[RawTrend]:
    out: list[RawTrend] = []
    regions = _regions()
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        for name, fetch in FETCHERS.items():
            for region in regions:
                try:
                    out.extend(fetch(region, client))
                except Exception as e:  # noqa: BLE001 — one broken source must not stop the rest
                    log.warning("trend source %s/%s failed: %s", name, region, e)
    return out


def store(raw: list[RawTrend], session: Session) -> None:
    ts = now()
    for r in raw:
        key = normalize_key(r.title)
        trend = session.exec(
            select(Trend).where(Trend.source == r.source, Trend.region == r.region, Trend.key == key)
        ).first()
        if not trend:
            trend = Trend(source=r.source, region=r.region, key=key, title=r.title, url=r.url, meta=r.meta)
        trend.last_seen = ts
        trend.meta = {**trend.meta, **r.meta, "rank": r.rank, "metric": r.metric, "engagement": r.engagement}
        session.add(trend)
        session.flush()
        session.add(TrendSnapshot(trend_id=trend.id, captured_at=ts, rank=r.rank, metric=r.metric, engagement=r.engagement))
    session.commit()


def rescore(session: Session) -> None:
    """Score trends seen in the last 48h; the same story on several sources (fuzzy clusters) scores higher."""
    ts = now()
    recent = [t for t in session.exec(select(Trend)).all() if (ts - _aware(t.last_seen)).total_seconds() < 48 * 3600]
    cluster_of: dict[int, tuple[str, int]] = {}
    by_region: dict[str, list[Trend]] = defaultdict(list)
    for t in recent:
        by_region[t.region].append(t)
    sources_in: dict[tuple[str, int], set[str]] = defaultdict(set)
    titles_in: dict[tuple[str, int], list[str]] = defaultdict(list)
    for region, items in by_region.items():
        for t, cid in zip(items, cluster([t.key for t in items]), strict=True):
            cluster_of[t.id] = (region, cid)
            sources_in[(region, cid)].add(t.source)
            titles_in[(region, cid)].append(f"{t.source}: {t.title}")
    for t in recent:
        c = cluster_of[t.id]
        t.meta = {**t.meta, "related": [x for x in titles_in[c] if not x.endswith(t.title)][:8]}
        snaps = session.exec(select(TrendSnapshot).where(TrendSnapshot.trend_id == t.id)).all()
        t.score, t.score_breakdown = score(
            rank=t.meta.get("rank"),
            snapshots=[(_aware(s.captured_at), s.metric) for s in snaps],
            metric=t.meta.get("metric"),
            engagement=t.meta.get("engagement"),
            n_sources=len(sources_in[c]),
            first_seen=_aware(t.first_seen),
            now=ts,
        )
        session.add(t)
    session.commit()


def collect_all() -> int:
    init_db()
    raw = fetch_all()
    with Session(engine) as session:
        store(raw, session)
        rescore(session)
    log.info("collected %d raw trends", len(raw))
    return len(raw)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(collect_all())
