"""Crescer → "Em alta" (user, 2026-11-23: "buscar o que está em alta em relação a lives do tema, e vídeos, para me dar
opção de criar vídeo/cortes").

`discover(topic, region)` searches YouTube through yt-dlp (free, no key) with the site's own filters:
- live now: `sp=EgJAAQ==` → streams on air, ranked by people watching (`concurrent_view_count`);
- this week / today by views: past lives (`live_status == "was_live"`) and videos, ranked by views.
`region` ("BR" / "US" / "" = world) goes as `gl`/`hl`, so a Brazilian channel sees what Brazil is watching.

`make(...)` turns a result into work: "short" = an editorial Short written for Shorts (no avatar, the clip whole in
the middle, comment over it, 🟡 + credit for someone else's video, source sound under the voice), "video" = a long
comment video, "cortes" = the existing lives flow (best-moments video + one Short per strong moment).
A stream still on air cannot be downloaded yet: the UI offers the actions once it has ended.
"""

import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote_plus

from cvm import private
from cvm.config import settings
from cvm.lives import platforms

log = logging.getLogger(__name__)

SP = {"live": "EgJAAQ%253D%253D", "week": "CAMSBAgDEAE%253D", "today": "CAMSBAgCEAE%253D"}
LANG = {"BR": "pt-BR", "US": "en-US", "AE": "ar"}
KEEP = 48  # per group; Em alta shows 12 and opens more with "Ver mais"


def _yt_dlp() -> list[str]:
    import shutil
    import sys

    exe = shutil.which("yt-dlp") or str(Path(sys.executable).with_name("yt-dlp"))
    return [exe, "--no-warnings", "--socket-timeout", "20"]


def _search(topic: str, sp: str, region: str, n: int = 60) -> list[dict]:
    url = f"https://www.youtube.com/results?search_query={quote_plus(topic)}&sp={sp}"
    if region:
        url += f"&gl={region}&hl={LANG.get(region, 'en')}"
    return _search_url(url, n)


