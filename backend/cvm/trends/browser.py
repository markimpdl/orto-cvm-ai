"""Browser-based trend sources (Playwright, persistent profiles so a one-time manual login is reused).

One-time login (free account) for TikTok Creative Center, which shows only the top 3 hashtags anonymously:
    uv run python -m cvm.trends.browser login tiktok
"""

import re
import sys
from contextlib import contextmanager

from cvm.config import settings
from cvm.trends.sources import RawTrend, _traffic

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
# Creative Center moved in Nov 2026 (old /business/creativecenter/... path redirects and times out headless);
# the country is now the `region` param, `countryCode` is ignored (it showed the US list).
LOGIN_URLS = {"tiktok": "https://ads.tiktok.com/creative/creativeCenter/trends/hashtag"}


@contextmanager
def persistent_page(name: str, headless: bool = True):
    from playwright.sync_api import sync_playwright

    profile = settings.data_dir / "browser-profiles" / name
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile), headless=headless, locale="en-US", user_agent=UA)
        try:
            yield ctx.pages[0] if ctx.pages else ctx.new_page()
        finally:
            ctx.close()


def parse_creative_center(text: str) -> list[tuple[str, float | None, float | None]]:
    """Parse the rendered hashtag table: rank, #tag, [categories], '<n> Posts', '<n> Views'."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith("#") and i > 0 and lines[i - 1].isdigit():
            tag, posts, views = lines[i], None, None
            for j in range(i + 1, min(i + 10, len(lines))):
                if lines[j] == "Posts" and posts is None:
                    posts = _traffic(lines[j - 1])
                if lines[j] == "Views":
                    views = _traffic(lines[j - 1])
                    break
            out.append((tag, posts, views))
        i += 1
    return out


def tiktok_hashtags(region: str, max_pages: int = 3) -> list[RawTrend]:
    with persistent_page("tiktok") as page:
        page.goto(f"{LOGIN_URLS['tiktok']}?region={"US" if region == "GLOBAL" else region}&period=7", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        for _ in range(max_pages):
            more = page.get_by_text(re.compile(r"^View more$"))
            if not more.count():
                break
            try:
                more.first.click(timeout=3000)
                page.wait_for_timeout(2500)
            except Exception:  # noqa: BLE001 — login wall or no more pages
                break
        rows = parse_creative_center(page.inner_text("body"))
    return [
        RawTrend("tiktok", region, tag, rank, url=f"https://www.tiktok.com/tag/{tag.lstrip('#')}", metric=views, engagement=posts)
        for rank, (tag, posts, views) in enumerate(rows, 1)
    ]


def login(name: str) -> None:
    with persistent_page(name, headless=False) as page:
        page.goto(LOGIN_URLS[name])
        input(f"Faça login em {name} na janela aberta e pressione Enter aqui para salvar a sessão… ")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "login":
        login(sys.argv[2])
    else:
        for t in tiktok_hashtags(sys.argv[1] if len(sys.argv) > 1 else "BR"):
            print(t.rank, t.title, t.metric, t.engagement)
