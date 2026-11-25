"""Read and download a pasted video/live (the user's own or another creator's) with yt-dlp, at most 1080p.

YouTube, Twitch (VODs/clips), Kick (VODs/clips, experimental), TikTok and Instagram (public posts): see
cvm/lives/platforms.py. A live still on air (any site) is NotReady; the site's errors become plain pt-BR messages.
"""

import json
import subprocess
from pathlib import Path

from cvm.lives import platforms
from cvm.visuals.sources import YTDLP

NOT_READY = {"is_live": "a live ainda está no ar", "is_upcoming": "a live ainda não começou",
             "post_live": "o YouTube ainda está processando a gravação da live"}


class NotReady(Exception):
    """The video can't be downloaded whole yet (live on air or still processing); send the link again later."""


BOT_CHECK = ("confirm you’re not a bot", "confirm you're not a bot", "HTTP Error 429")


def _raise_for(stderr: str, what: str, platform: str = "youtube") -> None:
    """YouTube's anti-bot check (too many reads in a short time) clears by itself after a while: NotReady, so the item
    waits and is retried later instead of failing. Known site errors (login, private, not found) read plainly."""
    if any(b in stderr for b in BOT_CHECK):
        raise NotReady("o YouTube pediu uma pausa nos downloads (anti-robô); tento de novo sozinho mais tarde")
    if msg := platforms.error_message(platform, stderr):
        raise RuntimeError(msg)
    raise RuntimeError(f"{what}: {stderr[-400:]}")


def info_from(e: dict, url: str = "") -> dict:
    """yt-dlp's info dict → {video_id, platform, title, url, original_url, duration, channel_id, uploader, language}.
    video_id = "<platform>:<id>" (YouTube: the bare id); url = the canonical page; original_url = what was pasted.
    A live still on air (or not started / still processing) is NotReady."""
    if e.get("is_live") and e.get("live_status") not in NOT_READY:
        e = {**e, "live_status": "is_live"}
    if e.get("live_status") in NOT_READY:
        raise NotReady(NOT_READY[e["live_status"]] + "; envie o link de novo depois")
    known = platforms.platform_of(url) if url else None
    key = (e.get("extractor_key") or e.get("extractor") or "").lower()
    platform = known or next((p for p in platforms.LABEL if key.startswith(p)), "youtube")
    vid = str(e["id"])
    canonical = (f"https://www.youtube.com/watch?v={vid}" if platform == "youtube"
                 else e.get("webpage_url") or (platforms.detect(url)["url"] if known else url))
    title = (e.get("title") or e.get("description") or vid).strip().splitlines()[0][:150]
    return {"video_id": platforms.uid(platform, vid), "platform": platform, "title": title, "url": canonical,
            "original_url": url or canonical, "duration": float(e.get("duration") or 0),
            "channel_id": e.get("channel_id") or (e.get("uploader_id") if platform != "youtube" else None),
            "uploader": e.get("uploader") or e.get("channel") or e.get("uploader_id"), "language": e.get("language")}


def probe(url: str, run=subprocess.run) -> dict:
    """info_from() of a pasted link, refusing lives that are not finished."""
    platform = platforms.platform_of(url) or "youtube"
    r = run([str(YTDLP), "--dump-json", "--skip-download", "--no-playlist", url], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        _raise_for(r.stderr or "", f"não consegui ler o vídeo ({platforms.LABEL.get(platform, platform)})", platform)
    return info_from(json.loads(r.stdout or "{}"), url)


def download(live: dict, dest_dir: Path, run=subprocess.run) -> Path:
    """dest_dir/live_<id>.mp4; an existing file is reused (a 3 h live is several GB)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    key = platforms.file_key(live["video_id"])
    out = dest_dir / f"live_{key}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    r = run([str(YTDLP), "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4",
             "--no-playlist", "-o", str(dest_dir / f"live_{key}.%(ext)s"), live["url"]],
            capture_output=True, text=True, timeout=6 * 3600)
    if r.returncode != 0 or not out.exists():
        _raise_for(r.stderr or r.stdout or "", "o download falhou", live.get("platform") or "youtube")
    return out
