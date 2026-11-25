"""Viral videos for a topic (Cortes tab): what is getting views fast right now, with Shorts potential.

vidIQ (paid by the user, CLAUDE.md exception) is reached through a research `claude -p` whose MCP config adds the
vidIQ server to the project's (data/tmp/mcp-research.json; the user's vidIQ OAuth carries over to headless runs,
checked 2026-11-21). If that run fails (limit, auth, network), a yt-dlp search (free) gives views and views per hour.
The last 5 searches per channel are kept in the Setting `viral:{channel_id}` → {"searches": [...]}.
"""

import json
import logging
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from cvm.config import REPO_ROOT, settings
from cvm.models import Setting
from cvm.visuals.sources import YTDLP

log = logging.getLogger(__name__)
VIDIQ_URL = "https://mcp.vidiq.com/mcp"
TOOLS = ["mcp__vidiq__vidiq_trending_videos", "mcp__vidiq__vidiq_outliers", "mcp__vidiq__vidiq_keyword_research"]
KEEP = 5
MAX_ITEMS = 15

PROMPT = """Pesquise vídeos em alta sobre "{topic}" com potencial de virar Shorts/cortes virais para um canal brasileiro de games.
Use as ferramentas do vidIQ (idioma dos títulos: {lang}):
1. vidiq_trending_videos com videoFormat "short", titleQuery "{topic}", sortBy "recentVph", limit 15;
2. vidiq_trending_videos com videoFormat "long", titleQuery "{topic}", sortBy "recentVph", limit 10;
3. vidiq_outliers com contentType "short", keyword "{topic}", publishedWithin "thisWeek", limit 10.
Escolha até {n} vídeos com mais chance de render um corte viral (momento forte, reação, humor, segredo, desafio, polêmica).
Para cada um escreva "why" (por que pode viralizar, uma linha) e "cut_idea" (qual trecho cortar e com que ângulo).
Em "patterns", resuma de 3 a 6 formatos que estão funcionando neste tema (ex.: "detalhes e segredos", "desafio",
"guia de dinheiro", "meme curto"), cada um com um título de exemplo, as views dele e o motivo.
Se nenhuma ferramenta do vidIQ funcionar, NÃO invente vídeos: responda {{"used_vidiq": false, "items": []}}.
Responda SÓ com JSON:
{{"used_vidiq": true, "items": [{{"video_id", "title", "channel", "views", "vph", "outlier", "duration", "published", "why", "cut_idea"}}],
  "patterns": [{{"format", "example", "views", "why"}}]}}"""


# runs that read third-party text (titles, transcripts) must not reach anything that writes: the project settings
# allow cvm write tools and web access, and --allowedTools only adds to that list (final review, 2026-11-21)
DENY = ["mcp__cvm__*", "WebFetch", "WebSearch", "Agent", "Bash", "Write", "Edit", "NotebookEdit", "Skill"]


def _mcp_file(name: str, servers: dict) -> Path:
    path = settings.data_dir / "tmp" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}, indent=1))
    return path


def mcp_config_with_vidiq() -> Path:
    """Only the vidIQ server: no cvm tools in research runs."""
    return _mcp_file("mcp-research.json", {"vidiq": {"type": "http", "url": VIDIQ_URL}})


def mcp_config_empty() -> Path:
    return _mcp_file("mcp-none.json", {})


def latest_examples(session: Session, channel_id: int, limit: int = 8) -> list[str]:
    """'format: “example”' of the latest search's working formats, for the viral scoring prompt."""
    row = session.get(Setting, f"viral:{channel_id}")
    for search in (row.value or {}).get("searches", []) if row else []:
        if search.get("patterns"):
            return [f"{p.get('format')}: “{p.get('example')}”" for p in search["patterns"]][:limit]
    return []


def _json_object(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text or "")
    a, b = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[a:b + 1]) if 0 <= a < b else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _num(v):
    return float(v) if isinstance(v, int | float) else None


