"""Pesquisa tab: a topic report from vidIQ — is the topic rising, stable or saturated, which keywords to aim at, what
the channel's tracked competitors are breaking out with, and video ideas. Same research claude -p as
viral_search (only the vidIQ MCP, write/web tools denied). Last 5 reports per channel in the Setting `research:{id}`.
~20 credits per report.
"""

import re
from datetime import UTC, datetime

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from cvm.models import Setting

TOOLS = ["mcp__vidiq__vidiq_keyword_research", "mcp__vidiq__vidiq_outliers", "mcp__vidiq__vidiq_list_competitors"]
VERDICTS = {"subindo", "estável", "saturado"}
KEEP = 5

PROMPT = """Analise o tema "{topic}" para {who} (idioma {lang}).
Use o vidIQ:
1. vidiq_keyword_research mode "research" com keyword "{topic}" (country "BR" se o idioma for pt), limit 15;
2. vidiq_keyword_research mode "rising" com niche "{topic}", period "week", limit 15;
3. {step3}
Decida o veredito do tema: "subindo" (busca crescendo e concorrência ainda dá para entrar), "estável" ou "saturado"
(muitos vídeos novos com views caindo ou concorrência muito alta), com o motivo em uma linha.
Proponha de 5 a 8 ideias de vídeo (título + por quê) para este canal, preferindo palavras com busca alta e
concorrência média ou baixa.
Se nenhuma ferramenta do vidIQ funcionar, responda {{"used_vidiq": false}}.
Responda SÓ com JSON:
{{"used_vidiq": true, "verdict": "subindo|estável|saturado", "verdict_why": "...",
  "keywords": [{{"keyword", "volume", "competition", "growth"}}],
  "competitor_outliers": [{{"video_id", "title", "channel", "views", "outlier"}}],
  "ideas": [{{"title", "why"}}]}}"""


def _num(v):
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


WHO = "um canal brasileiro de games pequeno que quer monetizar"
STEP3_COMPETITORS = ('vidiq_list_competitors{channel_arg} e depois vidiq_outliers com channelIds desses concorrentes, '
                     'keyword "{topic}",\n   publishedWithin "thisMonth", limit 10 (se não houver concorrentes, '
                     'vidiq_outliers só com o keyword).')
STEP3_KEYWORD = 'vidiq_outliers só com o keyword "{topic}", publishedWithin "thisMonth", limit 10.'


def report(topic: str, language: str = "pt", channel_youtube_id: str | None = None, run=None,
           audience: str | None = None) -> dict:
    """`audience` (Em alta, no channel required): who the ideas are for; without a channel's YouTube id the
    outliers come from the keyword alone (vidiq_list_competitors would read some other default channel)."""
    from cvm.grow.viral_search import DENY, _json_object, mcp_config_with_vidiq

    if run is None:
        from cvm.scripting.agents import light

        run = light("vidiq")  # the chosen AI (Claude as before, or Codex with only vidIQ)
    base = {"topic": topic, "language": language, "at": datetime.now(UTC).isoformat(), "verdict": None,
            "verdict_why": "", "keywords": [], "competitor_outliers": [], "ideas": []}
    try:
        channel_arg = f' (channelId "{channel_youtube_id}")' if channel_youtube_id else ""
        step3 = STEP3_KEYWORD if audience and not channel_youtube_id else STEP3_COMPETITORS
        res = run(PROMPT.format(topic=topic, lang=language, who=audience or WHO,
                                step3=step3.format(topic=topic, channel_arg=channel_arg)),
                  timeout=900, step="research", mcp_config=mcp_config_with_vidiq(), allowed_tools=TOOLS,
                  disallowed_tools=DENY)
        d = _json_object(str(res.get("result", "")))
    except Exception as e:  # noqa: BLE001
        return {**base, "error": f"pesquisa falhou: {str(e)[:200]}"}
    if d.get("used_vidiq") is not True:
        return {**base, "error": "o vidIQ não respondeu nesta pesquisa"}
    kws = [{"keyword": str(k.get("keyword"))[:80], "volume": _num(k.get("volume")), "competition": _num(k.get("competition")),
            "growth": _num(k.get("growth"))} for k in d.get("keywords", []) if isinstance(k, dict) and k.get("keyword")][:20]
    outs = [{"video_id": o["video_id"], "url": f"https://www.youtube.com/watch?v={o['video_id']}",
             "title": str(o.get("title") or "")[:200], "channel": str(o.get("channel") or "")[:100],
             "views": _num(o.get("views")), "outlier": _num(o.get("outlier"))}
            for o in d.get("competitor_outliers", [])
            if isinstance(o, dict) and re.fullmatch(r"[A-Za-z0-9_-]{11}", str(o.get("video_id", "")))][:12]
    ideas = [{"title": str(i.get("title"))[:120], "why": str(i.get("why") or "")[:240]}
             for i in d.get("ideas", []) if isinstance(i, dict) and i.get("title")][:8]
    verdict = d.get("verdict") if d.get("verdict") in VERDICTS else None
    return {**base, "verdict": verdict, "verdict_why": str(d.get("verdict_why") or "")[:240] if verdict else "",
            "keywords": kws, "competitor_outliers": outs, "ideas": ideas}


def save(session: Session, channel_id: int, result: dict) -> None:
    row = session.get(Setting, f"research:{channel_id}") or Setting(key=f"research:{channel_id}", value={})
    v = dict(row.value or {})
    v["reports"] = [result, *v.get("reports", [])][:KEEP]
    row.value = v
    flag_modified(row, "value")
    session.add(row)
    session.commit()
