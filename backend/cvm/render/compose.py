"""Compose the final video for one language × platform profile with FFmpeg.

scene clips (B-roll / Ken Burns image / headline / text card / avatar full / avatar PIP)
  → concat → narration (+ music ducked under the voice, loudness -14 LUFS)
  → burned captions (word highlight) + on-screen titles + source credits → platform encode.
"""

import contextlib
import logging
import shutil
import sys
from pathlib import Path

import yaml
from sqlmodel import Session, select

from cvm import progress
from cvm.avatar.lipsync import cached, lipsync
from cvm.config import settings
from cvm.media import H264, duration, probe, run_ffmpeg
from cvm.models import Asset, Avatar, Channel, Project, Render
from cvm.paths import project_dir
from cvm.render.captions import SOURCE_LABEL, word_timings, write_ass
from cvm.render.chart import render_chart
from cvm.render.music import credit_for, energetic_start, pick_music
from cvm.voice.narrate import latest_script, narration_asset

log = logging.getLogger(__name__)
PROFILES = yaml.safe_load((Path(__file__).parent / "profiles.yaml").read_text())
INTERMEDIATE = ["-c:v", H264, "-b:v", "20M", "-pix_fmt", "yuv420p", "-an"]
def _filter_path(p: Path) -> str:
    """A file path inside a quoted filter argument: forward slashes, and the drive colon (D:) escaped."""
    return Path(p).as_posix().replace(":", r"\:").replace("'", r"\'")


THUMB_FONT = ("/System/Library/Fonts/Supplemental/Arial Black.ttf" if sys.platform == "darwin"
              else "C:/Windows/Fonts/ariblk.ttf")  # Arial Black, both systems
CARD_BG = "0x1f2a44"  # slate, light enough not to read as a black screen (render QA flags luma < 10%)
END_SCREEN_S = 20.0  # long videos: the last 20 s stay clean (no captions or text) for YouTube's end screen
HOOK_TEXT_S = 1.2  # Shorts: the strongest line in big letters over the first second
COVER_S = 0.5  # Shorts: the 9:16 cover is the first frame (0.0–0.5 s), so the app can pick it as the cover


def platforms_for(session: Session, project: Project) -> list[str]:
    if project.platforms:
        return project.platforms
    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    return [channel.platform.value if channel else "youtube_shorts"]


def _ass_text(s: str) -> str:
    return s.replace("{", "(").replace("}", ")").replace("\n", "\\N")


def has_audio(path: Path) -> bool:
    try:
        return any(st.get("codec_type") == "audio" for st in probe(path).get("streams", []))
    except Exception:  # noqa: BLE001
        return False


ROOM_TONE = "anoisesrc=color=pink:amplitude=0.0013,lowpass=f=5000"  # ~-58 dBFS: a pause sounds like a room, not a dropout
BED_GAIN = 0.22  # someone else's reel under the presenter (~-13 dB), ducked further while he talks


def audio_graph(length: float, music: bool, beds: list[tuple[float, float]], fade_out: float, fade_d: float) -> str:
    """Filter graph for the final mix. Inputs: 0 narration, then music (if any), then the room tone, then one input
    per bed (already cut to its scene); `beds` = [(scene start, length)]."""
    pad = f"apad=whole_dur={length:.3f},"
    n_side = (1 if music else 0) + (1 if beds else 0)
    parts = [f"[0:a]{pad}asplit={n_side + 1}[v0]" + "".join(f"[sc{k}]" for k in range(n_side))]
    k = 0
    mix = ["[v0]"]
    nxt = 1
    if music:
        parts.append(f"[{nxt}:a]volume=0.28,afade=t=in:d=1.0,afade=t=out:st={fade_out:.2f}:d={fade_d:.1f}[m];"
                     f"[m][sc{k}]sidechaincompress=threshold=0.03:ratio=10:attack=15:release=450[duck]")
        mix.append("[duck]"); k += 1; nxt += 1
    room = nxt; nxt += 1
    parts.append(f"[{room}:a]atrim=0:{length:.3f}[room]"); mix.append("[room]")
    if beds:
        labels = []
        for j, (at, d) in enumerate(beds):
            ms = int(max(0.0, at) * 1000)
            parts.append(f"[{nxt + j}:a]aresample=48000,aformat=channel_layouts=stereo,volume={BED_GAIN},"
                         f"afade=t=in:d=0.15,afade=t=out:st={max(0.0, d - 0.2):.2f}:d=0.2,adelay={ms}|{ms}[b{j}]")
            labels.append(f"[b{j}]")
        parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,apad=whole_dur={length:.3f}[bedsum];"
                     f"[bedsum][sc{k}]sidechaincompress=threshold=0.03:ratio=4:attack=20:release=350[bed]")
        mix.append("[bed]")
    parts.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    return ";".join(parts)


# ---------- scene clips ----------

def _video_clip(src: Path, d: float, W: int, H: int, fps: int, out: Path, hold: bool = False,
                start: float = 0.0, fit: bool = False) -> Path:
    """Fit a video to the scene. hold=True (generated clips) freezes the last frame instead of looping, so a
    short AI shot never jumps back to its first frame. start: where to cut in (seconds). fit=True (the user's own
    footage) shows the whole frame over a blurred copy instead of cropping, so screen recordings keep their UI."""
    loop = [] if hold else ["-stream_loop", "-1"]
    seek = ["-ss", f"{start:.2f}"] if start > 0 else []
    pad = f",tpad=stop_mode=clone:stop_duration={d:.3f}" if hold else ""
    if hold and not fit and (have := duration(src) - start) > 0 and d > have + 0.5:
        # a 5 s AI shot in a longer scene: play it a bit slower and keep the camera pushing in to the end, so the
        # held last frame doesn't read as a frozen picture
        slow = min(d / have, MAX_SLOWDOWN)
        frames = max(int(d * fps), 1)
        vf = (f"setpts={slow:.3f}*PTS,scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop={W * 2}:{H * 2},fps={fps}{pad},"
              f"zoompan=z='1+0.12*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps},setsar=1")
        run_ffmpeg(*seek, "-i", str(src), "-t", f"{d:.3f}", "-vf", vf, *INTERMEDIATE, str(out))
        return out
    if fit:
        vf = (f"split[bg][fg];[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:2,eq=brightness=-0.08[b];"
              f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[f];[b][f]overlay=(W-w)/2:(H-h)/2,"
              f"fps={fps}{pad},setsar=1")
    else:
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},fps={fps}{pad},setsar=1"
    run_ffmpeg(*loop, *seek, "-i", str(src), "-t", f"{d:.3f}", "-vf", vf, *INTERMEDIATE, str(out))
    return out


