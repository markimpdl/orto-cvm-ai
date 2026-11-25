"""Checks before a video enters the queue and before it is scheduled (docs/plano-canais-e-voz.md, Phase 4).

At creation (`check_creation`, called by `suggest.new_project`):
- the channel takes new videos and the project's language is the channel's (hard: `ChannelClosed`);
- no similar title/topic on the same channel in the last 30 days (`SimilarTopic`, a `ChannelClosed`, so every
  path that skips closed channels also skips repeats); the user can "fazer mesmo assim" with `force_similar: true`.
  Kids episodes only get a warning (the scriptwriter already reads the previous titles and endings).

Before `scheduled` (`check_publication`, called by `publish.service.schedule` for every render × channel):
- the render's language — and the language its script is really written in — is the channel's (hard);
- an episode of a made-for-kids series never goes out on a channel that is not made for kids (hard), and
  `render.meta["made_for_kids"]` is set from the channel for the upload paths and the publish kit;
- `render.meta["synthetic_disclosure"] = True` (with `synthetic_reasons`) when the video has a cloned/AI voice,
  an avatar or AI imagery: the kit ticks YouTube's "conteúdo alterado ou sintético" and the description says so.
"""

import re
import unicodedata
from datetime import timedelta

from sqlmodel import Session, col, select

from cvm.channels import ChannelClosed, check_new_project, series_of
from cvm.models import Asset, Channel, Project, Render, Script, now

DAYS = 30
TITLE_OVERLAP = 0.6  # shared words / words of the shorter title
TOPIC_COSINE = 0.5  # title + topic word sets


class SimilarTopic(ChannelClosed):
    """A video with a similar title/topic was made on this channel recently; `similar` lists them."""

    def __init__(self, message: str, similar: list[dict]):
        super().__init__(message)
        self.similar = similar


class PublishRefused(ValueError):
    """A hard check failed before scheduling; the message is for the user (Portuguese)."""


# ---- similarity -----------------------------------------------------------------------------------------------

_STOP = set("""
a o as os um uma uns umas de da do das dos em no na nos nas por para pra com sem sobre entre e ou que se seu sua seus
suas como mais menos muito muita ja nao sim eu voce voces ele ela eles elas isso esse essa este esta aqui ali quando
onde porque qual quais quem ao aos foi ser sao esta estao tem ter vai vao the an of to in on for with without about
and or is are was were be been it its this that these those you your we our they their how why what which who when
where from by at as vs into out up new top best do does did can will
في من على الى إلى عن مع هذا هذه ذلك التي الذي كيف لماذا ما ماذا هل او أو ثم كل بعد قبل عند
""".split())
_EP = re.compile(r"\b(?:Ep\.?|Episódio|Episode)\s*\d+|الحلقة\s*\d+", re.IGNORECASE)
_TAIL = re.compile(r"\s*\((?:tendência|nicho do canal)[^)]*\)\s*$", re.IGNORECASE)
_REPOST = re.compile(r"^Repostagem \([^)]*\):\s*", re.IGNORECASE)
_SERIES_TOPIC = re.compile(r"^Episódio\s+\d+\s+da\s+série[^.]*\.\s*", re.IGNORECASE)
_BOILER = re.compile(r"Crie uma aventura nova[^.]*\.", re.IGNORECASE)
NOTE_MARK = "\n\n[Adaptação"  # suggest.adaptation_note: not part of the topic itself


def _strip(text: str) -> str:
    text = _REPOST.sub("", (text or "").split(NOTE_MARK)[0].strip())
    # kids (/infantil, channels.number_episode): "Episódio N da série X. [Premissa:] …" — only the premise counts
    text = _BOILER.sub(" ", _SERIES_TOPIC.sub("", text)).replace("Premissa:", " ")
    return _TAIL.sub("", _EP.sub(" ", text.strip()))


def _plain(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(ch))


_STOP = {_plain(w) for w in _STOP}


def words(text: str, drop: set[str] | frozenset = frozenset()) -> set[str]:
    """Content words, without accents, stop words, plural -s / Arabic al- and the given channel/series words."""
    out = set()
    for w in re.findall(r"\w+", _plain(text)):
        if w in _STOP or (len(w) < 3 and not w.isdigit()):
            continue
        if w.isascii() and len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        elif not w.isascii() and len(w) > 4 and w.startswith("ال"):
            w = w[2:]
        w = w[:8]  # crude stem: inteligencia / inteligente
        if w not in drop:
            out.add(w)
    return out


