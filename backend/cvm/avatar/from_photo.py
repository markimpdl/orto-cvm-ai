"""Avatar from a single photo: frame it 16:9 (head, shoulders and room above), animate an idle loop (looking into
the lens, blinking, breathing) with the image-to-video engine (LTX-2.3, else Wan) and save it as the base video.
The voice still comes from the avatar's own recording."""

import logging
import subprocess
from pathlib import Path

from cvm.config import settings

log = logging.getLogger(__name__)

BASE = ("the person keeps looking straight into the camera, steady eye contact, never looks away or to the side, lips "
        "pressed together and the mouth stays closed the whole time, static locked-off tripod camera, ")
# one 5 s take per line; every take starts and ends on the same still, so they chain into one natural ~30 s base video
TAKES = [
    "calm serious face, blinks naturally twice, breathes gently, very subtle head movement",
    "gives two small slow nods as if listening, blinks once, then returns to the same pose",
    "tilts the head slightly to one side and back, eyebrows lift a little, blinks",
    "gestures calmly with both hands at chest height as if explaining something, hands stay below the chin, then rest again",
    "raises the eyebrows and shrugs the shoulders slightly, a small natural body sway, blinks",
    "leans in very slightly toward the camera with interest, blinks twice, then settles back",
]
# head-and-shoulders framing hides the hands, and LTX barely moves a real photo when asked for "subtle" motion:
# close-ups get clearly visible head, face and shoulder movement instead of hand gestures
CLOSE_TAKES = [
    "a lively engaged expression, blinks naturally, clearly nods twice while breathing, the shoulders rise and fall",
    "turns the head a little to one side and back to the camera, eyebrows raise clearly with interest, blinks",
    "a warm friendly look, the cheeks lift a little, tilts the head to the side and back, blinks twice",
    "nods slowly three times in agreement, the shoulders move with the nods, eyebrows move, blinks",
    "frowns slightly as if thinking, then the face relaxes, a small head shake, shoulders shift, blinks",
    "leans clearly toward the camera with interest, eyebrows lift, then settles back, the shoulders move, blinks twice",
]
# "free" takes (the user's pick for his own photo, test B of 2026-11-01): no "static"/"mouth closed" wording and no
# return to the still after every take — each 8 s take starts from the previous one's last frame, only the final take
# lands back on the still, so the base still loops. Much more alive on a real photo than the anchored takes.
FREE_LOOK = "head and shoulders, looking straight into the camera, plain studio background, static tripod camera, realistic, natural skin texture, "
FREE_TAKES = [
    "he listens attentively, blinks naturally, nods twice, raises his eyebrows, tilts his head slightly, breathes, the shoulders rise and fall, alive and natural",
    "he reacts with a warm natural smile, small nods, eyebrows move, blinks, breathes, the shoulders move, alive and natural",
    "he looks thoughtful, a small head tilt, the eyebrows lift, then a confident friendly look, blinks, breathes, alive and natural",
    "he listens calmly and nods in agreement, blinks, breathes, then settles into a relaxed friendly expression, alive and natural",
]
FREE_SECONDS = 8.0
IDLE = BASE + TAKES[0]  # kept for single-take callers


CROP = {"medium": 0.9, "close": 0.72}  # kept height of a tall photo, as a fraction of its width (head+chest / head+shoulders)