MAX_SLOWDOWN = 1.4  # how much a short generated clip may be slowed to fill its scene
AUTO_MOVES = ["push_in", "pan_right", "pull_out", "pan_left", "push_in", "pan_up"]


def camera_expr(move: str, frames: int) -> tuple[str, str, str]:
    """zoompan (z, x, y) expressions for a move over `frames` output frames (`on` = output frame number)."""
    p = f"(on/{max(frames - 1, 1)})"
    center_x, center_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    return {
        "push_in": (f"1+0.10*{p}", center_x, center_y),
        "pull_out": (f"1.10-0.10*{p}", center_x, center_y),
        "pan_right": ("1.12", f"(iw-iw/zoom)*{p}", center_y),
        "pan_left": ("1.12", f"(iw-iw/zoom)*(1-{p})", center_y),
        "pan_down": ("1.12", center_x, f"(ih-ih/zoom)*{p}"),
        "pan_up": ("1.12", center_x, f"(ih-ih/zoom)*(1-{p})"),
    }[move]


def _image_clip(src: Path, d: float, W: int, H: int, fps: int, out: Path, zoom: bool = True, move: str = "push_in") -> Path:
    frames = max(int(d * fps), 1)
    fit = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=40:5[bg];"
           f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
    if zoom and move != "static":
        z, x, y = camera_expr(move, frames)
        motion = f",scale={W * 2}:{H * 2},zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={fps}"
    else:
        motion = f",loop={frames}:1:0,fps={fps}"
    run_ffmpeg("-i", str(src), "-vf", fit + motion + ",setsar=1", "-frames:v", str(frames), *INTERMEDIATE, str(out))
    return out


def _card_clip(d: float, W: int, H: int, fps: int, out: Path) -> Path:
    run_ffmpeg("-f", "lavfi", "-i", f"color=c={CARD_BG}:s={W}x{H}:d={d:.3f}:r={fps}", *INTERMEDIATE, str(out))
    return out


def _is_landscape(video: Path) -> bool:
    st = next(s for s in probe(video)["streams"] if s["codec_type"] == "video")
    return st["width"] > st["height"]


def _presenter_square(bg: Path, avatar: Path, d: float, W: int, H: int, fps: int, margin_bottom: int, out: Path) -> Path:
    """Vertical frame from a landscape presenter video without enlarging the face: a centred square crop at
    (or below) its own resolution, sitting just above the caption area, over the scene's visual (darkened)."""
    y = max(0, H - margin_bottom - W)
    run_ffmpeg("-i", str(bg), "-stream_loop", "-1", "-i", str(avatar), "-t", f"{d:.3f}", "-filter_complex",
               f"[0:v]eq=brightness=-0.12:saturation=0.85[b];"
               f"[1:v]crop='min(iw,ih)':'min(iw,ih)',scale={W}:{W}:flags=lanczos,setsar=1[a];"
               f"[b][a]overlay=0:{y},fps={fps}", *INTERMEDIATE, str(out))
    return out


@contextlib.contextmanager
def written_aside(final: Path):
    """Yield a temp path next to `final`; on success it replaces `final` in one step, on failure it is removed and
    the old file stays. The review page streams `final` while a re-render runs (2026-11-22: "fica carregando")."""
    tmp = final.with_name(final.stem + ".encoding" + final.suffix)
    try:
        yield tmp
        tmp.replace(final)
    finally:
        tmp.unlink(missing_ok=True)


def _cutout(clip: Path) -> Path:
    """The talking avatar clip cut out of its photo's background (RVM, .mov with alpha), made once per clip.
    Never the plain clip: the user's rule (2026-11-22) is no white background behind the avatar, ever."""
    from cvm.avatar import matting

    alpha = clip.with_name(clip.stem + "_alpha.mov")
    if alpha.exists() and alpha.stat().st_mtime >= clip.stat().st_mtime:
        return alpha
    if not matting.matting_ready():
        raise RuntimeError("recorte do apresentador indisponível (RVM): o avatar não vai ao ar com fundo branco")
    return matting.matte(clip, alpha)


# Shorts: the presenter only small, at the side, now and then — never big, never covering the game (user, 2026-11-24)
SHORT_PRESENTER = 0.17  # height of his square in a vertical render (≈ 30% of the width)


def short_presenter_hide(scenes: list[dict], timings: dict, t0: float, length: float) -> list[tuple[float, float]]:
    """Vertical renders: video-time ranges where the presenter stays off screen — everything except the first scene
    and the scenes the script gives him (avatar_full / avatar_pip)."""
    show = [(timings[s["index"]]["start"] - t0, timings[s["index"]]["end"] - t0) for k, s in enumerate(scenes)
            if s["index"] in timings and (k == 0 or s["visual"]["type"] in ("avatar_full", "avatar_pip"))]
    hide, at = [], 0.0
    for a, b in sorted(show):
        if a > at:
            hide.append((at, a))
        at = max(at, b)
    if at < length:
        hide.append((at, length))
    return hide


def _overlay_cutout(bg: Path, cut: Path, d: float, W: int, H: int, fps: int, margin_bottom: int, out: Path,
                    big: bool) -> Path:
    """The cut-out avatar over the scene: big = his own scene (large, right in landscape); else a corner presence
    above the captions. No frame, no background. Vertical (Shorts) is always the small side presence, never big and
    never over the middle of the picture (user, 2026-11-24)."""
    if H > W:
        side = int(H * SHORT_PRESENTER) // 2 * 2
        x, y = W - side - int(W * 0.02), H - side - (margin_bottom + 40)
    elif big:
        side = int(H * 0.85) // 2 * 2
        x, y = W - side - int(W * 0.04), H - side
    else:
        side = int(H * 0.42) // 2 * 2
        x, y = W - side - 20, H - side
    run_ffmpeg("-i", str(bg), "-stream_loop", "-1", "-i", str(cut), "-t", f"{d:.3f}", "-filter_complex",
               f"[1:v]crop='min(iw,ih)':'min(iw,ih)',scale={side}:{side}:flags=lanczos,format=yuva420p[p];"
               f"[0:v][p]overlay={x}:{y}:format=auto,fps={fps}", *INTERMEDIATE, str(out))
    return out