def _overlap(a: set, b: set) -> tuple[float, int]:
    shared = len(a & b)
    return (shared / min(len(a), len(b)) if a and b else 0.0), shared


def _cosine(a: set, b: set) -> float:
    return len(a & b) / (len(a) * len(b)) ** 0.5 if a and b else 0.0


def similarity(title_a: str, topic_a: str, title_b: str, topic_b: str, drop: set[str] | frozenset = frozenset()) -> float:
    """0..1: how much two videos look like the same video (title words, then title + topic words)."""
    ta, tb = words(_strip(title_a), drop), words(_strip(title_b), drop)
    ov, shared = _overlap(ta, tb)
    title = ov if shared >= 2 or (shared == 1 and len(ta) == len(tb) == 1) else 0.0
    fa, fb = ta | words(_strip(topic_a), drop), tb | words(_strip(topic_b), drop)
    topic = _cosine(fa, fb) if len(fa & fb) >= 3 else 0.0
    return max(title / TITLE_OVERLAP, topic / TOPIC_COSINE) / 2  # 0.5 = at the threshold of either measure


def _channel_words(session: Session, channel: Channel) -> set[str]:
    names = [channel.name, *((channel.profile or {}).get("series_names") or {}).values()]
    for p in session.exec(select(Project.series_id).where(Project.channel_id == channel.id,
                                                          Project.series_id.is_not(None)).distinct()).all():
        s = session.get(Channel, p)
        if s:
            names += [s.name, *((s.profile or {}).get("series_names") or {}).values()]
    return set().union(*(words(n) for n in names))


def similar_recent(session: Session, channel: Channel | None, title: str, topic: str, days: int = DAYS,
                   exclude_id: int | None = None) -> list[dict]:
    """Videos of the same channel in the last `days` days that look like this one, most similar first."""
    if not channel:
        return []
    since = now() - timedelta(days=days)
    recent = [p for p in session.exec(select(Project).where(Project.channel_id == channel.id)
                                      .order_by(col(Project.id).desc())).all()
              if p.id != exclude_id and p.created_at and p.created_at.replace(tzinfo=since.tzinfo) >= since]
    drop = _channel_words(session, channel)
    # words in most of the channel's recent titles say nothing about the topic (series name, "Ep.", recurring format)
    if len(recent) >= 3:
        counts: dict[str, int] = {}
        for p in recent:
            for w in words(_strip(p.title)):
                counts[w] = counts.get(w, 0) + 1
        drop |= {w for w, n in counts.items() if n > len(recent) / 2}
    out = []
    for p in recent:
        score = similarity(title, topic, p.title, p.topic, drop)
        if score >= 0.5:
            out.append({"project_id": p.id, "title": p.title, "channel_id": channel.id, "channel": channel.name,
                        "created_at": p.created_at.isoformat(), "score": round(min(score, 1.0), 2)})
    return sorted(out, key=lambda x: -x["score"])


def check_creation(session: Session, data: dict, channel: Channel | None) -> list[str]:
    """Run before a project is created. Raises ChannelClosed / SimilarTopic; returns non-blocking warnings."""
    check_new_project(channel, data.get("languages") or [])
    warnings: list[str] = []
    if not channel:
        return warnings
    if channel.made_for_kids:
        warnings.append(f"{channel.name} é um canal infantil: o vídeo sai marcado como “conteúdo para crianças”.")
    title, topic = data.get("title") or "", data.get("topic") or ""
    # kids episodes: an episode queued without a premise has nothing to compare yet (and the scriptwriter reads the
    # previous titles/endings); with a premise, a repeat is only a warning (/infantil has no "fazer mesmo assim")
    kids = str(data.get("style") or "") == "kids"
    premise = words(_strip(title), _channel_words(session, channel))
    similar = [] if kids and not premise else similar_recent(session, channel, title, topic)
    if similar:
        s = similar[0]
        msg = (f"Parecido com “{s['title']}” (projeto {s['project_id']}, {s['created_at'][:10]}) no canal "
               f"{channel.name}, nos últimos {DAYS} dias.")
        if data.get("force_similar") or kids:
            warnings.append(msg)
        else:
            raise SimilarTopic(msg + " Use “fazer mesmo assim” para criar mesmo assim.", similar)
    return warnings


