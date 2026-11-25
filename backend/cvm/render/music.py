"""Background music: pick a track, start it where it has energy, fade it in and out.

Order: the channel's folder (data/music/<channel_id>/), the shared folder (data/music/), then the source chosen in
`settings.music_source`: "acestep" = own tracks generated locally (cvm/render/music_gen.py, falls back to Openverse
when the model isn't installed), "openverse" = a free search on Openverse audio (Jamendo, ccMixter, Freesound…) limited to licences that allow commercial use and modification
(CC0, CC BY, CC BY-SA; never NC or ND). Downloaded tracks are kept in data/music/openverse/<style>/ with a
.json sidecar holding the credit, so each style builds a small library and later videos don't hit the network.
"""

import json
import logging
import random
import subprocess
from pathlib import Path

import httpx
import numpy as np

from cvm.config import settings
from cvm.models import Project

log = logging.getLogger(__name__)

AUDIO_EXT = (".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac")
STYLE_QUERIES = {
    "dark": ["dark ambient", "suspense cinematic", "mystery ambient"],
    "react": ["lofi", "chill beat", "hip hop instrumental"],
    "explainer": ["corporate upbeat", "inspiring background", "light electronic"],
    "review": ["upbeat tech background", "modern electronic upbeat", "positive corporate pop"],
    "lesson": ["calm corporate background", "soft piano instrumental", "light ambient instrumental"],
    "top_list": ["energetic electronic", "upbeat pop instrumental", "driving rock instrumental"],
}
LIBRARY_TARGET = 4  # tracks per style before we stop searching
MIN_TRACK_S = 60
from cvm.visuals.sources import UA  # noqa: E402 — one agent string for every external request


def _local(d: Path) -> list[Path]:
    return sorted(p for p in d.glob("*") if p.suffix.lower() in AUDIO_EXT) if d.exists() else []


def _with_audio_ext(path: Path) -> Path:
    """Give a downloaded track the extension its bytes say (Jamendo links end in "?trackid=…&format=mp32", so the
    file came without one and was thrown away as "not audio": no video had music until 2026-11-15)."""
    if path.suffix.lower() in AUDIO_EXT:
        return path
    head = path.read_bytes()[:12]
    ext = (".mp3" if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") else
           ".ogg" if head[:4] == b"OggS" else ".flac" if head[:4] == b"fLaC" else
           ".wav" if head[:4] == b"RIFF" else ".m4a" if head[4:8] == b"ftyp" else None)
    if not ext:
        return path
    new = path.with_suffix(ext)
    path.replace(new)
    return new


def credit_for(track: Path) -> str | None:
    side = track.with_suffix(".json")
    if not side.exists():
        return None
    m = json.loads(side.read_text())
    if m.get("source") in ("acestep", "elevenlabs"):  # our own generated track: nothing to credit
        return None
    return f"Música: “{m['title']}” — {m['creator']} ({m['license']})"


def openverse_tracks(query: str, client: httpx.Client, n: int = 10) -> list[dict]:
    r = client.get("https://api.openverse.org/v1/audio/", headers=UA,
                   params={"q": query, "page_size": n, "license_type": "commercial,modification", "category": "music", "mature": "false"})
    r.raise_for_status()
    out = []
    for x in r.json().get("results", []):
        lic = x.get("license", "")
        if lic not in ("cc0", "pdm", "by", "by-sa"):
            continue
        if (x.get("duration") or 0) / 1000 < MIN_TRACK_S or not x.get("url"):
            continue
        out.append({"url": x["url"], "title": (x.get("title") or "")[:100], "creator": (x.get("creator") or "")[:100],
                    "license": lic if lic in ("cc0", "pdm") else f"CC {lic.upper()} {x.get('license_version', '')}".strip(),
                    "page": x.get("foreign_landing_url"), "source": x.get("source")})
    return out


def _grow_library(style: str, folder: Path, seed: int) -> None:
    from cvm.visuals.sources import download

    have = {json.loads(p.read_text()).get("url") for p in folder.glob("*.json")}
    queries = STYLE_QUERIES.get(style, STYLE_QUERIES["explainer"])[:]
    random.Random(seed).shuffle(queries)
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        for q in queries:
            for t in openverse_tracks(q, client):
                if t["url"] in have:
                    continue
                path = _with_audio_ext(download(t["url"], folder))
                if path.suffix.lower() not in AUDIO_EXT:
                    path.unlink(missing_ok=True)
                    continue
                path.with_suffix(".json").write_text(json.dumps(t, ensure_ascii=False))
                return


def pick_music(project: Project, channel_prefs: dict | None = None, session=None) -> Path | None:
    """A source chosen for this video (Project.music_source, /fabrica/novo) wins: "none" = no music at all,
    "elevenlabs" = its own ElevenLabs track (cvm/render/music_eleven.py; ACE-Step when credits are low or the call
    fails), "acestep" = the local library. Otherwise the user's own folders always count, and generated/searched
    music only plays on channels with prefs.music on (prefs.music_source, else settings.music_source)."""
    prefs = channel_prefs or {}
    chosen = getattr(project, "music_source", None)
    if chosen == "none":
        return None
    if not chosen:
        dirs = ([settings.music_dir / str(project.channel_id)] if project.channel_id else []) + [settings.music_dir]
        for d in dirs:
            if files := _local(d):
                return random.Random(project.id).choice(files)
    source = chosen or ((prefs.get("music_source") or settings.music_source)
                        if settings.auto_music and prefs.get("music") else "none")
    if source == "none":
        return None
    if source == "elevenlabs":
        from cvm.render import music_eleven

        if track := music_eleven.track(project, session):
            return track
        source = "acestep"  # low credits / no key / failed call: the local model, so the queue never stops
    style = getattr(project.style, "value", str(project.style))
    if source == "acestep":
        from cvm.render import music_gen

        if music_gen.ready():
            folder = music_gen.library(style)
            try:
                music_gen.grow(style, seed=project.id or 1)
            except Exception as e:  # noqa: BLE001 — no music is better than a failed render
                log.warning("acestep music failed: %s", e)
            if files := _local(folder):
                return random.Random(project.id).choice(files)
    folder = settings.music_dir / "openverse" / style
    folder.mkdir(parents=True, exist_ok=True)
    if len(_local(folder)) < LIBRARY_TARGET:
        try:
            _grow_library(style, folder, project.id)
        except Exception as e:  # noqa: BLE001 — no music is better than a failed render
            log.warning("music search failed: %s", e)
    files = _local(folder)
    return random.Random(project.id).choice(files) if files else None


def energetic_start(track: Path, need_s: float) -> float:
    """Skip a slow intro: start where the 8 s loudness first reaches 70% of the track's loudest stretch,
    leaving room for need_s of music when possible."""
    pcm = subprocess.run([settings.ffmpeg_bin, "-v", "error", "-i", str(track), "-ac", "1", "-ar", "4000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    a = np.frombuffer(pcm, dtype=np.int16).astype("float32") / 32768
    sr, win = 4000, 4000  # 1 s windows
    n = len(a) // win
    if n < 16:
        return 0.0
    rms = np.sqrt(np.mean(a[: n * win].reshape(n, win) ** 2, axis=1))
    smooth = np.convolve(rms, np.ones(8) / 8, mode="valid")
    latest = max(0, int(n - need_s)) if n > need_s else 0
    hits = np.nonzero(smooth >= 0.7 * smooth.max())[0]
    start = int(hits[0]) if len(hits) else 0
    return float(min(start, latest, 45))