def _pip(bg: Path, avatar: Path, d: float, W: int, H: int, fps: int, margin_bottom: int, out: Path) -> Path:
    pw = int(W * (0.42 if H > W else 0.28)) // 2 * 2
    ph = int(pw * 1.2) // 2 * 2
    # cover the portrait box whatever the presenter's orientation (landscape bases are 16:9), then centre-crop
    run_ffmpeg("-i", str(bg), "-stream_loop", "-1", "-i", str(avatar), "-t", f"{d:.3f}", "-filter_complex",
               f"[1:v]scale={pw}:{ph}:force_original_aspect_ratio=increase:flags=lanczos,crop={pw}:{ph},"
               f"setsar=1,pad=iw+12:ih+12:6:6:white[p];"
               f"[0:v][p]overlay=W-w-40:H-h-{margin_bottom + 60},fps={fps}", *INTERMEDIATE, str(out))
    return out


def _end_screen_tail(video: Path, d: float, W: int, H: int, fps: int, out: Path) -> Path:
    """The video's last frame held for `d` seconds: it eases into a darker blur under a slow push-in (moving, so
    render QA doesn't call it frozen), a clean backdrop for the end screen's elements."""
    last = out.with_suffix(".last.png")
    run_ffmpeg("-sseof", "-0.3", "-i", str(video), "-update", "1", "-frames:v", "1", str(last))
    frames = max(int(d * fps), 1)
    run_ffmpeg("-loop", "1", "-i", str(last), "-filter_complex",
               f"[0:v]scale={W * 2}:{H * 2},zoompan=z='1+0.08*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
               f":d=1:s={W}x{H}:fps={fps},split[a][b];[b]boxblur=28:2,eq=brightness=-0.12:saturation=0.85[bl];"
               "[a][bl]blend=all_expr='A*(1-min(1,T/1.2))+B*min(1,T/1.2)',setsar=1",
               "-frames:v", str(frames), *INTERMEDIATE, str(out))
    last.unlink(missing_ok=True)
    return out


def end_screen_plan(length: float, speech_end: float, overlays: list[dict]) -> float:
    """Seconds of clean tail to add so nothing is written on screen in the last END_SCREEN_S (overlays are cut
    at the end of the speech)."""
    for o in overlays:
        o["e"] = min(o["e"], max(o["s"], speech_end))
    return max(0.0, END_SCREEN_S - (length - speech_end))


def short_hook_text(content: dict, first_scene: dict | None, whole: bool) -> str | None:
    """Shorts: the strongest line, shown big in 0–1 s — scene 1's on-screen text, else the thumbnail words, else
    a short hook. Cuts of a long video use their own first scene's text only."""
    if first_scene and (t := (first_scene.get("on_screen_text") or "").strip()):
        return t
    if not whole:
        return None
    if t := ((content.get("packaging") or {}).get("thumbnail_text") or "").strip():
        return t
    hook = (content.get("hook") or "").strip()
    return hook if hook and len(hook.split()) <= 8 else None


# ---------- main ----------

def _avatar_clips(session: Session, project: Project, language: str, scenes: list[dict], work: Path) -> dict[int, Path]:
    """Lipsync only the scenes that show the avatar. Static (non-talking) results are not used."""
    if editorial_mode(session, project):
        return {}  # avatar_id remains the narrator identity; editorial never puts that avatar on screen
    avatar = session.get(Avatar, project.avatar_id) if project.avatar_id else None
    needs = [s for s in scenes if s["visual"]["type"] in ("avatar_full", "avatar_pip")]
    if not avatar or not avatar.base_video_dir or not needs:
        return {}
    base = Path(avatar.base_video_dir) / "base.mp4"
    voice_dir = project_dir(project.id, language) / "voice"
    work.mkdir(parents=True, exist_ok=True)
    out = {}
    for n, s in enumerate(needs):
        progress.report(n, len(needs), f"{language}: cena com avatar {n + 1}/{len(needs)}")
        wav = voice_dir / f"scene_{s['index']:02d}.wav"
        target, marker = work / f"avatar_{s['index']:02d}.mp4", work / f"avatar_{s['index']:02d}.engine"
        if engine := cached(target, marker, wav, base):
            path = target  # made by the lipsyncing stage with the current engine and base video
        else:
            path, engine = lipsync(base, wav, target)
            marker.write_text(engine)
        if engine != "static" or settings.allow_static_avatar:
            out[s["index"]] = path
    return out


def presenter_mode(session: Session, project: Project) -> str:
    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    return project.presenter or (channel.presenter if channel else None) or "scenes"


def editorial_mode(session: Session, project: Project) -> bool:
    """Editorial never shows an avatar, at any aspect ratio. Its special header/layout is vertical-only."""
    return presenter_mode(session, project) == "editorial"


def cover_presenter(mode: str, shown: bool) -> bool:
    """The presenter's cut-out goes on the cover/thumbnail only when the video itself shows him (owner, 2026-11-25:
    "Não coloque o avatar na capa se eu não solicitar avatar"): never in editorial (header + clip, no avatar), never
    when no avatar scene/overlay/podcast was actually rendered."""
    return bool(shown and mode != "editorial")


def source_with_facecam(session: Session, project: Project) -> Asset | None:
    """The project's recording that carries a webcam box (Asset meta `facecam`, cvm/render/stack.py), if any."""
    from cvm.render.stack import source_facecam

    rows = session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "user_media")
                        .order_by(Asset.id)).all()
    return next((a for a in rows if (a.meta or {}).get("kind", "video") == "video" and a.path
                 and source_facecam(a.meta)), None)


