"""Free trend sources. Each fetcher returns a list of RawTrend for one region.

TikTok (Playwright scraper) and X (trends24 scraping) are added in Phase 2.
"""

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field

import feedparser
import httpx

from cvm.config import key, settings

log = logging.getLogger(__name__)

BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) cvm/0.1"}

REDDIT_GEO = {"BR": "br", "AE": "ae", "US": "us"}
NEWS_LOCALE = {"BR": ("pt-BR", "BR:pt-419"), "AE": ("ar", "AE:ar"), "US": ("en-US", "US:en")}


@dataclass
class RawTrend:
    source: str
    region: str
    title: str
    rank: int
    url: str | None = None
    metric: float | None = None
    engagement: float | None = None
    meta: dict = field(default_factory=dict)


def normalize_key(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode() or title
    return re.sub(r"[^a-z0-9؀-ۿ]+", " ", t.lower()).strip()


def _traffic(s: str | None) -> float | None:
    if not s:
        return None
    m = re.match(r"([\d.,]+)\s*([KkMm]?)", s.replace("+", ""))
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return n * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1)


def google_trends(region: str, client: httpx.Client) -> list[RawTrend]:
    geo = "US" if region == "GLOBAL" else region  # no worldwide feed: the US one stands in
    r = client.get(f"https://trends.google.com/trending/rss?geo={geo}", headers=UA)
    r.raise_for_status()
    feed = feedparser.parse(r.text)
    out = []
    for i, e in enumerate(feed.entries, 1):
        out.append(
            RawTrend(
                "google",
                region,
                e.title,
                i,
                url=e.get("ht_news_item_url") or e.get("link"),
                metric=_traffic(e.get("ht_approx_traffic")),
                meta={"news_title": e.get("ht_news_item_title"), "picture": e.get("ht_picture")},
            )
        )
    return out


def _reddit_token(client: httpx.Client) -> str:
    r = client.post("https://www.reddit.com/api/v1/access_token", data={"grant_type": "client_credentials"},
                    auth=(settings.reddit_client_id, settings.reddit_client_secret), headers=BROWSER_UA)
    r.raise_for_status()
    return r.json()["access_token"]


def reddit_popular(region: str, client: httpx.Client) -> list[RawTrend]:
    """Official API (app-only OAuth) when a free app is configured — gives votes and comments; else public RSS."""
    geo = REDDIT_GEO.get(region, "GLOBAL")
    if settings.reddit_client_id and settings.reddit_client_secret:
        r = client.get("https://oauth.reddit.com/r/popular/hot", params={"geo_filter": geo, "limit": 50},
                       headers={**BROWSER_UA, "Authorization": f"Bearer {_reddit_token(client)}"})
        r.raise_for_status()
        posts = [c["data"] for c in r.json()["data"]["children"]]
        return [
            RawTrend("reddit", region, p["title"], i, url=f"https://www.reddit.com{p['permalink']}", metric=float(p.get("score") or 0),
                     engagement=(p.get("num_comments") or 0) / max(p.get("score") or 1, 1), meta={"subreddit": p.get("subreddit")})
            for i, p in enumerate(posts, 1) if not p.get("over_18")
        ]
    for wait in (0, 8, 20):  # the RSS rate-limits bursts (429); the 3 regions are fetched back to back
        time.sleep(wait)
        r = client.get(f"https://www.reddit.com/r/popular/.rss?geo_filter={geo}&limit=50", headers=BROWSER_UA)
        if r.status_code != 429:
            break
    if r.status_code in (403, 404):  # since 2026-11-17 Reddit answers anonymous RSS with an empty 404 feed
        log.warning("reddit/%s: RSS anônimo bloqueado pelo Reddit; crie um app grátis 'script' em "
                    "reddit.com/prefs/apps e ponha CVM_REDDIT_CLIENT_ID/SECRET no .env", region)
        return []
    r.raise_for_status()
    feed = feedparser.parse(r.text)
    return [
        RawTrend("reddit", region, e.title, i, url=e.link, meta={"subreddit": e.get("tags", [{}])[0].get("term")})
        for i, e in enumerate(feed.entries, 1)
    ]


def youtube_most_popular(region: str, client: httpx.Client) -> list[RawTrend]:
    if not key("youtube_api_key"):
        return []
    r = client.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet,statistics",
            "chart": "mostPopular",
            "regionCode": "US" if region == "GLOBAL" else region,  # no worldwide chart
            "maxResults": 50,
            "key": key("youtube_api_key"),
        },
    )
    r.raise_for_status()
    out = []
    for i, v in enumerate(r.json().get("items", []), 1):
        st = v.get("statistics", {})
        views = float(st.get("viewCount", 0) or 0)
        eng = float(st.get("likeCount", 0) or 0) + float(st.get("commentCount", 0) or 0)
        out.append(
            RawTrend(
                "youtube",
                region,
                v["snippet"]["title"],
                i,
                url=f"https://youtube.com/watch?v={v['id']}",
                metric=views,
                engagement=eng,
                meta={"channel": v["snippet"].get("channelTitle"), "tags": v["snippet"].get("tags", [])[:10]},
            )
        )
    return out


def google_news(region: str, client: httpx.Client) -> list[RawTrend]:
    hl, ceid = NEWS_LOCALE.get(region, ("en-US", "US:en"))
    gl = "US" if region == "GLOBAL" else region  # no worldwide edition: English / US
    r = client.get(f"https://news.google.com/rss?hl={hl}&gl={gl}&ceid={ceid}", headers=UA)
    r.raise_for_status()
    feed = feedparser.parse(r.text)
    return [RawTrend("news", region, e.title, i, url=e.link) for i, e in enumerate(feed.entries[:40], 1)]


X_COUNTRY = {"BR": "brazil", "AE": "united-arab-emirates", "US": "united-states", "GLOBAL": ""}  # "" = worldwide


def x_trends(region: str, client: httpx.Client) -> list[RawTrend]:
    """X/Twitter trends via trends24.in (official API is paid). metric = hours trending in the last 24 lists."""
    from selectolax.lexbor import LexborHTMLParser as HTMLParser

    country = X_COUNTRY.get(region, "")  # a country trends24 is not mapped for falls back to the worldwide page
    path = f"{country}/" if country else ""
    r = client.get(f"https://trends24.in/{path}", headers=BROWSER_UA)
    r.raise_for_status()
    lists = HTMLParser(r.text).css(".list-container")
    if not lists:
        return []
    persistence: dict[str, int] = {}
    for lst in lists[:24]:
        for a in lst.css("a.trend-link"):
            persistence[a.text()] = persistence.get(a.text(), 0) + 1
    return [
        RawTrend("x", region, a.text(), i, url=a.attributes.get("href"), metric=float(persistence[a.text()]), meta={"hours_trending": persistence[a.text()]})
        for i, a in enumerate(lists[0].css("a.trend-link"), 1)
    ]


def tiktok(region: str, client: httpx.Client) -> list[RawTrend]:
    from cvm.trends.browser import tiktok_hashtags

    return tiktok_hashtags(region)  # GLOBAL: the Creative Center has no worldwide view, handled there


FETCHERS = {
    "google": google_trends,
    "reddit": reddit_popular,
    "youtube": youtube_most_popular,
    "news": google_news,
    "x": x_trends,
    "tiktok": tiktok,
}