def _search_url(url: str, n: int = 25) -> list[dict]:
    """Flat entries of any YouTube list page (search results, a channel's /videos or /streams tab)."""
    r = subprocess.run([*_yt_dlp(), "--flat-playlist", "--playlist-end", str(n), "--dump-single-json", url],
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError((r.stderr or "yt-dlp falhou")[-300:])
    return (json.loads(r.stdout or "{}").get("entries") or [])


def _item(e: dict) -> dict | None:
    vid = e.get("id")
    if not vid or len(vid) != 11:  # channels and playlists also come back from a search
        return None
    status = e.get("live_status") or ""
    dur = e.get("duration")
    kind = "live" if status == "is_live" else "was_live" if status == "was_live" else \
        "short" if dur and dur <= 70 else "video"
    return {"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": e.get("title") or "",
            "channel": e.get("channel") or e.get("uploader") or "", "channel_url": e.get("channel_url"),
            "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", "views": e.get("view_count"),
            "viewers": e.get("concurrent_view_count"), "duration": dur, "kind": kind}


def discover(topic: str, region: str = "BR", live_topic: str | None = None) -> dict:
    """{live, lives, videos}: what is on air now, the most watched past lives and videos of the week/today.
    `live_topic` searches the lives on air with other words (the global search without a topic)."""
    seen: set[str] = set()
    live, past, videos = [], [], []
    for e in _search(live_topic or topic, SP["live"], region):
        it = _item(e)
        if it and it["kind"] == "live" and it["id"] not in seen:
            seen.add(it["id"]); live.append(it)
    for sp in (SP["week"], SP["today"]):
        try:
            entries = _search(topic, sp, region)
        except Exception as e:  # noqa: BLE001 — one filter failing still leaves the others
            log.warning("discover %s: %s", sp, e)
            continue
        for e in entries:
            it = _item(e)
            if not it or it["id"] in seen:
                continue
            seen.add(it["id"])
            (live if it["kind"] == "live" else past if it["kind"] == "was_live" else videos).append(it)
    by_viewers = lambda x: -(x.get("viewers") or 0)  # noqa: E731
    by_views = lambda x: -(x.get("views") or 0)  # noqa: E731
    return {"topic": topic, "region": region, "at": datetime.now(UTC).isoformat(),
            "live": sorted(live, key=by_viewers)[:KEEP], "lives": sorted(past, key=by_views)[:KEEP],
            "videos": sorted(videos, key=by_views)[:KEEP]}


# Em alta without a topic (cvm/api/em_alta.py): broad words per region — what is on air now / most watched this week
BROAD = {"BR": ("ao vivo", "brasil"), "AE": ("بث مباشر", "الإمارات"), "US": ("live", "world"), "": ("live", "world")}


def discover_global(topic: str = "", region: str = "") -> dict:
    """Em alta → Lives / Vídeos, no channel and no topic needed: YouTube (live now, past lives, videos of the week)
    plus the most watched Twitch (official Helix API) and Kick (experimental) streams. Each source fails on its own."""
    from cvm.trends import kick, twitch

    topic = (topic or "").strip()
    region = region if region in BROAD else ""
    try:
        if topic:
            yt = discover(topic, region)
        else:
            live_q, video_q = BROAD[region]
            yt = discover(video_q, region, live_topic=live_q)
        yt_error = None
    except Exception as e:  # noqa: BLE001 — Twitch/Kick still come back
        yt, yt_error = {"live": [], "lives": [], "videos": []}, f"O YouTube não respondeu agora: {str(e)[:200]}"
    for group in ("live", "lives", "videos"):
        for it in yt.get(group, []):
            it.setdefault("platform", "youtube")
    return {"topic": topic, "region": region, "at": datetime.now(UTC).isoformat(), "live": yt.get("live", []),
            "lives": yt.get("lives", []), "videos": yt.get("videos", []), "youtube_error": yt_error,
            "twitch": twitch.safe_top(topic, region, n=40), "kick": kick.safe_top(topic, region, n=40)}


def download(url: str, dest_dir: Path) -> Path:
    """≤1080p mp4 of a video; tries other YouTube clients when one is refused (403 / 'page needs to be reloaded')."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    vid = url.rsplit("v=", 1)[-1].split("&")[0] if "v=" in url else url.rstrip("/").rsplit("/", 1)[-1]
    out = dest_dir / f"yt_{vid}.mp4"
    if out.exists():
        return out
    from cvm.lives.progress import ytdlp_run  # "Baixando N%" for the cut editor's prep steps

    last = ""
    for client in ("default", "mweb", "web_safari", "ios"):
        extra = [] if client == "default" else ["--extractor-args", f"youtube:player_client={client}"]
        r = ytdlp_run([*_yt_dlp(), *extra, "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4",
                       "-o", str(dest_dir / f"yt_{vid}.%(ext)s"), url], timeout=3600)
        if out.exists():
            return out
        if login := platforms.login_args(r.stderr or ""):  # age-restricted / login wall: once with the user's login
            ytdlp_run([*_yt_dlp(), *login, *extra, "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
                       "--merge-output-format", "mp4", "-o", str(dest_dir / f"yt_{vid}.%(ext)s"), url], timeout=3600)
            if out.exists():
                return out
        last = (r.stderr or "")[-300:]
    raise RuntimeError(f"não consegui baixar o vídeo: {last}")


EDITORIAL = ("FORMATO: Short EDITORIAL: sem avatar; comentário narrado por cima do material; "
             "packaging.header {kicker, headline com UMA palavra entre *asteriscos*}; on_screen_text vazio; gancho mostrando a "
             "ação no 1º segundo; sem pedido de inscrição; a última frase é completa e emenda na primeira (loop). 35–50 s. ")
THIRD = ("Material de OUTRO criador: título começa com 🟡; crédito na tela; só trechos curtos; o som original toca baixinho "
         "por baixo da voz; a narração COMENTA (o que acontece, por que importa, a opinião do criador do canal), nunca descreve. ")
VIDEO_ANGLE = "Melhores momentos, contexto e a opinião do criador do canal, em blocos."


def prompts() -> dict:
    """The topic lines above, with the owner's own wording from private/prompts/discover.json when present."""
    own = private.json("prompts/discover.json", {}) or {}
    return {"editorial": own.get("editorial") or EDITORIAL, "third": own.get("third") or THIRD,
            "video_angle": own.get("video_angle") or VIDEO_ANGLE}


def attach_source(session, project_id: int, path: Path, title: str, credit: str, url: str, highlights: bool = False):
    """Someone else's video as project material, processed like an upload (kind, duration, preview — a raw Asset row
    left the scriptwriter blind: 685–688 failed with "media has no file") and, for a long video, its best moments
    worked out BEFORE the script starts. Its sound plays low under the presenter's comment (audio_under)."""
    from cvm.api.media import attach_file

    a = attach_file(session, project_id, path, path.name, f"vídeo de {credit}: “{title}”", keep_audio=False)
    a.source, a.license = f"{credit} ({url})", "terceiros (comentário)"
    a.meta = {**(a.meta or {}), "credit": credit, "audio_under": True}
    session.add(a)
    session.commit()
    if highlights:
        from cvm.visuals.highlights import refresh_project

        refresh_project(project_id, True)
    return a


def make(session, channel, item: dict, kind: str):
    """Download `item` (a discover result) and open the project for it; returns the project (or the lives run)."""
    from cvm.lives import runs
    from cvm.suggest import new_project

    if kind == "cortes":  # the existing flow: best-moments video + one Short per strong moment
        return runs.process_link(session, channel, item["url"], item.get("title"), claimed=False)
    path = download(item["url"], settings.data_dir / "lives" / str(channel.id))
    credit = item.get("channel") or "YouTube"
    long_source = (item.get("duration") or 0) > 180

    def attach(p):
        attach_source(session, p.id, path, item.get("title") or path.name, f"{credit} (YouTube)", item["url"],
                      highlights=long_source)

    base = f"{item.get('title')}" + f" — {credit}"
    lines = prompts()
    if kind == "short":
        data = {"title": f"🟡 Short: {item.get('title')}"[:200], "style": "react", "platforms": ["youtube_shorts"],
                "presenter": "editorial", "media_cut": "highlights" if long_source else "full", "no_avatar": True,
                "topic": lines["editorial"] + lines["third"] + f"Vídeo: {base} ({item['url']}). Escolha o momento mais forte e comente."}
    else:  # "video": a long comment video on the material
        data = {"title": f"🟡 {item.get('title')}"[:200], "style": "react", "platforms": ["youtube"],
                "media_cut": "highlights" if long_source else "full",
                "topic": lines["third"] + f"Vídeo de comentário (3 a 8 min) sobre: {base} ({item['url']}). "
                                          + lines["video_angle"]}
    return new_project(session, {**data, "channel_id": channel.id, "languages": [channel.language], "media_mode": "mine",
                                 "hook": True, "force_similar": True}, before_job=attach)
