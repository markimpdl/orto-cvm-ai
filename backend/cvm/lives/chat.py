"""A live's chat replay as a signal for its best moments (2026-11-22, IShowSpeed marathons).

fetch → yt-dlp's `live_chat` subtitle (JSON lines, one replay action per line, slow: ~4 min per hour of live);
density → weighted messages per second (superchats, gifts and new members weigh more); boost → each analysed moment
gets `chat_rate` and its score raised by how far the chat around it stands above the live's average, and the
strongest chat peaks the loudness/motion analysis missed become candidate moments too.
"""

import json
import logging
import subprocess
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
PAID_WEIGHT = 5  # a superchat / gift / new member says more than one text message
PAID = {"liveChatPaidMessageRenderer", "liveChatPaidStickerRenderer", "liveChatMembershipItemRenderer",
        "giftMessageViewModel", "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer"}
PEAK_WINDOW = 30.0  # length of a moment made from a chat peak (s)
PEAKS = 8  # chat peaks added as candidates at most
CHAT_WEIGHT = 1.0  # how much a standard deviation of chat above average adds to a moment's score


def fetch(url: str, dest_dir: Path, video_id: str, timeout: int = 4 * 3600) -> Path | None:
    """The chat replay of a past live as JSON lines, or None (not a live, no replay, blocked, too slow)."""
    from cvm.lives.ingest import YTDLP

    out = dest_dir / f"{video_id}.live_chat.json"
    if out.exists() and out.stat().st_size > 0:
        return out
    try:
        subprocess.run([str(YTDLP), "--no-update", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
                        "-o", str(dest_dir / video_id), url], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("chat replay of %s too slow, using what came", video_id)
        part = out.with_suffix(".json.part")
        if part.exists():
            part.replace(out)
    return out if out.exists() and out.stat().st_size > 0 else None


def density(path: Path) -> dict[int, float]:
    """{second: weighted messages} from a live_chat JSON-lines file; bad lines are skipped."""
    out: dict[int, float] = {}
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                r = json.loads(line).get("replayChatItemAction") or {}
                sec = int(int(r.get("videoOffsetTimeMsec", -1)) / 1000)
            except (ValueError, AttributeError):
                continue
            if sec < 0:
                continue
            for a in r.get("actions") or []:
                item = (a.get("addChatItemAction") or {}).get("item") or {}
                for kind in item:
                    out[sec] = out.get(sec, 0.0) + (PAID_WEIGHT if kind in PAID else 1.0)
    return out


def boost(moments: list[dict], dens: dict[int, float], duration: float) -> list[dict]:
    """Moments with `chat_rate` (weighted messages/min) and a score raised by the chat's z-score, plus up to PEAKS
    windows around the strongest chat peaks that no moment covers."""
    if not dens:
        return moments
    n = int(duration) + 1
    per_s = np.zeros(n)
    for s, w in dens.items():
        if 0 <= s < n:
            per_s[s] += w
    smooth = np.convolve(per_s, np.ones(int(PEAK_WINDOW)) / PEAK_WINDOW, mode="same")  # msgs/s over ~30 s
    mean, std = smooth.mean(), smooth.std() + 1e-6

    def rate(a: float, b: float) -> float:
        part = per_s[int(a):max(int(a) + 1, int(b))]
        return float(part.mean()) if len(part) else 0.0

    out = []
    for m in moments:
        r = rate(m["start"], m["end"])
        z = (r - mean) / std
        out.append({**m, "score": round(float(m.get("score") or 0) + CHAT_WEIGHT * z, 2), "chat_rate": round(r * 60, 1)})
    taken = [(m["start"], m["end"]) for m in out]
    for t in np.argsort(smooth)[::-1]:
        if len([m for m in out if m.get("chat_peak")]) >= PEAKS or smooth[t] <= mean + std:
            break
        a = max(0.0, float(t) - PEAK_WINDOW * 0.6)
        b = min(float(duration), a + PEAK_WINDOW)
        if any(a < y and b > x for x, y in taken):
            continue
        z = (rate(a, b) - mean) / std
        out.append({"start": round(a, 1), "end": round(b, 1), "peak": float(t), "score": round(CHAT_WEIGHT * z, 2),
                    "chat_rate": round(rate(a, b) * 60, 1), "chat_peak": True})
        taken.append((a, b))
    return out