# ---- before scheduling ----------------------------------------------------------------------------------------

_LANG_STOP = {
    "pt-BR": set("de que não uma para com os do da em no na é você mais isso como mas ao das dos pelo pela também está são".split()),
    "en-US": set("the and of to is you that it in for with this are what was have not but they be on your".split()),
}


def detect_language(text: str) -> str | None:
    """pt-BR / en-US / ar-AE when the text clearly is one of them (≥ 20 words), else None."""
    letters = [c for c in text or "" if c.isalpha()]
    if len(letters) < 60:
        return None
    arabic = sum("؀" <= c <= "ۿ" or "ݐ" <= c <= "ݿ" for c in letters) / len(letters)
    if arabic > 0.6:
        return "ar-AE"
    if arabic > 0.2:
        return None
    toks = re.findall(r"[^\W\d_]+", text.lower())
    if len(toks) < 20:
        return None
    hits = {lang: sum(t in stop for t in toks) for lang, stop in _LANG_STOP.items()}
    best, other = sorted(hits, key=hits.get, reverse=True)
    return best if hits[best] >= 5 and hits[best] >= 3 * max(hits[other], 1) else None


def script_text(content: dict) -> str:
    return " ".join(str(sc.get("narration") or "") for sc in (content or {}).get("scenes") or [])


def avatar_on_screen(session: Session, project: Project | None, render: Render) -> bool:
    """YouTube's "altered or synthetic content" box is ticked only when the AI avatar appears in the video (user's
    rule, 2026-11-24): a cloned voice or AI stills alone leave it off. Renders record `avatar_on_screen`
    (cvm/render/compose.py); older ones fall back to their avatar scenes and the presenter mode (overlay/podcast)."""
    meta = render.meta or {}
    if "avatar_on_screen" in meta:
        return bool(meta["avatar_on_screen"])
    if meta.get("avatar_scenes"):
        return True
    if project is None:
        return False
    from cvm.render.compose import presenter_mode

    return presenter_mode(session, project) in ("overlay", "podcast") and bool(project.avatar_id)


def synthetic_reasons(session: Session, project: Project) -> list[str]:
    from cvm.visuals.sources import is_ai_still

    assets = session.exec(select(Asset).where(Asset.project_id == project.id)).all()
    reasons = []
    if any(a.kind == "narration" for a in assets) or project.style == "kids":
        reasons.append("voz sintética/clonada")
    if project.avatar_id or any(a.kind == "lipsync" for a in assets):
        reasons.append("avatar de IA")
    if project.style == "kids" or any(a.kind == "visual" and is_ai_still(a.source) for a in assets):
        reasons.append("imagens geradas por IA")
    return reasons


def check_publication(session: Session, project: Project, render: Render, channel: Channel) -> None:
    """Hard checks before a render is scheduled on a channel; sets the kit/upload flags on `render.meta`."""
    if render.language != channel.language:
        raise PublishRefused(f"O vídeo está em {render.language}, mas o canal {channel.name} é {channel.language}: "
                             "cada canal recebe só o seu idioma (adaptação, não tradução).")
    script = session.exec(select(Script).where(Script.project_id == project.id, Script.language == render.language)
                          .order_by(col(Script.version).desc())).first()
    found = detect_language(script_text(script.content)) if script else None
    if found and found.split("-")[0] != channel.language.split("-")[0]:
        raise PublishRefused(f"O roteiro do vídeo “{project.title}” parece estar em {found}, mas o canal "
                             f"{channel.name} é {channel.language}. Revise o roteiro antes de agendar.")
    series = series_of(session, project)
    if series and series.made_for_kids and not channel.made_for_kids:
        raise PublishRefused(f"“{project.title}” é de uma série infantil ({series.name}) e o canal {channel.name} não "
                             "está marcado como feito para crianças. Marque o canal ou escolha o canal infantil.")
    reasons = synthetic_reasons(session, project)
    render.meta = {**(render.meta or {}), "made_for_kids": bool(channel.made_for_kids),
                   "synthetic_disclosure": avatar_on_screen(session, project, render), "synthetic_reasons": reasons}
    session.add(render)