def _clean(items: list) -> list[dict]:
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", str(it.get("video_id", ""))):
            continue
        out.append({"video_id": it["video_id"], "url": f"https://www.youtube.com/watch?v={it['video_id']}",
                    "title": str(it.get("title") or "")[:200], "channel": str(it.get("channel") or "")[:100],
                    "views": _num(it.get("views")), "vph": _num(it.get("vph")), "outlier": _num(it.get("outlier")),
                    "duration": _num(it.get("duration")), "published": it.get("published"),
                    "why": str(it.get("why") or "")[:300], "cut_idea": str(it.get("cut_idea") or "")[:300]})
    return sorted(out, key=lambda i: -(i["vph"] or 0))[:MAX_ITEMS]


def search(topic: str, language: str = "pt", run=None, fallback=None) -> dict:
    """{topic, language, source: vidiq|yt-dlp, at, items, patterns}."""
    if run is None:
        from cvm.scripting.agents import light

        run = light("vidiq")  # the chosen AI (Claude as before, or Codex with only vidIQ)
    fallback = fallback or ytdlp_search
    base = {"topic": topic, "language": language, "at": datetime.now(UTC).isoformat()}
    try:
        res = run(PROMPT.format(topic=topic, lang=language, n=MAX_ITEMS), timeout=900, step="research",
                  mcp_config=mcp_config_with_vidiq(), allowed_tools=TOOLS, disallowed_tools=DENY)
        data = _json_object(str(res.get("result", "")))
        items = _clean(data.get("items")) if data.get("used_vidiq") is True else []
        if items:
            pats = [p for p in data.get("patterns", []) if isinstance(p, dict)][:6]
            return {**base, "source": "vidiq", "items": items, "patterns": pats}
        log.warning("vidIQ research gave no items for %r", topic)
    except Exception as e:  # noqa: BLE001 — limit, auth, network: the free search still answers
        log.warning("vidIQ research failed for %r: %s", topic, e)
    try:
        return {**base, "source": "yt-dlp", "patterns": [], **fallback(topic, language)}
    except Exception as e:  # noqa: BLE001 — shown on the page instead of killing the run
        log.warning("yt-dlp search failed for %r: %s", topic, e)
        return {**base, "source": "yt-dlp", "patterns": [], "items": [], "error": f"pesquisa falhou: {str(e)[:200]}"}


def ytdlp_search(topic: str, language: str = "pt", run=subprocess.run) -> dict:
    """Recent YouTube results for the topic with views per hour (when the upload time is known)."""
    r = run([str(YTDLP), f"ytsearchdate30:{topic}", "--flat-playlist", "--dump-json", "--no-warnings"],
            capture_output=True, text=True, timeout=180)
    items = []
    for line in (r.stdout or "").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        ts, views = e.get("timestamp"), e.get("view_count")
        hours = (time.time() - ts) / 3600 if ts else None
        items.append({"video_id": e.get("id"), "title": e.get("title"), "channel": e.get("channel") or e.get("uploader"),
                      "views": views, "vph": round(views / hours, 1) if views and hours and hours > 0 else None,
                      "outlier": None, "duration": e.get("duration"), "published": None, "why": "", "cut_idea": ""})
    return {"items": _clean(items)}


def save(session: Session, channel_id: int, result: dict) -> None:
    row = session.get(Setting, f"viral:{channel_id}") or Setting(key=f"viral:{channel_id}", value={})
    v = dict(row.value or {})
    v["searches"] = [result, *v.get("searches", [])][:KEEP]
    row.value = v
    flag_modified(row, "value")
    session.add(row)
    session.commit()


def remove(session: Session, channel_id: int, at: str) -> bool:
    """Delete one saved search (a tab on Fábrica > Clips e cortes > Cortes) by its time stamp."""
    row = session.get(Setting, f"viral:{channel_id}")
    if not row:
        return False
    v = dict(row.value or {})
    kept = [x for x in v.get("searches", []) if x.get("at") != at]
    if len(kept) == len(v.get("searches", [])):
        return False
    v["searches"] = kept
    row.value = v
    flag_modified(row, "value")
    session.add(row)
    session.commit()
    return True