def header_graph(ass: str, cover: bool, length: float, W: int, H: int, fps: int,
                 place: tuple[str, int, int] = ("", 0, 0)) -> str:
    """-filter_complex of the editorial Short's final pass: captions, the header still (input 2) at `place` (scale
    filter, x, y — the top of the frame, or its band in the 3-panel layout), then the 9:16 cover (input 3) over the
    first COVER_S. The header is one still repeated by the loop filter (an `-loop 1` image input stalled an encode)."""
    scale, x, y = place
    g = (f"[0:v]ass='{ass}'[c];[2:v]{scale}format=rgba,loop=loop=-1:size=1,trim=duration={length:.3f},"
         f"setpts=N/{fps}/TB[h];[c][h]overlay={x}:{y}:eof_action=pass[hd]")
    if cover:
        g += (f";[3:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,format=yuv420p,"
              f"loop=loop=-1:size=1,trim=duration={COVER_S},setpts=N/{fps}/TB[cv];[hd][cv]overlay=0:0:eof_action=pass[v]")
    return g


def presenter_overlay(session: Session, project: Project) -> bool:
    """True when the project (or else its channel) shows the cut-out presenter over the whole video, and everything
    it needs exists."""
    from cvm.avatar.matting import matting_ready

    avatar = session.get(Avatar, project.avatar_id) if project.avatar_id else None
    return bool(presenter_mode(session, project) == "overlay" and avatar and avatar.base_video_dir and matting_ready())


def podcast_base(session: Session, project: Project) -> Path | None:
    """The avatar's podcast-desk base video when the project is a podcast (else None: normal presenter)."""
    avatar = session.get(Avatar, project.avatar_id) if project.avatar_id else None
    if presenter_mode(session, project) != "podcast" or not avatar or not avatar.podcast_video_dir:
        return None
    base = Path(avatar.podcast_video_dir) / "base.mp4"
    return base if base.exists() else None


def _presenter_podcast(session: Session, project: Project, language: str, work: Path) -> Path | None:
    """The podcast host speaking the whole narration at his desk (not cut out). Cached until the narration changes."""
    narr = narration_asset(session, project.id, language)
    base = podcast_base(session, project)
    if not narr or not base:
        return None
    wav = Path(narr.path)
    work.mkdir(parents=True, exist_ok=True)
    talk, marker = work / "podcast.mp4", work / "podcast.engine"
    if cached(talk, marker, wav, base):
        return talk
    progress.report(0, 1, f"{language}: apresentador do podcast falando a narração inteira")
    _, engine = lipsync(base, wav, talk)
    marker.write_text(engine)
    return None if engine == "static" and not settings.allow_static_avatar else talk


def _podcast_window(video: Path, talk: Path, t0: float, d: float, W: int, H: int, fps: int, margin_bottom: int,
                    full: list[tuple[float, float]], out: Path) -> Path:
    """The podcast host in a boxed window (with his studio, not cut out) at the bottom right of the whole video,
    hidden while a scene already shows him full screen."""
    pw = int(W * (0.28 if H > W else 0.24)) // 2 * 2
    ph = int(pw * (1.15 if H > W else 0.75)) // 2 * 2
    enable = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in full)
    hide = f":enable='not({enable})'" if full else ""
    run_ffmpeg("-i", str(video), "-ss", f"{t0:.3f}", "-i", str(talk), "-t", f"{d:.3f}", "-filter_complex",
               f"[1:v]scale={pw}:{ph}:force_original_aspect_ratio=increase:flags=lanczos,crop={pw}:{ph},setsar=1,"
               f"pad=iw+10:ih+10:5:5:white[p];"
               f"[0:v][p]overlay=W-w-{int(W * 0.025)}:H-h-{margin_bottom + int(H * 0.03)}:eof_action=pass{hide},fps={fps}",
               *INTERMEDIATE, str(out))
    return out


def _presenter_alpha(session: Session, project: Project, language: str, work: Path) -> Path | None:
    """The presenter speaking the whole narration, cut out (alpha .mov). Cached until the narration changes."""
    from cvm.avatar import talking

    token = talking.NO_REFINE.set(talking.refine_off(session, project.id))
    try:
        return _presenter_alpha_inner(session, project, language, work)
    finally:
        talking.NO_REFINE.reset(token)


def _presenter_alpha_inner(session: Session, project: Project, language: str, work: Path) -> Path | None:
    from cvm.avatar.matting import matte

    avatar = session.get(Avatar, project.avatar_id)
    narr = narration_asset(session, project.id, language)
    if not narr:
        return None
    wav = Path(narr.path)
    work.mkdir(parents=True, exist_ok=True)
    talk, alpha, marker = work / "presenter.mp4", work / "presenter_alpha.mov", work / "presenter.engine"
    base = Path(avatar.base_video_dir) / "base.mp4"
    if cached(alpha, marker, wav, base):
        return alpha  # same narration, base video and engine
    progress.report(0, 2, f"{language}: apresentador falando a narração inteira")
    _, engine = lipsync(base, wav, talk)
    marker.write_text(engine)
    if engine == "static" and not settings.allow_static_avatar:
        return None
    progress.report(1, 2, f"{language}: recortando o apresentador")
    return matte(talk, alpha)


def silent_ranges(scenes: list[dict], timings: list[dict], t0: float) -> list[tuple[float, float]]:
    """(start, end) in video time of the scenes without the presenter's own lines: the source's own voice plays or
    nothing is narrated. He leaves the screen there (user, 2026-11-22: "se não tem fala, não mostra avatar")."""
    said = {sc["index"]: bool((sc.get("narration") or "").strip()) for sc in scenes}
    return [(t["start"] - t0, t["end"] - t0) for t in timings
            if t.get("original_audio") or not said.get(t["index"], True)]


def captions_outside(words: list[dict], ranges: list[tuple[float, float]]) -> list[dict]:
    """Word captions minus those inside `ranges` (the stretches where the presenter is off screen because the
    source's own voice plays): no caption there (user, 2026-11-22)."""
    return [w for w in words if not any(a <= (w["s"] + w["e"]) / 2 < b for a, b in ranges)]


