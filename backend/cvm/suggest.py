"""Which channel/avatar/languages/style a trend or Claude idea goes to by default, and the project it becomes."""

from sqlmodel import Session, select

from cvm.models import Avatar, Channel, Job, Project, Trend

REGION_LANG = {"BR": "pt-BR", "AE": "ar-AE", "US": "en-US"}
# TODO(install wizard part 2, export cleanup — docs/superpowers/specs/2026-11-24-install-wizard-design.md §"padrões
# do dono"): the BR/AE/US table and the pt-BR default for "no region" are the owner's; the clean copy should map every
# region the wizard offers (features._clean) to a language. Until then any other region (DE, JP, GLOBAL...) gets en-US.
UNMAPPED_LANG = "en-US"


def region_lang(region: str | None) -> str | None:
    """The language of a trend region: the table above; None for no region (callers keep their old default);
    en-US for any other region the wizard lets the user pick (GLOBAL included)."""
    if not region:
        return None
    return REGION_LANG.get(region.upper(), UNMAPPED_LANG)


def default_avatar(session: Session, channel: Channel | None) -> int | None:
    """The channel's avatar, else the only ready avatar (so recordings are used without extra setup)."""
    prefs = (channel.prefs or {}) if channel else {}
    if prefs.get("no_avatar"):
        return None
    if prefs.get("avatar_id"):
        return prefs["avatar_id"]
    if channel and channel.avatar_id:
        return channel.avatar_id
    ready = session.exec(select(Avatar).where(Avatar.status == "ready")).all()
    return ready[0].id if len(ready) == 1 else None


def languages_of(channel: Channel) -> list[str]:
    """One channel = one language (other languages are adaptations made on the sibling channels)."""
    return [channel.language]


def platforms_of(channel: Channel | None) -> list[str]:
    return ((channel.prefs or {}).get("platforms") or []) if channel else []


def save_prefs(session: Session, data: dict) -> None:
    """Remember what the user picked on /novo as this channel's defaults."""
    channel = session.get(Channel, data.get("channel_id")) if data.get("channel_id") else None
    if not channel:
        return
    channel.prefs = {**(channel.prefs or {}), "platforms": data.get("platforms") or [],
                     "avatar_id": data.get("avatar_id"), "no_avatar": bool(data.get("no_avatar"))}
    if data.get("hook") is not None:  # /novo's "Gancho" / "Vinheta" switches (lesson and review channels)
        channel.prefs["hook"] = bool(data["hook"])
    if data.get("bumper") is not None:
        old = channel.prefs.get("bumper_mode")
        channel.prefs["bumper_mode"] = (old if old in ("short", "full") else "short") if data["bumper"] else "none"
    if "bumper_from" in data:  # another channel's bumper (its id) or None = this channel's own
        channel.prefs["bumper_from"] = data.get("bumper_from") or None
    channel.prefs.pop("languages", None)
    session.add(channel)
    session.commit()


def pick_channel(channels: list[Channel], region: str | None, channel_id: int | None = None) -> Channel | None:
    by_id = {c.id: c for c in channels}
    if channel_id in by_id:
        return by_id[channel_id]
    lang = region_lang(region)
    return next((c for c in channels if c.language == lang), channels[0] if channels else None)


def suggestion(session: Session, channels: list[Channel], region: str | None, idea: dict | None = None) -> dict:
    """{channel_id, avatar_id, languages, style} for a trend (by region) or an idea (its channel_id/style first)."""
    ch = pick_channel(channels, region, (idea or {}).get("channel_id"))
    return {
        "channel_id": ch.id if ch else None,
        "avatar_id": default_avatar(session, ch),
        "languages": languages_of(ch) if ch else [region_lang(region) or "pt-BR"],
        "platforms": platforms_of(ch),
        "style": (idea or {}).get("style") or (ch.default_style if ch else "explainer"),
    }


def used_trends(session: Session) -> dict[int, int]:
    """trend_id -> project_id for trends that already became a project."""
    return {p.trend_id: p.id for p in session.exec(select(Project).where(Project.trend_id.is_not(None))).all()}