def frame_photo(photo: Path, out: Path, framing: str = "medium") -> Path:
    """1344x768 frame with the person centred and room above the head. A tall photo is cut to head and torso.
    The sides are filled with the photo's own background colour when it is plain (studio photos), else with a
    blurred copy, so there are no hard borders for the matting/lipsync to trip on."""
    import numpy as np
    from PIL import Image, ImageFilter

    W, H = 1344, 768
    img = Image.open(photo).convert("RGB")
    w, h = img.size
    keep = CROP.get(framing, CROP["medium"])
    if h > w * keep * 1.3:
        img = img.crop((0, 0, w, int(w * keep)))
        if framing == "close":  # also trim the sides so the head fills more of the frame
            side = int(w * 0.07)
            img = img.crop((side, 0, w - side, img.height))
        w, h = img.size
    fg = img.resize((round(w * H / h), H), Image.LANCZOS) if w / h < W / H else img.resize((W, round(h * W / w)), Image.LANCZOS)
    top = np.asarray(img)[: max(4, h // 20)].reshape(-1, 3).astype(float)  # the strip above the head
    if top.std(axis=0).mean() < 12:  # plain backdrop: extend it with its own colour
        bg = Image.new("RGB", (W, H), tuple(int(c) for c in np.median(top, axis=0)))
    else:
        bg = img.resize((W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    bg.paste(fg, ((W - fg.width) // 2, H - fg.height))
    bg.save(out)
    return out


def make_base(photo: Path, work: Path, framing: str = "medium", takes: list[str] | None = None) -> Path:
    """Photo → framed still → several 5 s takes (blink, nod, tilt, gestures…) → one ~30 s base at 1920x1080/25 fps.
    With LTX every take is anchored to the still at both ends, so they chain without cuts; with Wan (no end anchor)
    a single take plays back and forth."""
    from cvm.visuals.sources import animate_clip, ltx_ready

    work.mkdir(parents=True, exist_ok=True)
    still = frame_photo(photo, work / "framed.png", framing)
    takes = takes or (CLOSE_TAKES if framing == "close" else TAKES)
    if not ltx_ready():
        takes = takes[:1]
    clips = []
    for i, take in enumerate(takes):
        clip, engine = animate_clip(still, BASE + take, work / f"take_{i}.mp4", 5.0, portrait=False, loop=True)
        log.info("avatar take %d by %s", i, engine)
        clips.append(clip)
    base = work / "base_from_photo.mp4"
    n = len(clips)
    if engine.startswith("ltx"):
        graph = "".join(f"[{i}:v]scale=1920:1080:flags=lanczos,fps=25,setsar=1[v{i}];" for i in range(n)) + \
            "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1,format=yuv420p[v]"
    else:
        graph = "[0:v]scale=1920:1080:flags=lanczos,fps=25,split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1,format=yuv420p[v]"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", *[x for c in clips for x in ("-i", str(c))],
                    "-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-crf", "14", str(base)],
                   check=True, capture_output=True)
    return base


def make_base_free(photo: Path, work: Path, framing: str = "close", takes: list[str] | None = None,
                   look: str = FREE_LOOK) -> Path:
    """Photo → still → chained 8 s LTX takes at 1024x576 (FREE_TAKES) → one ~32 s base at 1920x1080/25 fps."""
    import os

    from cvm import gpu
    from cvm.visuals.sources import _ltx_model

    work.mkdir(parents=True, exist_ok=True)
    still = frame_photo(photo, work / "framed.png", framing)
    bin_ = settings.models_dir / "LTX2MLX" / ".venv" / "bin" / "ltx-2-mlx"
    takes = takes or FREE_TAKES
    anchor, parts = still, []
    for i, take in enumerate(takes):
        raw, part = work / f"free_{i}.ltx.mp4", work / f"free_{i}.mp4"
        if not part.exists():
            last = i == len(takes) - 1
            with gpu.section():
                subprocess.run([str(bin_), "generate", "--distilled", "--model", str(_ltx_model()), "--image", str(anchor), "0", "1.0",
                                *(["--image", str(still), "last", "1.0", "guide"] if last else []),
                                "-p", look + take, "-W", "1024", "-H", "576", "-f", str(int(FREE_SECONDS * 24) + 1),
                                "--frame-rate", "24", "--seed", str(7 + i), "-o", str(raw)],
                               check=True, capture_output=True, text=True, timeout=3600, env={**os.environ, "HF_HUB_OFFLINE": "1"})
            subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-i", str(raw), "-an", "-vf",
                            "scale=1920:1080:flags=lanczos,fps=25,setsar=1", "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p",
                            str(part)], check=True, capture_output=True)
            raw.unlink(missing_ok=True)
        log.info("avatar free take %d done", i)
        nxt = work / f"free_anchor_{i}.png"  # the next take continues from this one's last frame
        subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-sseof", "-0.1", "-i", str(part), "-frames:v", "1", "-update", "1",
                        str(nxt)], check=True, capture_output=True)
        anchor = nxt
        parts.append(part)
    base = work / "base_from_photo.mp4"
    listing = work / "free_parts.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(base)],
                   check=True, capture_output=True)
    return base
