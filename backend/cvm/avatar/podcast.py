"""Podcast desk version of an avatar: the same person (cut out of his framed still) placed in a podcast studio
plate, behind the desk, then animated like any base video (anchored LTX takes). Saved in data/avatars/<id>/podcast
and used by the "podcast" presenter mode (cvm/render/compose.py)."""

import logging
import shutil
import subprocess
from pathlib import Path

from sqlmodel import Session

from cvm.config import settings
from cvm.models import Avatar
from cvm.paths import avatar_dir

log = logging.getLogger(__name__)

TAKES = [
    "sits relaxed at the podcast desk, calm friendly face, blinks naturally, breathes, a very subtle head movement",
    "listens like a podcast host, gives two small slow nods, blinks, then returns to the same pose",
    "tilts the head slightly as if thinking about a question, eyebrows lift a little, blinks",
    "a warm small smile as if hearing something funny, shoulders move a little, blinks twice",
    "leans very slightly toward the microphone with interest, blinks, then settles back",
    "raises the eyebrows and shrugs slightly, a small natural body sway, blinks",
]

# a real photo barely moves with anchored takes: free chained takes, like the person's own base (from_photo.FREE_TAKES)
FREE_LOOK = ("a podcast host sitting behind the desk in his podcast studio, looking into the camera, static tripod camera, "
             "realistic, natural skin texture, the room and the microphone stay still, ")
FREE_TAKES = [
    "listens attentively, blinks naturally, nods twice, raises the eyebrows, tilts the head slightly, breathes, the shoulders rise and fall, alive and natural",
    "reacts with a warm natural smile, small nods, eyebrows move, blinks, breathes, the shoulders move, alive and natural",
    "looks thoughtful, a small head tilt, the eyebrows lift, then a confident friendly look, blinks, breathes, alive and natural",
    "listens calmly and nods in agreement, blinks, breathes, then settles into a relaxed friendly expression, alive and natural",
]


def cut_out(still: Path, out: Path) -> Path:
    """RGBA cut-out of a framed still (RobustVideoMatting on a 1 s clip of it)."""
    from cvm.avatar.matting import matte

    work = out.parent
    clip, mov = work / "still_clip.mp4", work / "still_alpha.mov"
    ff = settings.ffmpeg_bin
    subprocess.run([ff, "-v", "error", "-y", "-loop", "1", "-i", str(still), "-t", "1", "-r", "25", "-pix_fmt", "yuv420p", str(clip)], check=True)
    matte(clip, mov)
    subprocess.run([ff, "-v", "error", "-y", "-ss", "0.8", "-i", str(mov), "-frames:v", "1", "-pix_fmt", "rgba", str(out)], check=True)
    return out


def composite(person: Path, plate: Path, desk_y: float, out: Path, scale: float = 0.92, x: float = 0.5,
              y: float = 0.06) -> Path:
    """Person (RGBA, 1344x768 framing) in the studio plate. The plate's desk band (below desk_y, a fraction of the
    height) is laid back over the person so he sits behind the desk; a soft shadow ties him to the room."""
    from PIL import Image, ImageFilter

    bg = Image.open(plate).convert("RGBA").resize((1344, 768), Image.LANCZOS)
    p = Image.open(person).convert("RGBA")
    p = p.resize((round(p.width * scale), round(p.height * scale)), Image.LANCZOS)
    px, py = round(1344 * x - p.width / 2), 768 - p.height + round(768 * y)
    shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 110), (px + 14, py + 10), p.getchannel("A"))
    bg.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))
    bg.alpha_composite(p, (px, py))
    desk = Image.open(plate).convert("RGBA").resize((1344, 768), Image.LANCZOS)
    top = round(768 * desk_y)
    band = desk.crop((0, top, 1344, 768))
    fade = Image.new("L", band.size, 255)  # 6 px feather on the desk edge
    for i in range(6):
        fade.paste(int(255 * (i + 1) / 7), (0, i, band.width, i + 1))
    bg.paste(band, (0, top), fade)
    bg.convert("RGB").save(out)
    return out


def build(session: Session, avatar_id: int, still: Path, free: bool = False) -> Path:
    """Animate the podcast still into data/avatars/<id>/podcast/base.mp4 and attach it to the avatar.
    free=True: chained free takes (real photos), else anchored takes (AI portraits)."""
    from cvm.avatar.from_photo import make_base, make_base_free

    work = avatar_dir(avatar_id) / "podcast"
    work.mkdir(parents=True, exist_ok=True)
    if still.resolve() != (work / "still.png").resolve():
        shutil.copy(still, work / "still.png")
    base = (make_base_free(work / "still.png", work / "takes", "medium", FREE_TAKES, FREE_LOOK) if free
            else make_base(work / "still.png", work / "takes", "medium", TAKES))
    shutil.copy(base, work / "base.mp4")
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-ss", "1", "-i", str(work / "base.mp4"), "-frames:v", "1",
                    str(work / "still.jpg")], check=True)
    avatar = session.get(Avatar, avatar_id)
    avatar.podcast_video_dir = str(work)
    session.add(avatar)
    session.commit()
    log.info("avatar %s podcast base ready", avatar_id)
    return work / "base.mp4"