def _overlay_presenter(video: Path, alpha: Path, t0: float, d: float, W: int, H: int, fps: int, out: Path,
                       hide: list[tuple[float, float]] | None = None, margin_bottom: int = 0) -> tuple[Path, int]:
    """Put the cut-out presenter at the bottom of every scene (centred in vertical, bottom-right in landscape).
    `hide`: (start, end) seconds of video time where he leaves the screen — the source's own audio is playing and
    someone else is talking (user, 2026-11-22). Returns the new video and the presenter's height, so captions can
    sit above it."""
    side = int(H * (SHORT_PRESENTER if H > W else 0.55)) // 2 * 2
    x = W - side - int(W * 0.02)
    y = H - side - (margin_bottom + 40 if H > W else 0)  # Shorts: small, at the side, above the captions
    gate = (":enable='not(" + "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in hide) + ")'") if hide else ""
    run_ffmpeg("-i", str(video), "-ss", f"{t0:.3f}", "-i", str(alpha), "-t", f"{d:.3f}", "-filter_complex",
               f"[1:v]crop='min(iw,ih)':'min(iw,ih)',scale={side}:{side}:flags=lanczos,format=yuva420p[p];"
               f"[0:v][p]overlay={x}:{y}:eof_action=pass:format=auto{gate},fps={fps}", *INTERMEDIATE, str(out))
    return out, (0 if H > W else side)


def prepare_avatar(session: Session, project: Project) -> dict[str, list[int]]:
    """Lipsync stage: render the avatar scenes (or the whole-video cut-out presenter) for every language."""
    if editorial_mode(session, project):
        return {lang: [] for lang in project.languages}
    result = {}
    overlay = presenter_overlay(session, project)
    for lang in progress.parts(project.languages):
        script = latest_script(session, project.id, lang)
        if not script:
            continue
        if podcast_base(session, project):
            _presenter_podcast(session, project, lang, project_dir(project.id, lang) / "avatar")
            result[lang] = []
        elif overlay:
            _presenter_alpha(session, project, lang, project_dir(project.id, lang) / "avatar")
            result[lang] = []
        else:
            result[lang] = sorted(_avatar_clips(session, project, lang, script.content["scenes"], project_dir(project.id, lang) / "avatar"))
    return result


