"""Which site a pasted video link comes from (Phase 6 of docs/superpowers/plans/2026-11-24-painel-reestruturacao.md).

YouTube (videos, Shorts, finished lives), Twitch (VODs and clips), Kick (VODs and clips, experimental: the site changes
often and yt-dlp follows behind), TikTok and Instagram (public posts only: user's decision D14, nothing that needs a
login). Everything is read and downloaded by yt-dlp (cvm/lives/ingest.py); this module only knows the URLs, the ids
and the site's error messages.

- `detect(url)` → {platform, id (None for short links resolved by yt-dlp), url (canonical), original_url}; ValueError
  with a plain pt-BR message for a link that can never be a whole video (a channel page, a profile, a story).
- `uid(platform, info_id)` = "<platform>:<id>" (YouTube keeps its bare 11-char id: old items, files and own-video
  detection use it).
- `error_for(platform, stderr)` maps yt-dlp's message to a clear one (login required, private, not found…).
"""

import re

LABEL = {"youtube": "YouTube", "twitch": "Twitch", "kick": "Kick", "tiktok": "TikTok", "instagram": "Instagram"}
EXPERIMENTAL = {"kick"}

_YT = re.compile(r"^https?://(?:www\.|m\.|music\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)"
                 r"([\w-]{11})")
_TWITCH_VOD = re.compile(r"^https?://(?:www\.|m\.)?twitch\.tv/(?:videos/|[\w]+/v(?:ideo)?/)(\d+)")
_TWITCH_CLIP = re.compile(r"^https?://(?:clips\.twitch\.tv/(?:embed\?clip=)?|(?:www\.|m\.)?twitch\.tv/[\w]+/clip/)"
                          r"([\w-]+)")
_TWITCH_ANY = re.compile(r"^https?://(?:[\w-]+\.)?twitch\.tv(?:/|$)")
_KICK_VOD = re.compile(r"^https?://(?:www\.)?kick\.com/(?:[\w-]+/videos/|video/)([0-9a-f-]{36})")
_KICK_CLIP = re.compile(r"^https?://(?:www\.)?kick\.com/[\w-]+(?:/clips/|\?(?:.*&)?clip=)(clip_[\w]+)")
_KICK_ANY = re.compile(r"^https?://(?:www\.)?kick\.com(?:/|$)")
_TIKTOK = re.compile(r"^https?://(?:www\.|m\.)?tiktok\.com/@([\w.-]+)/video/(\d+)")
_TIKTOK_SHORT = re.compile(r"^https?://(?:(?:vm|vt)\.tiktok\.com/|(?:www\.|m\.)?tiktok\.com/t/)([\w-]+)")
_TIKTOK_PHOTO = re.compile(r"^https?://(?:www\.|m\.)?tiktok\.com/@[\w.-]+/photo/\d+")
_TIKTOK_ANY = re.compile(r"^https?://(?:[\w-]+\.)?tiktok\.com(?:/|$)")
_INSTA = re.compile(r"^https?://(?:www\.|m\.)?instagram\.com/(?:[\w.]+/)?(p|reels?|tv)/([\w-]+)")
_INSTA_STORY = re.compile(r"^https?://(?:www\.|m\.)?instagram\.com/stories/")
_INSTA_ANY = re.compile(r"^https?://(?:www\.|m\.)?instagram\.com(?:/|$)")

ACCEPTED = "YouTube, Twitch (vídeos e clipes), Kick, TikTok ou Instagram (posts públicos)"


def detect(url: str) -> dict:
    """{platform, id, url, original_url} of a pasted link; ValueError (pt-BR) when it can't be one whole video."""
    original = (url or "").strip()
    u = original
    if m := _YT.match(u):
        return _out("youtube", m.group(1), f"https://www.youtube.com/watch?v={m.group(1)}", original)
    if m := _TWITCH_VOD.match(u):
        return _out("twitch", f"v{m.group(1)}", f"https://www.twitch.tv/videos/{m.group(1)}", original)
    if m := _TWITCH_CLIP.match(u):
        return _out("twitch", m.group(1), f"https://clips.twitch.tv/{m.group(1)}", original)
    if _TWITCH_ANY.match(u):
        raise ValueError("Esse link é de um canal da Twitch (ao vivo), não de um vídeo. Cole o link de um vídeo "
                         "salvo (twitch.tv/videos/…) ou de um clipe.")
    if m := _KICK_VOD.match(u):
        return _out("kick", m.group(1), u.split("?")[0], original)
    if m := _KICK_CLIP.match(u):
        return _out("kick", m.group(1), u, original)
    if _KICK_ANY.match(u):
        raise ValueError("Esse link é de um canal do Kick, não de um vídeo. Cole o link de um vídeo salvo "
                         "(kick.com/…/videos/…) ou de um clipe.")
    if m := _TIKTOK.match(u):
        return _out("tiktok", m.group(2), f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}", original)
    if _TIKTOK_SHORT.match(u):
        return _out("tiktok", None, u.split("?")[0], original)  # the id comes from yt-dlp (redirect)
    if _TIKTOK_PHOTO.match(u):
        raise ValueError("Esse post do TikTok é de fotos, não de vídeo.")
    if _TIKTOK_ANY.match(u):
        raise ValueError("Esse link é de um perfil do TikTok. Cole o link de um vídeo (tiktok.com/@…/video/…).")
    if m := _INSTA.match(u):
        kind = "p" if m.group(1) == "p" else "tv" if m.group(1) == "tv" else "reel"
        return _out("instagram", m.group(2), f"https://www.instagram.com/{kind}/{m.group(2)}/", original)
    if _INSTA_STORY.match(u):
        raise ValueError("Stories do Instagram pedem login. Só dá para usar posts e reels públicos.")
    if _INSTA_ANY.match(u):
        raise ValueError("Esse link é de um perfil do Instagram. Cole o link de um post ou reel público "
                         "(instagram.com/p/… ou /reel/…).")
    raise ValueError(f"Link não reconhecido: {original[:80] or '(vazio)'}. Aceito links de {ACCEPTED}.")