def new_project(session: Session, data: dict, warnings: list[str] | None = None, before_job=None,
                start_job: bool = True) -> Project:
    """Create a project + its job and put it in the pipeline (the script step starts on its own).
    Runs cvm/guards.py first: ChannelClosed (paused/archived channel, foreign language) or SimilarTopic (a similar
    video on the channel in the last 30 days, unless data["force_similar"]); other warnings go into `warnings`.
    start_job=False: the project waits without a job until `start(session, project)` (/fabrica/novo attaches the
    files and links first, cvm/media_links.py)."""
    from cvm import guards
    from cvm.channels import number_episode

    project = Project.model_validate(data)
    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    found = guards.check_creation(session, {**data, "languages": project.languages, "style": project.style}, channel)
    if warnings is not None:
        warnings.extend(found)
    number_episode(session, project)
    if project.avatar_id is None and not data.get("no_avatar"):
        project.avatar_id = default_avatar(session, channel)
    session.add(project)
    session.commit()
    session.refresh(project)
    if before_job:  # e.g. attach the material the script needs before the job (and its script step) starts
        try:
            before_job(project)
        except Exception:
            session.rollback()  # no half-made project left behind (no job, maybe a media row)
            from cvm.models import Asset

            for a in session.exec(select(Asset).where(Asset.project_id == project.id)).all():
                session.delete(a)
            session.delete(session.get(Project, project.id))
            session.commit()
            raise
    if start_job:
        start(session, project)
    session.refresh(project)
    return project


def start(session: Session, project: Project) -> Job:
    """Put a project in the pipeline (its job; the script step starts on its own). A project that has one keeps it."""
    job = session.exec(select(Job).where(Job.project_id == project.id)).first()
    if job:
        return job
    job = Job(project_id=project.id)
    session.add(job)
    session.commit()

    from cvm.workers.tasks import run_job

    run_job(job.id)
    return job


def project_from(session: Session, trend: Trend, idea: dict | None = None, overrides: dict | None = None,
                 note: str = "") -> Project:
    """Produce a trend (or a Claude idea built on it) with the suggested channel's defaults."""
    from cvm.channels import producing

    channels = producing(session)
    s = suggestion(session, channels, trend.region, idea)
    topic = (idea or {}).get("topic")
    data = {
        "title": topic or trend.title,
        "topic": (f"{topic} (tendência: {trend.title})" if topic else trend.title) + note,
        "trend_id": trend.id,
        "style": s["style"],
        "languages": s["languages"],
        "platforms": s["platforms"],
        "channel_id": s["channel_id"],
        "avatar_id": s["avatar_id"],
        **(overrides or {}),
    }
    return new_project(session, data)


def project_from_channel_idea(session: Session, channel: Channel, idea: dict, note: str = "",
                              force_similar: bool = False) -> Project:
    """Produce one of a channel's niche ideas with that channel's defaults (style from the idea if given)."""
    trend = session.get(Trend, (idea.get("trend_ids") or [None])[0]) if idea.get("trend_ids") else None
    topic = idea["topic"]
    return new_project(session, {
        "force_similar": force_similar,
        "title": topic,
        "topic": f"{topic} (nicho do canal {channel.name}{f'; tendência: {trend.title}' if trend else ''})" + note,
        "trend_id": trend.id if trend else None,
        "style": idea.get("style") or channel.default_style,
        "languages": languages_of(channel),
        "platforms": platforms_of(channel),
        "channel_id": channel.id,
        "avatar_id": default_avatar(session, channel),
    })


# ---- one topic, several channels of a group (adaptation, never translation) -----------------------------------

AUDIENCE = {
    "pt-BR": "o público brasileiro: exemplos do Brasil, valores em R$",
    "en-US": "o público americano: exemplos dos EUA, valores em US$",
    "ar-AE": "o público árabe do Golfo: exemplos dos Emirados e do Golfo, valores em AED (dirham)",
}


