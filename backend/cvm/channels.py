"""Channel rules: one language per channel, status (active | paused | archived) and the brief the agents read,
generated from the channel profile (docs/plano-canais-e-voz.md, Phase 1)."""

import re

from sqlmodel import Session, select

from cvm.models import Channel

# "series": a novelinha with no YouTube channel of its own (bible, cast, voices); its episodes go to the channels of
# its profile.publish_group, one per language
STATUSES = ("active", "paused", "archived", "series")
FLAGS = {"pt-BR": "🇧🇷", "en-US": "🇺🇸", "ar-AE": "🇦🇪"}

# profile field -> heading in the generated brief (the agents read Portuguese headings)
_SECTIONS = [
    ("niche", "Nicho"),
    ("audience", "Público"),
    ("persona", "Persona"),
    ("tone", "Tom"),
    ("content_pillars", "Pilares (temas permitidos)"),
    ("banned_topics", "Temas proibidos"),
    ("title_patterns", "Padrões de título que funcionaram"),
    ("cta", "Chamada para ação"),
    ("rules", "Regras"),
]


class ChannelClosed(ValueError):
    """New production refused: the channel is paused/archived or the languages are not the channel's."""


def check_language(language: str | None) -> None:
    """Refuse (ValueError, pt-BR) a language this install does not offer; None/"" passes (nothing chosen)."""
    from cvm.config import settings

    if language and language not in settings.video_languages:
        raise ValueError(f"Idioma {language} não está disponível nesta instalação "
                         f"({', '.join(settings.video_languages)}).")


def producing(session: Session) -> list[Channel]:
    """Channels that take new videos (ideas, /novo, autopilot, batch)."""
    return [c for c in session.exec(select(Channel)).all() if (c.status or "active") == "active"]


def check_new_project(channel: Channel | None, languages: list[str] | None) -> None:
    if not channel:
        return
    if (channel.status or "active") != "active":
        why = {"paused": "está pausado", "series": "é uma série (publique pelo canal que a exibe)"}
        raise ChannelClosed(f"O canal {channel.name} {why.get(channel.status, 'está arquivado')}: não recebe vídeos novos.")
    extra = [lang for lang in (languages or []) if lang != channel.language]
    if extra:
        raise ChannelClosed(f"O canal {channel.name} é só {channel.language}; {', '.join(extra)} vai para o canal "
                            "daquele idioma (adaptação, não tradução).")


def _text(v) -> str:
    if isinstance(v, dict):
        return "; ".join(f"{k}: {x}" for k, x in v.items() if x)
    if isinstance(v, list):
        return "\n".join(f"- {x}" for x in v if x)
    return str(v or "").strip()


def brief_text(channel: Channel) -> str:
    """What the scriptwriter/SEO agents read: the profile in sections, then the free notes (`brief`)."""
    p = channel.profile or {}
    parts = [f"Canal: {channel.name} ({channel.language}{', infantil — made for kids' if channel.made_for_kids else ''})"]
    for key, label in _SECTIONS:
        t = _text(p.get(key))
        if t:
            parts.append(f"{label}:\n{t}" if "\n" in t else f"{label}: {t}")
    formats = [f"{f.get('kind')} {f.get('min_s', 0) // 60}–{f.get('max_s', 0) // 60} min, {f.get('per_week')}/semana"
               if f.get("max_s", 0) >= 120 else f"{f.get('kind')} {f.get('min_s')}–{f.get('max_s')} s, {f.get('per_week')}/semana"
               for f in p.get("formats") or []]
    if formats:
        parts.append("Formatos: " + "; ".join(formats))
    main = next((f for f in p.get("formats") or [] if f.get("kind") in ("episode", "long")), None)
    has_own = any(line.strip().startswith("Duração:") for line in (channel.brief or "").splitlines())
    if main and main.get("max_s") and not has_own:  # the style skills follow "Duração:"; the notes' own rule wins
        parts.append(f"Duração: {main['min_s'] // 60} a {main['max_s'] // 60} min por "
                     f"{'episódio' if main['kind'] == 'episode' else 'vídeo longo'} (target_duration_s dentro dessa faixa)")
    if channel.brief:
        parts.append(f"Observações:\n{channel.brief.strip()}")
    from cvm import private  # owner-only rules (private/channel_rules/<handle>.md); nothing without private/

    if extra := private.channel_rules(channel):
        parts.append(f"Regras privadas do canal:\n{extra}")
    return "\n\n".join(parts)


