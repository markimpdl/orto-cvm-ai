"""'Avaliar com vidIQ' (render approval, on the user's click): title score, scored alternative titles, tags and
description keywords; the thumbnail is scored only once the video is published (vidIQ needs its YouTube id or a public
image). Uses the same research claude -p + vidIQ MCP config as cvm/grow/viral_search.py. Read-only: it never calls
vidiq_update_video; applying a title is the user's click on the existing SEO edit. Result in Render.meta["vidiq"].
"""

import json
import re
from datetime import UTC, datetime

from cvm.models import Channel, Render

TOOLS = ["mcp__vidiq__vidiq_score_title", "mcp__vidiq__vidiq_generate_titles", "mcp__vidiq__vidiq_keyword_research",
         "mcp__vidiq__vidiq_score_thumbnail"]

PROMPT = """Avalie a embalagem deste vídeo do YouTube com o vidIQ, para um canal brasileiro (pt-BR).
Título atual: "{title}"
Descrição (início): "{description}"
Formato: {{"type": "{kind}"}}
Faça:
1. vidiq_score_title com o título atual, type "{kind}"{channel_arg};
2. vidiq_generate_titles com title e description acima, language "pt-BR", regionCode "BR", numTitles 3, type "{kind}";
3. vidiq_keyword_research (mode "research", country "BR") com o tema principal do título, para escolher até 12 tags e
   até 6 palavras-chave para a descrição;
{thumb}
Responda SÓ com JSON:
{{"title_score": <0-100 ou null>, "titles": [{{"title", "score"}}], "thumb_score": <0-100 ou null>,
  "thumb_feedback": <texto curto ou null>, "tags": [..], "description_keywords": [..]}}"""


def _published_id(render: Render) -> str | None:
    from sqlmodel import Session, select

    from cvm.db import engine
    from cvm.models import Publication

    with Session(engine) as s:
        for p in s.exec(select(Publication).where(Publication.render_id == render.id)).all():
            if m := re.search(r"(?:v=|shorts/|youtu\.be/)([A-Za-z0-9_-]{11})", p.url or ""):
                return m.group(1)
    return None


def review(render: Render, channel: Channel, run=None, video_id: str | None = None) -> dict:
    from cvm.grow.viral_search import DENY, _json_object, mcp_config_with_vidiq

    if run is None:
        from cvm.scripting.agents import light

        run = light("vidiq")  # the chosen AI (Claude as before, or Codex with only vidIQ)
    short = "short" in render.profile
    kind = "short" if short else "long"
    seo = render.seo or {}
    thumb = ("4. vidiq_score_thumbnail com videoId \"" + video_id + "\" e o título atual." if video_id and not short
             else "Não avalie a thumbnail (thumb_score null).")
    prompt = PROMPT.format(title=seo.get("title") or "", description=(seo.get("description") or "")[:600], kind=kind,
                           channel_arg=f', channelId "{channel.youtube_channel_id}"' if channel.youtube_channel_id else "",
                           thumb=thumb)
    res = run(prompt, timeout=600, step="research", mcp_config=mcp_config_with_vidiq(), allowed_tools=TOOLS,
              disallowed_tools=DENY)
    d = _json_object(str(res.get("result", "")))

    def score(v):
        return int(v) if isinstance(v, int | float) and 0 <= v <= 100 else None
    titles = [{"title": str(t.get("title"))[:100], "score": score(t.get("score"))}
              for t in d.get("titles", []) if isinstance(t, dict) and t.get("title")][:5]
    return {"title_score": score(d.get("title_score")), "titles": titles, "thumb_score": score(d.get("thumb_score")),
            "thumb_feedback": (str(d["thumb_feedback"])[:400] if d.get("thumb_feedback") else
                               None if video_id else "A nota da thumbnail fica disponível depois de publicar."),
            "tags": [str(t)[:60] for t in d.get("tags", []) if t][:15],
            "description_keywords": [str(t)[:60] for t in d.get("description_keywords", []) if t][:8],
            "at": datetime.now(UTC).isoformat()}
