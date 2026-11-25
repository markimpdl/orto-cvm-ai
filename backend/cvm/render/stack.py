""""Melhores momentos da live" layout (Phase 6 of docs/superpowers/plans/2026-11-24-painel-reestruturacao.md, D13).

The streamer's camera is marked once per channel on /canais/[id]: Channel.prefs.facecam = {x, y, w, h}, fractions of
the source frame. In a project of style "live" whose channel has it, every scene that shows the live recording is
laid out as:
- vertical (9:16): the facecam crop on top, scaled to the full width; the game below, cropped to fill the rest and
  shifted sideways away from the camera's corner; a thin accent line between them. Titles and the hook sit in the
  game panel, the word captions at its bottom: no text over the face.
- 16:9, only when Channel.prefs.facecam_landscape is true: the whole game with the camera enlarged in its corner.
Without a facecam (or for other styles, or editorial Shorts, whose header logic is untouched) compose works as before.
"""

from pathlib import Path

from cvm.media import probe, run_ffmpeg

ACCENT = "0xFFD21E"  # same yellow as the editorial header
LINE = 6  # px, the accent line between camera and game
TOP_MIN, TOP_MAX = 0.28, 0.45  # share of the 9:16 height the camera may take
INSET_W = 0.30  # 16:9: the camera's width in the corner, share of the frame width
MIN_SIDE = 0.05


def validate(fc) -> dict:
    """{x, y, w, h} as fractions inside the frame (camera at least 5% wide and tall); ValueError (pt-BR) otherwise."""
    if not isinstance(fc, dict):
        raise ValueError("facecam: envie {x, y, w, h} (frações do quadro, de 0 a 1)")
    try:
        x, y, w, h = (float(fc[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError("facecam: x, y, w e h precisam ser números de 0 a 1") from e
    if min(x, y) < 0 or w < MIN_SIDE or h < MIN_SIDE or x + w > 1.0001 or y + h > 1.0001:
        raise ValueError("facecam: o retângulo precisa caber no quadro e ter pelo menos 5% de largura e altura")
    return {"x": round(x, 4), "y": round(y, 4), "w": round(min(w, 1 - x), 4), "h": round(min(h, 1 - y), 4)}


def facecam_of(channel) -> dict | None:
    """The channel's saved camera rectangle, or None (missing or invalid)."""
    try:
        return validate(((channel.prefs or {}) if channel else {}).get("facecam"))
    except ValueError:
        return None


def wanted(project, channel, vertical: bool, editorial: bool) -> dict | None:
    """The facecam to stack with in this render, or None (= the current layout)."""
    if editorial or project.style != "live":
        return None
    fc = facecam_of(channel)
    if not fc or not (vertical or (channel.prefs or {}).get("facecam_landscape")):
        return None
    return fc


def top_height(fc: dict, W: int, H: int, src_w: int = 1920, src_h: int = 1080) -> int:
    """Height of the camera panel: the camera's own shape at full width, kept between TOP_MIN and TOP_MAX of H."""
    natural = W * (fc["h"] * src_h) / (fc["w"] * src_w)
    return int(round(min(max(natural, H * TOP_MIN), H * TOP_MAX) / 2) * 2)


def _f(v: float) -> str:
    return f"{v:.4f}"


def filter_graph(fc: dict, W: int, H: int, fps: int, src_w: int = 1920, src_h: int = 1080) -> str:
    """-vf graph (one input) for the stack layout at W×H."""
    cam = f"crop=iw*{_f(fc['w'])}:ih*{_f(fc['h'])}:iw*{_f(fc['x'])}:ih*{_f(fc['y'])}"
    if H > W:
        top = top_height(fc, W, H, src_w, src_h)
        bot = H - top
        r = W / bot  # the game window's shape (w/h)
        if fc["x"] + fc["w"] / 2 >= 0.5:  # camera on the right: slide the game window left of it
            gx = f"max(0,min((iw-ow)/2,iw*{_f(fc['x'])}-ow))"
        else:
            gx = f"min(iw-ow,max((iw-ow)/2,iw*{_f(fc['x'] + fc['w'])}))"
        game = f"crop=w='min(iw,ih*{r:.5f})':h='min(ih,iw/{r:.5f})':x='{gx}':y='(ih-oh)/2'"
        return (f"split[a][b];[a]{cam},scale={W}:{top}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{top},setsar=1[cam];[b]{game},scale={W}:{bot}:flags=lanczos,setsar=1[game];"
                f"[cam][game]vstack=inputs=2,drawbox=x=0:y={top - LINE // 2}:w={W}:h={LINE}:color={ACCENT}:t=fill,"
                f"fps={fps},setsar=1")
    iw = int(W * INSET_W) // 2 * 2
    m = int(W * 0.025)
    right = fc["x"] + fc["w"] / 2 >= 0.5
    ox = f"{W}-w-{m}" if right else f"{m}"
    oy = f"{m}"  # always at the top: the captions run along the bottom
    return (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1[game];"
            f"[b]{cam},scale={iw}:-2:flags=lanczos,pad=iw+{LINE * 2}:ih+{LINE * 2}:{LINE}:{LINE}:color={ACCENT},"
            f"setsar=1[cam];[game][cam]overlay=x={ox}:y={oy},fps={fps},setsar=1")


def src_size(src: Path) -> tuple[int, int]:
    st = next((s for s in probe(src)["streams"] if s.get("codec_type") == "video"), {})
    return int(st.get("width") or 1920), int(st.get("height") or 1080)


def clip(src: Path, d: float, W: int, H: int, fps: int, out: Path, fc: dict, start: float = 0.0, hold: bool = False,
         intermediate: list[str] | None = None) -> Path:
    """One scene of the live recording in the stack layout. hold=True freezes the last frame when the recording ends
    before the scene (like compose._video_clip); otherwise it loops."""
    seek = ["-ss", f"{start:.2f}"] if start > 0 else []
    vf = filter_graph(fc, W, H, fps, *src_size(src)) + (f",tpad=stop_mode=clone:stop_duration={d:.3f}" if hold else "")
    run_ffmpeg(*([] if hold else ["-stream_loop", "-1"]), *seek, "-i", str(src), "-t", f"{d:.3f}", "-vf", vf,
               *(intermediate or ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-an"]), str(out))
    return out


def text_fx(fc: dict, W: int, H: int, style: str, src_w: int = 1920, src_h: int = 1080) -> str:
    """ASS override that moves a title/hook into the game panel (vertical stack), so no text covers the face."""
    if W >= H:
        return ""
    top = top_height(fc, W, H, src_w, src_h)
    if style == "Hook":
        return f"{{\\an5\\pos({W // 2},{top + (H - top) // 3})}}"
    return f"{{\\an8\\pos({W // 2},{top + int(H * 0.03)})}}"
