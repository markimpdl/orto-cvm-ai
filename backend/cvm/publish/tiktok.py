"""A YouTube channel's TikTok account (owner, 2026-11-25: "vídeos shorts, quero ter a opção para publicar no TikTok
também").

Model: the TikTok account is a sibling Channel (platform "tiktok") in the YouTube channel's `group`, same language,
`prefs.linked_to` = the YouTube channel's id, `prefs.accounts.tiktok` = the handle (also written on the YouTube
channel, read by own-video detection, cvm/media_links.py). It never takes new videos (`channels.producing` and
`check_new_project` skip it): it only receives publications.

When a vertical Short is approved for the YouTube channel, `companions()` adds a Publication on the TikTok sibling
with the SAME render file (no extra render), unless the review unticked it. The default is the YouTube channel's
`prefs.tiktok_also` (on once connected; never on for made-for-kids channels). Publishing stays manual: the kit
on /publicar (file, caption, TikTok Studio, browser assist that never clicks Post).
"""

import re
from datetime import datetime
from pathlib import Path

import yaml
from sqlmodel import Session, select

from cvm.models import Channel, Platform, Project, Publication, PublishMode, Render

YOUTUBE = ("youtube", "youtube_shorts")
VERTICAL = ("youtube_shorts", "instagram", "tiktok")
HANDLE = re.compile(r"^[A-Za-z0-9._]{2,24}$")
CAPTION_MAX = 2200
_PROFILES = Path(__file__).resolve().parents[1] / "render" / "profiles.yaml"


def max_seconds() -> float:
    """TikTok's length limit for our uploads (render/profiles.yaml `tiktok.max_seconds`, 180 s)."""
    try:
        return float((yaml.safe_load(_PROFILES.read_text()).get("tiktok") or {}).get("max_seconds") or 180)
    except Exception:  # noqa: BLE001
        return 180.0


def clean_handle(handle: str | None) -> str:
    """"@Meu.Canal" / "tiktok.com/@meu.canal" → "Meu.Canal"; raises ValueError (pt-BR) when it is not a handle."""
    h = str(handle or "").strip()
    m = re.search(r"tiktok\.com/@([^/?#\s]+)", h)
    h = (m.group(1) if m else h).lstrip("@").strip()
    if not HANDLE.match(h):
        raise ValueError("Escreva o @ da conta do TikTok (letras, números, ponto ou _), por exemplo @meucanal.")
    return h


def _platform(c: Channel | None) -> str:
    return getattr(getattr(c, "platform", None), "value", getattr(c, "platform", None)) or ""


def is_linked_account(c: Channel | None) -> bool:
    """A TikTok account attached to a YouTube channel: receives publications, never new videos."""
    return bool(c) and _platform(c) == "tiktok" and bool((c.prefs or {}).get("linked_to"))


def linked(session: Session, channel: Channel) -> Channel | None:
    """The TikTok account connected to this YouTube channel (not archived), if any."""
    if _platform(channel) not in YOUTUBE or not channel.id:
        return None
    for c in session.exec(select(Channel).where(Channel.platform == Platform.tiktok)).all():
        if (c.prefs or {}).get("linked_to") == channel.id and (c.status or "active") != "archived":
            return c
    return None


def parent(session: Session, tiktok: Channel) -> Channel | None:
    pid = (tiktok.prefs or {}).get("linked_to")
    return session.get(Channel, int(pid)) if pid else None


def default_on(channel: Channel) -> bool:
    """Is "Publicar também no TikTok" ticked by default? Never for a made-for-kids channel."""
    if channel.made_for_kids:
        return False
    return (channel.prefs or {}).get("tiktok_also", True) is not False


def _group_name(session: Session, channel: Channel) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", channel.name.lower()).strip("-") or f"canal-{channel.id}"
    taken = {g for g in session.exec(select(Channel.group)).all() if g}
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def connect(session: Session, channel: Channel, handle: str) -> Channel:
    """Create (or update) the TikTok sibling of a YouTube channel. Raises ValueError (pt-BR) on a bad request."""
    if _platform(channel) not in YOUTUBE:
        raise ValueError("Só um canal do YouTube pode ter uma conta do TikTok ligada.")
    if (channel.status or "active") in ("archived", "series"):
        raise ValueError(f"O canal {channel.name} está arquivado ou é uma série: não publica no TikTok.")
    h = clean_handle(handle)
    if not channel.group:
        channel.group = _group_name(session, channel)
    tk = linked(session, channel)
    if tk is None:
        tk = Channel(name=f"{channel.name} · TikTok", platform=Platform.tiktok, language=channel.language,
                     default_style=channel.default_style, avatar_id=channel.avatar_id, publish_mode=PublishMode.browser,
                     made_for_kids=channel.made_for_kids, presenter=channel.presenter, status="active")
    tk.group = channel.group
    tk.language = channel.language
    tk.prefs = {**(tk.prefs or {}), "linked_to": channel.id, "accounts": {**((tk.prefs or {}).get("accounts") or {}), "tiktok": h}}
    prefs = dict(channel.prefs or {})
    prefs["accounts"] = {**(prefs.get("accounts") or {}), "tiktok": h}
    if "tiktok_also" not in prefs:
        prefs["tiktok_also"] = not channel.made_for_kids
    channel.prefs = prefs
    session.add_all([channel, tk])
    session.commit()
    session.refresh(tk)
    return tk


