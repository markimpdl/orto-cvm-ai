"""Post-publication metrics (free YouTube Data API key). Feeds back into which styles/topics perform."""

import logging
import re

import httpx
from sqlmodel import Session, select

from cvm.config import key, settings
from cvm.models import Publication, now

log = logging.getLogger(__name__)
YT_ID = re.compile(r"(?:v=|shorts/|youtu\.be/)([A-Za-z0-9_-]{11})")


def refresh_youtube(session: Session) -> int:
    if not key("youtube_api_key"):
        return 0
    pubs = [p for p in session.exec(select(Publication).where(Publication.status == "published")).all() if p.url and YT_ID.search(p.url)]
    ids = {YT_ID.search(p.url).group(1): p for p in pubs}
    for chunk in [list(ids)[i : i + 50] for i in range(0, len(ids), 50)]:
        r = httpx.get("https://www.googleapis.com/youtube/v3/videos",
                      params={"part": "statistics", "id": ",".join(chunk), "key": key("youtube_api_key")}, timeout=20)
        r.raise_for_status()
        for item in r.json().get("items", []):
            st = {k: int(v) for k, v in item["statistics"].items() if v.isdigit()}
            p = ids[item["id"]]
            hist = (p.metrics or {}).get("history", [])[-200:]
            p.metrics = {**{k: v for k, v in (p.metrics or {}).items() if k == "analytics"}, **st, "updated_at": now().isoformat(), "history": [*hist, {"at": now().isoformat(), **st}]}
            session.add(p)
    session.commit()
    return len(ids)


def performance_summary(session: Session) -> list[dict]:
    """Average views per style/platform/language — used by the trend-analyst to pick angles."""
    from collections import defaultdict

    from cvm.models import Channel, Project, Render

    agg: dict[tuple, list[int]] = defaultdict(list)
    for p in session.exec(select(Publication).where(Publication.status == "published")).all():
        views = (p.metrics or {}).get("viewCount")
        if views is None:
            continue
        r = session.get(Render, p.render_id)
        proj = session.get(Project, r.project_id)
        ch = session.get(Channel, p.channel_id)
        agg[(proj.style.value, ch.platform.value, r.language)].append(views)
    return [{"style": k[0], "platform": k[1], "language": k[2], "videos": len(v), "avg_views": round(sum(v) / len(v))}
            for k, v in sorted(agg.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))]