def compose(session: Session, project: Project, language: str, profile_name: str, part: tuple[int, list[int]] | None = None) -> Render:
    """part=(n, scene_indices) renders only those scenes as cut n (see compose_cuts)."""
    from cvm import editorial as editorial_format

    if part is None and editorial_format.wanted(session, project, language, profile_name):
        return _compose_editorial(session, project, language, profile_name)  # the news-desk long video
    prof = PROFILES[profile_name]
    W, H, fps = prof["width"], prof["height"], prof["fps"]
    narr = narration_asset(session, project.id, language)
    script = latest_script(session, project.id, language)
    if not narr or not script:
        raise RuntimeError(f"narration/script missing for {language}")
    scenes = script.content["scenes"]
    timings = {t["index"]: t for t in narr.meta["scenes"]}
    visuals = {a.scene: a for a in session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "visual"))}

    out_dir = project_dir(project.id, language) / (profile_name + (f"_part{part[0]}" if part else ""))
    work = out_dir / "work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    mode = presenter_mode(session, project)
    no_presenter = mode == "editorial"  # any ratio; avatar_id is still used by narration
    # whole-video presenter: no full-screen avatar scenes (they show their backup visual), the presenter goes on top
    # podcast: the host talks the whole narration at his desk; full screen in avatar_full scenes, a window elsewhere
    talk = None if no_presenter else (_presenter_podcast(session, project, language, project_dir(project.id, language) / "avatar")
                                       if podcast_base(session, project) else None)
    alpha = None if no_presenter else (_presenter_alpha(session, project, language, project_dir(project.id, language) / "avatar")
                                        if not talk and presenter_overlay(session, project) else None)
    avatars = {} if no_presenter or alpha or talk else _avatar_clips(
        session, project, language, scenes, project_dir(project.id, language) / "avatar")
    full_shots: list[tuple[float, float]] = []  # (start, end) of the podcast's full-screen scenes, video time

    total = narr.meta["duration"]
    if part:
        scenes = [sc for sc in scenes if sc["index"] in part[1]]
        last = max(part[1])
        after = [t["start"] for t in narr.meta["scenes"] if t["index"] > last]
        t0 = timings[min(part[1])]["start"]
        total = (after[0] - 0.05) if after else total
    else:
        t0 = 0.0
    limit = prof.get("max_seconds")
    clips, overlays, end_at = [], [], total
    beds: list[tuple[Path, float, float, float]] = []  # (clip, its start, scene start, length): sound under the voice
    ordered = [s for s in scenes if s["index"] in timings]
    vertical = H > W
    from cvm.render import editorial as editorial_mod

    editorial = editorial_mod.wanted(mode, vertical)  # special header/layout is vertical-only; avatar suppression is not
    from cvm.render import stack as stack_mod  # live highlights: streamer camera on top, game below (cvm/render/stack.py)

    # the recording's own webcam box (cut editor / own live): editorial Shorts get camera + header + gameplay
    fc_asset = source_with_facecam(session, project)
    src_fc = stack_mod.source_facecam(fc_asset.meta) if fc_asset else None
    stack_fc = stack_mod.wanted(project, session.get(Channel, project.channel_id) if project.channel_id else None,
                                vertical, editorial, source_fc=src_fc)
    stack_size = None
    lay3 = stack_mod.layout3(src_fc, (fc_asset.meta or {}).get("facecam_order"), W, H,
                             *stack_mod.src_size(Path(fc_asset.path))) \
        if stack_mod.three_wanted(editorial, vertical, src_fc) else None
    cover_at: tuple[Path, dict, float, float] | None = None  # (recording, box, cut start, cut end) for the reaction cover
    end_screen = profile_name == "youtube" and not part
    hook_text = short_hook_text(script.content, ordered[0] if ordered else None, not part) if vertical and not editorial else None
    if hook_text:
        overlays.append({"text": _ass_text(hook_text), "s": 0.0, "e": HOOK_TEXT_S, "style": "Hook",
                         "fx": "{\\fad(0,150)\\fscx70\\fscy70\\t(0,160,\\fscx100\\fscy100)}"})
    for i, s in enumerate(ordered):
        t = timings[s["index"]]
        nxt = timings[ordered[i + 1]["index"]]["start"] if i + 1 < len(ordered) else total + 0.4
        if limit and nxt - t0 > limit:  # cut at a scene boundary
            end_at = t["start"] if i else min(total, t0 + limit)
            break
        d = nxt - t["start"]
        v = visuals.get(s["index"])
        vtype = s["visual"]["type"]
        kind = (v.meta.get("kind") if v else None) or "card"
        clip = work / f"scene_{s['index']:02d}.mp4"
        src = Path(v.path) if v and v.path else None

        chart_png = None
        if vtype == "chart" and s["visual"].get("chart"):
            chart_png = render_chart(s["visual"]["chart"], W, H, prof["caption_margin_bottom"], work / f"chart_{s['index']:02d}.png", language)
        on_scene = vtype in ("avatar_full", "avatar_pip") and s["index"] in avatars
        if talk and vtype == "avatar_full" and not vertical:  # Shorts: never full screen (user, 2026-11-24)
            _video_clip(talk, d, W, H, fps, clip, hold=True, start=t["start"])
            full_shots.append((t["start"] - t0, t["start"] - t0 + d))
            kind = "avatar"
        elif chart_png and not on_scene:
            _image_clip(chart_png, d, W, H, fps, clip, zoom=True, move="push_in")
            kind = "chart"
        else:
            if kind == "video" and src:
                own_fc = stack_mod.source_facecam(fc_asset.meta) if lay3 and v.meta.get("media_id") == fc_asset.id else None
                if own_fc:  # the streamer's reaction on top (or under the header), the whole game below
                    start = float(v.meta.get("start") or 0)
                    stack_mod.clip3(src, d, W, H, fps, clip, own_fc, lay3, start=start,
                                    hold=bool(v.meta.get("hold_last_frame")), intermediate=INTERMEDIATE)
                    if cover_at is None:
                        cover_at = (src, own_fc, start, start + d)
                    elif cover_at[0] == src:
                        cover_at = (src, own_fc, cover_at[2], start + d)
                elif stack_fc and v.meta.get("media_id"):  # the live recording itself: camera on top, game below
                    stack_size = stack_size or stack_mod.src_size(src)
                    stack_mod.clip(src, d, W, H, fps, clip, stack_fc, start=float(v.meta.get("start") or 0),
                                   hold=bool(v.meta.get("hold_last_frame")), intermediate=INTERMEDIATE)
                else:
                    _video_clip(src, d, W, H, fps, clip, hold=bool(v.meta.get("hold_last_frame")),
                                start=float(v.meta.get("start") or 0), fit=bool(v.meta.get("fit")) or editorial)
                # someone else's reel: its own sound stays, low, under the presenter's comment (user, 2026-11-23)
                src_asset = session.get(Asset, v.meta["media_id"]) if v.meta.get("media_id") else None
                if (s["visual"].get("audio_under") or v.meta.get("audio_under") or (src_asset and (src_asset.meta or {}).get("audio_under"))) \
                        and not t.get("original_audio") and has_audio(src):
                    beds.append((src, float(v.meta.get("start") or 0), t["start"] - t0, d))
                if t.get("original_audio"):  # the user speaking in his own recording: no podcast window over him
                    full_shots.append((t["start"] - t0, t["start"] - t0 + d))
            elif kind in ("image", "screenshot") and src:
                # headlines keep a gentle push-in so the text stays readable; photos vary unless the script says
                move = s["visual"].get("camera") or (AUTO_MOVES[s["index"] % len(AUTO_MOVES)] if kind == "image" else "push_in")
                _image_clip(src, d, W, H, fps, clip, zoom=True, move=move)
            else:
                _card_clip(d, W, H, fps, clip)
                kind = "card"
            if on_scene:  # the cut-out avatar over the scene's own (or backup) visual, never his photo's background
                framed = work / f"scene_{s['index']:02d}_avatar.mp4"
                clip = _overlay_cutout(clip, _cutout(avatars[s["index"]]), d, W, H, fps, prof["caption_margin_bottom"],
                                       framed, big=vtype == "avatar_full")
                if vtype == "avatar_full":
                    kind = "avatar"
        clips.append(clip)

        # a text card shows its text the whole scene (an empty card reads as a black screen); titles fade after 4 s;
        # a drawn chart already carries its own title
        text = s.get("on_screen_text") or (s["narration"][:80] if kind == "card" else None)
        if editorial and kind != "card":  # the fixed header and the captions carry the text; a title would sit on the header
            text = None
        if text and kind != "chart":
            o = {"text": _ass_text(text), "s": t["start"] - t0, "e": t["start"] - t0 + (d if kind == "card" else min(d, 4.0)),
                 "style": "Card" if kind == "card" else "Title"}
            if hook_text and i == 0 and text.strip() == hook_text:  # the hook already shows it big: the title follows
                o["s"] = HOOK_TEXT_S
            if o["e"] > o["s"]:
                overlays.append(o)
        if v and v.meta.get("credit") and kind != "card":
            overlays.append({"text": _ass_text(f"{SOURCE_LABEL.get(language, 'Fonte')}: {v.meta['credit']}"), "s": t["start"] - t0, "e": t["start"] - t0 + d, "style": "Credit"})

    if stack_size:  # titles and the hook go down into the game panel: no text over the streamer's face
        for o in overlays:
            if o.get("style") in ("Title", "Hook"):
                o["fx"] = stack_mod.text_fx(stack_fc, W, H, o["style"], *stack_size) + o.get("fx", "")

    # captions (word timings), needed before the concat to plan the end-screen tail
    all_scenes = script.content["scenes"]
    words = [{**w, "s": w["s"] - t0, "e": w["e"] - t0}
             for w in word_timings(Path(narr.path), language, all_scenes, narr.meta["scenes"], settings.use_whisper_captions)
             if t0 <= w["s"] < end_at]
    # captions only on the presenter's own lines: never over the source's own voice (user, 2026-11-22, all videos)
    silent = silent_ranges(script.content["scenes"], narr.meta["scenes"], t0)
    # stretches where the presenter must not be on screen although the narration is ours: e.g. a take whose made-up
    # words were silenced afterwards (643, by the rhinos) while the avatar was already generated moving its mouth
    from cvm.models import Setting
    _hide = session.get(Setting, f"presenter_hide:{project.id}")
    silent = sorted([*silent, *[(a0 - t0, a1 - t0) for a0, a1 in ((_hide.value or {}).get(language) or [] if _hide else []) if a1 > t0]])
    words = captions_outside(words, silent)
    length = end_at - t0
    tail = 0.0
    if end_screen and clips:
        speech_end = min(length, max((w["e"] for w in words), default=length))
        tail = end_screen_plan(length, speech_end, overlays)

    # concat
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{c}'\n" for c in clips))
    video = work / "video.mp4"
    run_ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(video))
    if tail > 0.05:  # end screen: hold the last picture, clean, with the music tail
        tail_clip = _end_screen_tail(video, tail, W, H, fps, work / "end_screen.mp4")
        lst.write_text("".join(f"file '{c}'\n" for c in clips + [tail_clip]))
        video = work / "video_end.mp4"
        run_ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(video))
        length += tail
    caption_margin = prof["caption_margin_bottom"]
    if vertical and (talk or alpha):  # Shorts: he shows up only now and then (hook + his own scenes)
        now_and_then = short_presenter_hide(ordered, timings, t0, length)
        full_shots = sorted([*full_shots, *now_and_then])
        silent = sorted([*silent, *now_and_then])
    if talk:
        video = _podcast_window(video, talk, t0, length, W, H, fps, caption_margin, full_shots, work / "video_podcast.mp4")
    if alpha:
        video, side = _overlay_presenter(video, alpha, t0, length, W, H, fps, work / "video_presenter.mp4",
                                         hide=silent, margin_bottom=caption_margin)  # off while he isn't the one talking
        caption_margin = max(caption_margin, side + int(H * 0.02))

    if lay3:  # 3 panels: captions at the bottom of the gameplay, off the streamer's face and the header
        caption_margin = stack_mod.caption_margin3(H)
    elif editorial:  # captions right under the 16:9 picture, the header above it
        caption_margin = H - editorial_mod.CAPTION_TOP - int(H * 0.075)
    ass = write_ass(words, work / "captions.ass", W, H, caption_margin, overlays, big=vertical)

    # thumbnails first: a vertical render opens on its own 9:16 cover
    from cvm.render.thumbnail import make_thumbnails

    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    scene_media = {i: Path(v.meta.get("still") or v.path) for i, v in visuals.items() if v.path}
    # the presenter goes on the cover only when this video shows him (owner, 2026-11-25: "Não coloque o avatar na
    # capa se eu não solicitar avatar"); otherwise a Short's cover comes from the video itself
    with_presenter = cover_presenter(mode, bool(avatars or talk or alpha))
    first = visuals.get(ordered[0]["index"]) if ordered else None
    video_bg = None
    if vertical and not with_presenter:
        if cover_at:  # the streamer's reaction, big
            try:
                rsrc, rfc, ra, rb = cover_at
                video_bg = stack_mod.reaction_cover(rsrc, rfc, stack_mod.reaction_time(rsrc, rfc, ra, rb), W, H,
                                                    work / "cover_reaction.jpg")
            except Exception as e:  # noqa: BLE001 — the first frame still makes a cover
                log.warning("reaction cover skipped: %s", e)
        if video_bg is None and clips and first is not None and (first.meta or {}).get("media_id"):
            video_bg = clips[0]  # the recording's own first scene, never an AI picture
    thumbs = make_thumbnails(project, script.content, language, clips[0] if clips else video, out_dir, W > H,
                             variants=profile_name == "youtube" and not part, kids=bool(channel and channel.made_for_kids),
                             scene_media=scene_media, presenter=with_presenter, background_image=video_bg)
    thumb = thumbs[0]
    cover = thumb if vertical and thumb.exists() else None

    # audio + final encode
    music = pick_music(project, (channel.prefs or {}) if channel else None, session)
    clip_s = length
    music_ss = energetic_start(music, clip_s) if music else 0.0
    final = out_dir / f"{project.id}_{language}_{profile_name}{f'_part{part[0]}' if part else ''}.mp4"
    # Audio is mixed in its own pass: sharing one filter graph with the (slow) subtitle + VideoToolbox video
    # chain dropped audio frames (~6 s lost in a 68 s short), pushing the voice out of sync with the captions.
    mix = work / "audio_mix.wav"
    audio_in = ["-ss", f"{t0:.3f}", "-t", f"{end_at - t0:.3f}", "-i", str(narr.path)] + (["-stream_loop", "-1", "-ss", f"{music_ss:.2f}", "-i", str(music)] if music else [])
    # Shorts loop: a quick fade so the end runs into the start; long videos: a gentle fade over the end screen
    fade_d = 0.5 if vertical else (4.0 if tail else 2.0)
    fade_out = max(0.0, clip_s - fade_d)
    audio_in += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", ROOM_TONE]
    for src, start, _, d in beds:
        audio_in += ["-ss", f"{start:.3f}", "-t", f"{d:.3f}", "-i", str(src)]
    afilter = audio_graph(length, bool(music), [(at, d) for _, _, at, d in beds], fade_out, fade_d)
    run_ffmpeg(*audio_in, "-filter_complex", afilter, "-map", "[a]", "-ar", "48000", str(mix))
    header = None
    if editorial:
        try:
            kicker, headline = editorial_mod.header_text(script.content, project.topic)
            header = editorial_mod.header_png(kicker, headline, channel.name if channel else "", work / "editorial_header.png", W,
                                              height=lay3["header"][1] if lay3 else None)
        except Exception as e:  # noqa: BLE001 — the Short is still watchable without its header
            log.warning("editorial header skipped: %s", e)
    if header:
        place = stack_mod.header_place(lay3, W, png_h=lay3["header"][1]) if lay3 else ("", 0, 0)  # 3 panels: the header sits in its band
        vargs = ["-i", str(header), *(["-i", str(cover)] if cover else []), "-filter_complex",
                 header_graph(_filter_path(ass), bool(cover), length, W, H, fps, place),
                 "-map", "[v]" if cover else "[hd]"]
    elif cover:
        # the cover is one still repeated by the loop filter for COVER_S: an `-loop 1 -t` image input made the
        # overlay stall for good mid-encode (0% CPU, seen on 235's en-US Short with both VideoToolbox and libx264)
        vargs = ["-i", str(cover), "-filter_complex",
                 f"[0:v]ass='{_filter_path(ass)}'[c];[2:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                 f"setsar=1,format=yuv420p,loop=loop=-1:size=1,trim=duration={COVER_S},setpts=N/{fps}/TB[cv];"
                 f"[c][cv]overlay=0:0:eof_action=pass[v]",
                 "-map", "[v]"]
    else:
        vargs = ["-vf", f"ass='{_filter_path(ass)}'", "-map", "0:v"]
    with written_aside(final) as enc:
        run_ffmpeg("-i", str(video), "-i", str(mix), *vargs, "-map", "1:a", "-t", f"{length:.3f}",
                   "-c:v", H264, "-b:v", prof["video_bitrate"], "-profile:v", "high", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(enc))
        lost = duration(mix) - _decoded_audio_seconds(enc)
        if lost > 0.25:
            raise RuntimeError(f"áudio perdeu {lost:.1f}s na codificação final")

    # channel bumper right after the promise (long videos only, see cvm/render/bumper.py)
    from cvm.render import bumper as bumper_mod
    from cvm.render import chapters as chapters_mod

    bumper_meta, intro_end = None, script.content.get("intro_end")
    point = bumper_mod.bumper_point(narr.meta["scenes"], intro_end, end_at) \
        if bumper_mod.wants_bumper(channel, profile_name, part, project) else None
    if point is not None:
        at = point - t0 - 0.05
        try:
            b = bumper_mod.bumper_for(session, channel, language, W, H, prof["fps"])
            spliced = final.with_name(final.stem + "_bumper.mp4")
            _, added = bumper_mod.splice(final, b, at, spliced, prof["video_bitrate"], prof["fps"])
            spliced.replace(final)
            bumper_meta = {"at": round(at, 2), "seconds": round(added, 2)}  # content after `at` moved this much
        except bumper_mod.GenericBumper:  # a kids video never goes out with a generic bumper, nor without one
            raise
        except Exception as e:  # noqa: BLE001 — the video is fine without its bumper
            log.warning("bumper skipped: %s", e)
    final_s = round(duration(final), 2) if bumper_meta else round(min(length, duration(final)), 2)
    chapters = chapters_mod.from_blocks(script.content, {t["index"]: t["start"] for t in narr.meta["scenes"]}, language,
                                        bumper_meta, t0, end=final_s - (END_SCREEN_S if end_screen else 0)) \
        if profile_name == "youtube" and not part else []
    return _save_render(session, project, language, profile_name, part[0] if part else None, final, {
                   "duration": final_s, **(_end_screen_meta(final_s) if end_screen else {}),
                   "chapters": chapters, "cover_frame": bool(cover),
                   "thumbnails": [str(t) for t in thumbs], "bumper": bumper_meta, "scenes": [sc["index"] for sc in ordered if sc["index"] in timings], "thumbnail": str(thumb), "avatar_scenes": sorted(avatars),
                   "avatar_on_screen": bool(avatars or talk or alpha),  # YouTube AI disclosure (cvm/guards.py)
                   **({"layout": f"facecam_{lay3['order']}"} if lay3 else {}),
                   "credits": sorted({v.meta.get("credit") for v in visuals.values() if v.meta.get("credit")})
                   + ([c] if music and (c := credit_for(music)) else []),
                   "music": str(music) if music else None})