def disconnect(session: Session, channel: Channel) -> Channel | None:
    """Unlink: the TikTok sibling is archived (its history stays), the handle leaves the YouTube channel."""
    tk = linked(session, channel)
    prefs = dict(channel.prefs or {})
    accounts = {k: v for k, v in (prefs.get("accounts") or {}).items() if k != "tiktok"}
    prefs["accounts"] = accounts
    prefs.pop("tiktok_also", None)
    channel.prefs = prefs
    session.add(channel)
    if tk:
        tk.status = "archived"
        session.add(tk)
    session.commit()
    return tk


def info(session: Session, channel: Channel) -> dict:
    """For /canais/[id] and the render review: {connected, channel_id, handle, also (default), kids}."""
    tk = linked(session, channel)
    handle = ((tk.prefs or {}).get("accounts") or {}).get("tiktok") if tk else None
    return {"connected": bool(tk), "channel_id": tk.id if tk else None, "handle": handle,
            "also": bool(tk) and default_on(channel), "kids": bool(channel.made_for_kids),
            "max_seconds": max_seconds()}


# ---- publishing ----------------------------------------------------------------------------------------------------

def is_vertical_short(render: Render) -> bool:
    return render.profile.removesuffix("_cuts") in VERTICAL


def render_seconds(render: Render) -> float | None:
    d = (render.meta or {}).get("duration")
    if d:
        try:
            return float(d)
        except (TypeError, ValueError):
            pass
    if render.path and Path(render.path).exists():
        try:
            from cvm.media import duration

            return float(duration(render.path))
        except Exception:  # noqa: BLE001
            return None
    return None


def companions(session: Session, items: list[dict], pubs: list[Publication], notes: list[str]) -> list[dict]:
    """The TikTok publications to add next to the approved ones: [{render_id, channel_id, scheduled_at, mode}].
    `items` are the schedule() items (each may carry `tiktok`: true/false; missing = the channel's default) and
    `pubs` the publications just made for them, in the same order. Skipped Shorts get a message in `notes`."""
    from cvm.publish.service import _default_slot

    out: list[dict] = []
    taken = {(i["render_id"], int(i["channel_id"])) for i in items}
    for it, pub in zip(items, pubs, strict=False):
        channel, render = session.get(Channel, int(it["channel_id"])), session.get(Render, int(it["render_id"]))
        if not channel or not render or _platform(channel) not in YOUTUBE or not is_vertical_short(render):
            continue
        tk = linked(session, channel)
        want = it.get("tiktok")
        want = default_on(channel) if want is None else bool(want)
        if not tk or not want or (render.id, tk.id) in taken:
            continue
        if session.exec(select(Publication).where(Publication.render_id == render.id, Publication.channel_id == tk.id,
                                                  Publication.status != "failed")).first():
            continue  # already going to TikTok
        secs = render_seconds(render)
        if secs and secs > max_seconds() + 0.5:
            notes.append(f"TikTok: o Short tem {int(secs)} s, acima do limite de {int(max_seconds())} s; "
                         "foi só para o YouTube.")
            continue
        # the TikTok account's own publish_slots (Shorts), else the same time as the YouTube post
        from cvm import schedule as calendar

        try:
            when = calendar.slot_for(session, tk, render)
        except Exception:  # noqa: BLE001
            when = None
        when = when or pub.scheduled_at or _default_slot(session, channel, render)
        out.append({"render_id": render.id, "channel_id": tk.id,
                    "scheduled_at": when.isoformat() if isinstance(when, datetime) else when, "mode": tk.publish_mode.value})
        taken.add((render.id, tk.id))
    return out


# ---- caption -------------------------------------------------------------------------------------------------------

# YouTube-only lines that make no sense on TikTok: subscribe/bell asks, end screen, "link in the description",
# chapters, links (not clickable in a TikTok caption)
_YT_ONLY = re.compile(
    r"inscrev|subscri|ative o sino|sininho|ative as notifica|\bthe bell\b|tela final|end ?screen|"
    r"link na descri|link in the description|na descri[çc][ãa]o|in the description|no youtube|on youtube|"
    r"pr[óo]ximo v[íi]deo|next video|v[íi]deo completo|full video|"
    r"اشترك|الاشتراك|الجرس",
    re.IGNORECASE)