def series_of(session: Session, project) -> Channel | None:
    """The channel holding the project's series bible, cast and voices (its own channel unless series_id is set)."""
    sid = getattr(project, "series_id", None) or project.channel_id
    return session.get(Channel, sid) if sid else None


def series_episodes(session: Session, series_id: int, before_id: int | None = None) -> list:
    """Every episode of a series, oldest first, whatever channel/language it went to."""
    from sqlmodel import col, or_

    from cvm.models import Project

    q = select(Project).where(or_(Project.series_id == series_id,
                                  (Project.series_id.is_(None)) & (Project.channel_id == series_id)))
    if before_id is not None:
        q = q.where(Project.id < before_id)
    return list(session.exec(q.order_by(col(Project.id))).all())


def series_name(series: Channel, lang: str) -> str:
    return ((series.profile or {}).get("series_names") or {}).get(lang) or series.name.strip()


_EP = re.compile(r"(?:Ep\.?|Episódio|Episode|الحلقة)\s*(\d+)", re.I)


def episode_number(title: str) -> int | None:
    """The episode number in a kids-series title ("<série> – Ep. N: …"), else None."""
    hit = _EP.search(title or "")
    return int(hit[1]) if hit else None


def episode_lang(session: Session, project) -> str | None:
    if project.languages:
        return project.languages[0]
    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    return channel.language if channel else None


def next_episode_number(session: Session, series: Channel, lang: str) -> int:
    """Next number of a series in one language: after the highest one made here (numbered titles), the highest one
    recorded in prefs.episode_max (written by the old online cluster, removed 2026-11-24: kept so numbers never go
    backwards) or set by hand (prefs.last_episode, the series' language). Same rule as /infantil."""
    prefs = series.prefs or {}
    nums = [episode_number(p.title) or 0 for p in series_episodes(session, series.id) if episode_lang(session, p) == lang]
    return max([0, *nums, (prefs.get("episode_max") or {}).get(lang) or 0,
                (prefs.get("last_episode") or 0) if lang == series.language else 0]) + 1


def number_episode(session: Session, project) -> None:
    """Every kids-series episode carries its number ("<série> – Ep. N: título"): the episodes end with a teaser for
    the next one, so the sequence must never be lost. An episode queued without one (e.g. from the channel's
    ideas) gets the next number of its series and language."""
    if project.style != "kids" or episode_number(project.title):
        return
    series = series_of(session, project)
    lang = episode_lang(session, project)
    if not series or not lang:
        return
    n = next_episode_number(session, series, lang)
    name = series_name(series, lang)
    title = (project.title or "").strip()
    project.title = f"{name} – Ep. {n}" + (f": {title}" if title and title != name else "")
    project.topic = f"Episódio {n} da série animada {name}. {project.topic or ''}".strip()


def for_agents(session: Session, project, channel: Channel) -> dict:
    """The channel as the scriptwriter/SEO see it: when a series is published on another channel, the brief is the
    series bible plus the publishing channel's profile, and the cast is the series'."""
    out = {**channel.model_dump(mode="json"), "brief": brief_text(channel)}
    series = series_of(session, project)
    if series and series.id != channel.id:
        names = (series.profile or {}).get("series_names") or {}
        out.update(brief=f"Série: {names.get(channel.language) or series.name}\n\nBíblia da série:\n{series.brief or ''}"
                         f"\n\nCanal onde publica:\n{out['brief']}",
                   characters=series.characters, series_id=series.id,
                   series_name=names.get(channel.language) or series.name)
    return out


def siblings(session: Session, channel: Channel) -> list[Channel]:
    """The other active channels of the same group (same persona, other languages)."""
    if not channel.group:
        return []
    return [c for c in producing(session) if c.group == channel.group and c.id != channel.id]