def _out(platform: str, vid: str | None, url: str, original: str) -> dict:
    return {"platform": platform, "id": vid, "url": url, "original_url": original}


_TWITCH_CHANNEL = re.compile(r"^https?://(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{2,25})/?(?:[?#].*)?$")
_KICK_CHANNEL = re.compile(r"^https?://(?:www\.)?kick\.com/([\w-]{2,40})/?(?:[?#].*)?$")
_NOT_CHANNELS = {"videos", "directory", "settings", "p", "downloads", "jobs", "turbo", "search", "browse",
                 "categories", "following", "subscriptions", "inventory", "wallet", "drops", "login", "signup"}


def live_channel(url: str) -> dict | None:
    """A Twitch or Kick CHANNEL page (where its live plays) → {platform, channel, url, archives}; None otherwise.
    Used only by "Produzir quando acabar" (cvm/lives/after_live.py): once the live ends, its saved video is the
    newest one in `archives`."""
    for platform, rx in (("twitch", _TWITCH_CHANNEL), ("kick", _KICK_CHANNEL)):
        if (m := rx.match((url or "").strip())) and m.group(1).lower() not in _NOT_CHANNELS:
            ch = m.group(1)
            if platform == "twitch":
                return {"platform": platform, "channel": ch, "url": f"https://www.twitch.tv/{ch}",
                        "archives": f"https://www.twitch.tv/{ch}/videos?filter=archives&sort=time"}
            return {"platform": platform, "channel": ch, "url": f"https://kick.com/{ch}",
                    "archives": f"https://kick.com/{ch}/videos"}
    return None


def platform_of(url: str) -> str | None:
    try:
        return detect(url)["platform"]
    except ValueError:
        return None


def uid(platform: str, info_id: str) -> str:
    """The key of a video everywhere (files, lives items): "<platform>:<id>"; YouTube keeps the bare id."""
    return str(info_id) if platform == "youtube" else f"{platform}:{info_id}"


def file_key(video_id: str) -> str:
    """A uid safe for file names on every OS ("twitch:v123" → "twitch_v123")."""
    return re.sub(r"[^\w-]", "_", video_id)


# yt-dlp messages → what the user reads. Order matters: "private" before the generic "unavailable".
_ERRORS = [
    (re.compile(r"private", re.I), "o vídeo é privado. Só dá para usar vídeos públicos"),
    (re.compile(r"subscriber|sub-only|subs only", re.I), "o vídeo é só para inscritos do canal"),
    (re.compile(r"age.?restrict|confirm your age|\binappropriate", re.I), "o vídeo tem restrição de idade e pede login"),
    (re.compile(r"login|log in|sign in|logged|cookies|authenticat|rate-limit reached", re.I),
     "o site pede login para abrir este vídeo. Só dá para usar posts públicos (abra o link numa aba anônima para "
     "conferir)"),
    (re.compile(r"not available in your country|geo.?restrict|blocked in your", re.I),
     "o vídeo está bloqueado na sua região"),
    (re.compile(r"404|not found|does not exist|unavailable|removed|deleted|no longer|no video|unable to find",
                re.I), "não achei o vídeo (apagado, link errado ou fora do ar)"),
]


def error_message(platform: str, stderr: str) -> str | None:
    """A clear pt-BR message for a known yt-dlp failure, or None."""
    label = LABEL.get(platform, platform or "site")
    for rx, msg in _ERRORS:
        if rx.search(stderr or ""):
            out = f"{label}: {msg}."
            if platform in EXPERIMENTAL:
                out += " (O Kick ainda é experimental: o site muda com frequência.)"
            return out
    return None