_URL = re.compile(r"https?://\S+|www\.\S+")
_CHAPTER = re.compile(r"^\s*\(?\d{1,2}:\d{2}")
_ONLY_TAGS = re.compile(r"^(?:\s*#[\w؀-ۿ]+)+\s*$")
_DROP_TAGS = {"shorts", "short", "youtubeshorts", "ytshorts", "youtube", "subscribe", "inscrevase", "inscreva"}


def _tag(t: str) -> str:
    t = re.sub(r"[^\w؀-ۿ]", "", str(t).replace("#", "").strip(), flags=re.UNICODE)
    return t


def hashtags(seo: dict, n_min: int = 3, n_max: int = 5) -> list[str]:
    """3–5 hashtags: the SEO's hashtags first, then single-word tags; no #shorts/#youtube."""
    out: list[str] = []
    seen: set[str] = set()
    tk = seo.get("tiktok") if isinstance(seo.get("tiktok"), dict) else {}
    sources = [*(tk.get("hashtags") or []), *(seo.get("hashtags") or [])]
    for i, src in enumerate([sources, [t for t in seo.get("tags") or [] if len(str(t).split()) <= 2]]):
        if i and len(out) >= n_min:
            break
        for raw in src:
            t = _tag(raw)
            if not t or t.lower() in _DROP_TAGS or t.lower() in seen or len(t) > 30:
                continue
            out.append(t)
            seen.add(t.lower())
            if len(out) >= n_max:
                return [f"#{x}" for x in out]
    return [f"#{x}" for x in out]


def _title(seo: dict) -> str:
    t = re.sub(r"\s*#shorts?\b", "", str(seo.get("title") or ""), flags=re.IGNORECASE).strip()
    return t


def _body(seo: dict, title: str, limit: int = 400) -> str:
    """The description's first plain lines, without YouTube-only lines, links, chapters and hashtag-only lines."""
    keep: list[str] = []
    for line in str(seo.get("description") or "").splitlines():
        s = line.strip()
        if not s:
            if keep and keep[-1]:
                keep.append("")
            continue
        if _URL.search(s) or _CHAPTER.match(s) or _ONLY_TAGS.match(s) or _YT_ONLY.search(s):
            continue
        s = re.sub(r"(?:\s+#[\w؀-ۿ]+)+\s*$", "", s).strip()  # trailing hashtags: added once at the end
        if not s or s.lower() == title.lower():
            continue
        keep.append(s)
    text = "\n".join(keep).strip()
    if len(text) > limit:  # cut at a sentence end inside the limit
        cut = max(text.rfind(p, 0, limit) for p in (". ", "! ", "? ", "\n"))
        text = text[: cut + 1].strip() if cut > limit // 3 else text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def caption(render: Render) -> str:
    """The TikTok caption: a hand-edited/agent `seo.tiktok.caption` when present (hashtags added if missing), else
    title + a short description + 3–5 hashtags, ≤ 2200 chars, no YouTube-only lines."""
    seo = render.seo or {}
    tags = hashtags(seo)
    own = (seo.get("tiktok") or {}).get("caption") if isinstance(seo.get("tiktok"), dict) else None
    if own and str(own).strip():
        text = str(own).strip()
        missing = [t for t in tags if t.lower() not in text.lower()] if "#" not in text else []
        return (f"{text}\n\n{' '.join(missing)}".strip() if missing else text)[:CAPTION_MAX]
    title = _title(seo)
    body = _body(seo, title)
    tag_line = " ".join(tags)
    parts = [p for p in (title, body) if p]
    text = "\n\n".join(parts)
    room = CAPTION_MAX - len(tag_line) - 2
    if len(text) > room:
        text = text[:room].rsplit(" ", 1)[0]
    return f"{text}\n\n{tag_line}".strip() if tag_line else text


# ---- AI label ------------------------------------------------------------------------------------------------------

def ai_label(session: Session, project: Project | None, render: Render, channel: Channel) -> bool:
    """TikTok's "AI-generated content" switch follows YouTube's rule (cvm/guards.py, user 2026-11-24): on only when
    the AI avatar is on screen, unless the channel (or the YouTube channel it is linked to) chose yes/no in its
    publish_defaults (install wizard)."""
    from cvm.guards import avatar_on_screen
    from cvm.publish.platforms import youtube_rules

    rule_from = channel
    if is_linked_account(channel) and not ((channel.prefs or {}).get("publish_defaults")):
        rule_from = parent(session, channel) or channel
    rule = youtube_rules(rule_from)["synthetic"]
    return avatar_on_screen(session, project, render) if rule == "auto" else rule == "yes"