def adaptation_note(channel: Channel, others: list[Channel]) -> str:
    """Appended to the topic of each project when one topic goes to several channels: the scriptwriter writes a
    script of its own for this channel's audience instead of translating the sibling's (guards.NOTE_MARK)."""
    from cvm.guards import NOTE_MARK

    also = ", ".join(f"{c.name} ({c.language})" for c in others) or "outros canais do grupo"
    return (f"{NOTE_MARK} para {channel.name}, {channel.language}] O mesmo tema também sai em {also}. Este roteiro é "
            f"próprio deste canal: gancho, título, exemplos, referências e moeda pensados para "
            f"{AUDIENCE.get(channel.language, 'o público deste canal')}. Adaptar, nunca traduzir.")


def group_channels(session: Session, channel_ids: list[int]) -> list[Channel]:
    """The chosen channels, in the order given, without repeats; unknown ids are an error."""
    out: list[Channel] = []
    for cid in dict.fromkeys(int(c) for c in channel_ids):
        c = session.get(Channel, cid)
        if not c:
            raise ValueError(f"canal {cid} não existe")
        out.append(c)
    return out


def for_channel(session: Session, data: dict, main: Channel | None, channel: Channel) -> dict:
    """The /novo choices made for `main`, carried to a sibling channel with that channel's own defaults (language,
    platforms, avatar); avatar-specific choices (voice, podcast desk) stay with the main channel."""
    if main and channel.id == main.id:
        return {**data, "channel_id": channel.id, "languages": languages_of(channel),
                "platforms": data.get("platforms") or platforms_of(channel)}
    prefs = channel.prefs or {}
    out = {k: v for k, v in data.items() if k not in ("voice_variant", "presenter", "avatar_id", "no_avatar", "series_id")}
    avatar = default_avatar(session, channel)
    main_avatar = data.get("avatar_id") or (default_avatar(session, main) if main else None)
    if data.get("presenter") and avatar and avatar == main_avatar:
        # same presenter on both channels (e.g. the podcast desk): the sibling keeps the format, so the three
        # language versions of one topic look like one show (235/236 lost it and came out full-frame)
        out["presenter"] = data["presenter"]
    # the chosen voice goes along when the sibling uses the same avatar and that avatar has it in the sibling's language
    variant = data.get("voice_variant")
    if variant and avatar and avatar == (data.get("avatar_id") or avatar):
        from cvm.models import VoiceProfile

        has = any((v.params or {}).get("variant") == variant for v in session.exec(
            select(VoiceProfile).where(VoiceProfile.avatar_id == avatar, VoiceProfile.language == channel.language)).all())
        if has:
            out["voice_variant"] = variant
    return {**out, "channel_id": channel.id, "languages": languages_of(channel),
            "platforms": data.get("platforms") or platforms_of(channel),  # a format chosen on /novo goes along
            "avatar_id": avatar, "no_avatar": bool(prefs.get("no_avatar"))}


def new_projects(session: Session, data: dict, channel_ids: list[int], warnings: list[str] | None = None,
                 before_job=None, start_job: bool = True) -> list[Project]:
    """One project per chosen channel (never one project with several languages). Every channel is checked first
    (closed channel, similar recent video), so either all are created or none; raises ChannelClosed/SimilarTopic."""
    from cvm import guards

    chosen = group_channels(session, channel_ids)
    main = next((c for c in chosen if c.id == data.get("channel_id")), chosen[0] if chosen else None)
    chosen = [main, *[c for c in chosen if c.id != main.id]] if main else []
    datas = []
    similar: list[dict] = []
    for c in chosen:
        d = for_channel(session, data, main, c)
        if len(chosen) > 1:
            d["topic"] = (d.get("topic") or d.get("title") or "") + adaptation_note(c, [x for x in chosen if x.id != c.id])
        try:
            guards.check_creation(session, {**d, "languages": d.get("languages") or [c.language]}, c)
        except guards.SimilarTopic as e:
            similar += e.similar
        datas.append(d)
    if similar:
        names = "; ".join(f"“{s['title']}” em {s['channel']}" for s in similar)
        raise guards.SimilarTopic(f"Já há vídeo parecido nos últimos {guards.DAYS} dias: {names}. "
                                  "Use “fazer mesmo assim” para criar mesmo assim.", similar)
    return [new_project(session, {**d, "force_similar": True}, warnings, before_job, start_job) for d in datas]