def _end_screen_meta(final_s: float) -> dict:
    return {"end_screen_s": END_SCREEN_S, "end_screen_from": round(final_s - END_SCREEN_S, 2)}


def _save_render(session: Session, project: Project, language: str, profile_name: str, pn: int | None, final: Path,
                 meta: dict) -> Render:
    """The Render row of this language × profile (× part), created or updated, pointing at `final`."""
    render = session.exec(select(Render).where(Render.project_id == project.id, Render.language == language,
                                               Render.profile == profile_name, Render.part == pn)).first() \
        or Render(project_id=project.id, language=language, profile=profile_name, part=pn)
    render.path, render.status = str(final), "done"
    render.meta = meta
    session.add(render)
    session.commit()
    session.refresh(render)
    return render


def _compose_editorial(session: Session, project: Project, language: str, profile_name: str) -> Render:
    """The editorial long video (cvm/render/editorial/assemble.py): its own scenes, bumper, end screen and mix; the
    Render row, thumbnails and end-screen meta as for every long video."""
    from cvm.render.editorial import assemble
    from cvm.render.thumbnail import make_thumbnails

    out_dir = project_dir(project.id, language) / profile_name
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{project.id}_{language}_{profile_name}.mp4"
    with written_aside(final) as enc:
        res = assemble.render(session, project, language, enc)
    script = latest_script(session, project.id, language)
    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    thumbs = make_thumbnails(project, script.content if script else {}, language, res.first_segment or final, out_dir,
                             True, variants=True, kids=bool(channel and channel.made_for_kids), scene_media={},
                             presenter=False)  # the news desk has no presenter on screen: no avatar cut-out
    final_s = round(duration(final), 2)
    return _save_render(session, project, language, profile_name, None, final, {
        "duration": final_s, **_end_screen_meta(final_s), "chapters": res.chapters, "cover_frame": False,
        "thumbnails": [str(t) for t in thumbs], "thumbnail": str(thumbs[0]), "bumper": res.bumper, "scenes": [],
        "avatar_scenes": [], "avatar_on_screen": False, "credits": res.credits,
        "music": str(res.music) if res.music else None, "editorial": True,
        "editorial_end_from": res.end_screen_from})


def _decoded_audio_seconds(path: Path) -> float:
    """Real audio length (decoded samples), not the container's declared duration — catches dropped frames."""
    import subprocess

    out = subprocess.run([settings.ffmpeg_bin, "-v", "error", "-i", str(path), "-vn", "-f", "s16le", "-ac", "1", "-ar", "8000", "-"],
                         capture_output=True).stdout
    return len(out) / 2 / 8000


def compose_cuts(session: Session, project: Project, language: str, profile_name: str = "youtube_shorts", target_s: float = 55) -> list[Render]:
    """'Cortes': split a long narration into consecutive shorts of up to target_s seconds, at scene boundaries."""
    narr = narration_asset(session, project.id, language)
    groups, cur, cur_start = [], [], None
    for t in narr.meta["scenes"]:
        if cur and t["end"] - cur_start > target_s:
            groups.append(cur)
            cur = []
        if not cur:
            cur_start = t["start"]
        cur.append(t["index"])
    if cur:
        groups.append(cur)
    return [compose(session, project, language, profile_name, part=(n, g)) for n, g in enumerate(groups, 1)]
