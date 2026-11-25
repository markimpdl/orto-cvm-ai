"""Data model: multi-channel, multi-avatar content pipeline."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(UTC)


class Platform(StrEnum):
    youtube = "youtube"
    youtube_shorts = "youtube_shorts"
    instagram = "instagram"
    tiktok = "tiktok"


class PublishMode(StrEnum):
    manual = "manual"
    api = "api"
    browser = "browser"


class Style(StrEnum):
    dark = "dark"
    react = "react"
    explainer = "explainer"
    top_list = "top_list"
    kids = "kids"  # animated story for children: FLUX cartoon stills, every scene animated by Wan
    lesson = "lesson"  # video lesson: the user's slides (PDF pages), images and videos, in order, explained by the presenter
    review = "review"  # review / unboxing from the user's own footage (his recorded audio kept where marked), upbeat delivery
    live = "live"  # best moments of the user's own live (cvm/lives): the live's recorded audio carries it, short hook only


class Avatar(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    base_video_dir: str | None = None  # data/avatars/<id>/base
    podcast_video_dir: str | None = None  # data/avatars/<id>/podcast: the same person at a podcast desk (presenter "podcast")
    status: str = "new"  # new | capturing | processing | ready
    notes: str | None = None
    created_at: datetime = Field(default_factory=now)


class VoiceProfile(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    avatar_id: int = Field(foreign_key="avatar.id", index=True)
    language: str  # pt-BR | ar-AE | en-US
    engine: str = "voxcpm2"
    ref_audio_path: str | None = None
    ref_text: str | None = None
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))


class Channel(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    platform: Platform
    language: str = "pt-BR"
    default_style: Style = Style.explainer
    avatar_id: int | None = Field(default=None, foreign_key="avatar.id")
    publish_mode: PublishMode = PublishMode.manual
    account_ref: str | None = None  # Keychain item name for tokens
    schedule: dict = Field(default_factory=dict, sa_column=Column(JSON))  # e.g. {"slots": ["Mon 18:00"]}
    brief: str | None = None  # audience, tone, recurring themes, taboos, CTA — read by the scriptwriter and SEO agents
    allow_official_clips: bool = False  # short clips from the subject owner's channel (may get Content ID claims)
    allow_article_images: bool = False  # lead image of news articles, credited to the outlet
    # kids series cast: {name: {"description": English voice description, "voice": ref wav, "ref_text": str}};
    # each voice is designed once (VoxCPM2) and reused in every episode
    characters: dict = Field(default_factory=dict, sa_column=Column(JSON))
    made_for_kids: bool = False  # YouTube "made for kids" (COPPA): comments off, no personalised ads
    # scenes: avatar only in its scenes | overlay: cut-out over the whole video | podcast: studio presenter |
    # editorial: narration keeps this channel's avatar voice, but no avatar is shown on screen
    presenter: str = "scenes"
    # last choices made on /novo ("Ajustar") for this channel — the defaults for every new video of it:
    # {"languages": [...], "platforms": [...], "avatar_id": int | None, "no_avatar": bool}
    prefs: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # one channel = one language = one YouTube channel (docs/plano-canais-e-voz.md, Phase 1)
    status: str = "active"  # active | paused (no new production) | archived (hidden; history kept)
    youtube_handle: str | None = None
    youtube_channel_id: str | None = None
    group: str | None = None  # sibling channels (same persona, other languages), e.g. "my-show"
    # niche, audience, persona, tone, voice{engine,profile,speed,energy}, content_pillars[], banned_topics[],
    # formats[{kind,min_s,max_s,per_week}], publish_slots[{format,days[],time,tz}], title_patterns[],
    # description_footer, default_tags[], youtube_category, cta{spoken,written}, cross_links[] — see cvm/channels.py
    profile: dict = Field(default_factory=dict, sa_column=Column(JSON))
    uid: str | None = None  # global id from the old online cluster (removed 2026-11-24); kept, matches SharedEpisode rows


class Trend(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)  # google | youtube | reddit | tiktok | x | news
    region: str = Field(index=True)
    key: str = Field(index=True)  # normalized title for dedupe
    title: str
    url: str | None = None
    first_seen: datetime = Field(default_factory=now)
    last_seen: datetime = Field(default_factory=now)
    score: float = 0.0
    score_breakdown: dict = Field(default_factory=dict, sa_column=Column(JSON))
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))


class TrendSnapshot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    trend_id: int = Field(foreign_key="trend.id", index=True)
    captured_at: datetime = Field(default_factory=now)
    rank: int | None = None
    metric: float | None = None  # views / traffic / upvotes, source dependent
    engagement: float | None = None


class Project(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    topic: str
    trend_id: int | None = Field(default=None, foreign_key="trend.id")
    channel_id: int | None = Field(default=None, foreign_key="channel.id")
    # kids: the series (a channel holding the bible, cast/voices and image seed) when it is not the publishing channel,
    # e.g. a kids series published on a kids channel of the same language
    series_id: int | None = Field(default=None, foreign_key="channel.id")
    avatar_id: int | None = Field(default=None, foreign_key="avatar.id")
    style: Style = Style.explainer
    languages: list = Field(default_factory=lambda: ["pt-BR"], sa_column=Column(JSON))
    platforms: list = Field(default_factory=list, sa_column=Column(JSON))  # empty = channel's platform
    # None = the channel's choice | "scenes" | "overlay" (cut-out avatar over the whole video) | "podcast" (the avatar
    # at a podcast desk: a boxed window bottom-right the whole video, full screen in its avatar_full scenes) |
    # "editorial" (no on-screen avatar; the avatar identity remains available to voice narration)
    presenter: str | None = None
    media_mode: str = "mix"  # visuals: "mix" (your files + online/AI) | "mine" (only your files) | "online" (ignore yours)
    # your videos: "full" (each clip whole) | "highlights" (best moments only: dull and repeated parts cut,
    # cvm/visuals/highlights.py → Asset.meta["segments"])
    media_cut: str = "full"
    voice_variant: str | None = None  # one of the avatar's named voices (VoiceProfile.params.variant); None = default
    hook: bool | None = None  # opening hook + promise; None = the channel's default (prefs.hook, else yes)
    bumper: bool | None = None  # channel bumper after the opening; None = the channel's default (prefs.bumper_mode != "none")
    # the user's own script draft from /fabrica/novo: the scriptwriter starts from it (keeps its ideas and order)
    draft: str | None = None
    # background music for this video: "none" | "acestep" | "elevenlabs" (cvm/render/music_eleven.py, ACE-Step as
    # fallback); None = the channel's default (prefs.music + prefs.music_source)
    music_source: str | None = None
    autopilot: bool = False  # queued by the autopilot (counts toward its daily limit)
    created_at: datetime = Field(default_factory=now)
    uid: str | None = None  # global id from the old online cluster (removed 2026-11-24); unused, kept as a column


class Script(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    language: str
    version: int = 1
    content: dict = Field(default_factory=dict, sa_column=Column(JSON))  # see scripting/schema.py
    approved: bool = False
    created_at: datetime = Field(default_factory=now)


class Asset(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    kind: str  # narration | lipsync | broll | image | screenshot | music | caption | thumbnail
    language: str | None = None
    scene: int | None = None
    path: str
    source: str | None = None  # pexels | pixabay | flux | news:<url> | yt-dlp:<url> ...
    license: str | None = None
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))


class Render(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    language: str
    profile: str  # key in render/profiles.yaml
    part: int | None = None  # cut number when a long video is split into shorts
    path: str | None = None
    status: str = "pending"
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))
    seo: dict = Field(default_factory=dict, sa_column=Column(JSON))  # title, description, tags, hashtags


class Publication(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    render_id: int = Field(foreign_key="render.id", index=True)
    channel_id: int = Field(foreign_key="channel.id", index=True)
    mode: PublishMode = PublishMode.manual
    scheduled_at: datetime | None = None
    status: str = "scheduled"  # scheduled | ready_for_manual_publish | published | failed
    url: str | None = None
    published_at: datetime | None = None
    metrics: dict = Field(default_factory=dict, sa_column=Column(JSON))  # latest views/likes/comments + history


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    state: str = Field(default="trend_selected", index=True)
    error: str | None = None
    history: list = Field(default_factory=list, sa_column=Column(JSON))
    progress: dict = Field(default_factory=dict, sa_column=Column(JSON))  # running step: started, fraction, label
    paused: bool = False  # the user paused it: it stops at the next step boundary and waits until resumed
    updated_at: datetime = Field(default_factory=now)


class TrendAnalysis(SQLModel, table=True):
    """Output of the trend-analyst agent: ranked video ideas built from the collected trends."""

    id: int | None = Field(default=None, primary_key=True)
    region: str | None = None
    channel_id: int | None = None  # set for a channel's niche ideas (not built from trends; "/" ignores these)
    status: str = "running"  # running | done | failed
    created_at: datetime = Field(default_factory=now)
    summary: str | None = None
    items: list = Field(default_factory=list, sa_column=Column(JSON))
    error: str | None = None


class SharedEpisode(SQLModel, table=True):
    """A series episode made on another machine, pulled from the old online cluster (removed 2026-11-24). Nothing
    writes these rows any more; get_project still reads the ones saved for the continuity of old episodes."""

    uid: str = Field(primary_key=True)
    series_uid: str = Field(index=True)
    channel_uid: str | None = None
    language: str
    number: int | None = None
    title: str
    state: str | None = None
    machine: str
    ending: list = Field(default_factory=list, sa_column=Column(JSON))
    made_at: datetime | None = None


class Setting(SQLModel, table=True):
    """Small app settings edited from the UI (e.g. "autopilot")."""

    key: str = Field(primary_key=True)
    value: dict = Field(default_factory=dict, sa_column=Column(JSON))
