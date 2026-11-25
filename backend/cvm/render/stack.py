""""Melhores momentos da live" layout (Phase 6 of docs/superpowers/plans/2026-11-24-painel-reestruturacao.md, D13).

The streamer's camera is marked once per channel on /canais/[id]: Channel.prefs.facecam = {x, y, w, h}, fractions of
the source frame. In a project of style "live" whose channel has it, every scene that shows the live recording is
laid out as:
- vertical (9:16): the facecam crop on top, scaled to the full width; the game below, cropped to fill the rest and
  shifted sideways away from the camera's corner; a thin accent line between them. Titles and the hook sit in the
  game panel, the word captions at its bottom: no text over the face.
- 16:9, only when Channel.prefs.facecam_landscape is true: the whole game with the camera enlarged in its corner.
Without a facecam (or for other styles, or editorial Shorts, whose header logic is untouched) compose works as before.

Three panels (owner, 2026-11-25: "Mostrar a reação do youtuber é o essencial nesse formato"): an editorial Short
(header + clip, no avatar) whose source recording carries a webcam box in its Asset meta (`facecam`, found by
cvm/lives/facecam.py during the cut analysis or drawn in the cut editor; an own live gets the channel's) is stacked
as camera / header / gameplay ("youtuber_top", default) or header / camera / gameplay ("header_top",
`facecam_order`). The gameplay panel is the whole 16:9 frame at full width, the camera is cropped to fill its panel,
the header band is a blurred, darkened copy of the frame under the editorial header PNG, the word captions run at the
bottom of the gameplay panel (never over the face). The 9:16 cover of such a Short is the streamer's reaction
(`reaction_cover`): his camera big below, the game blurred above under the cover words.
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


def wanted(project, channel, vertical: bool, editorial: bool, source_fc: dict | None = None) -> dict | None:
    """The facecam to stack with in this render, or None (= the current layout). source_fc: the recording's own box
    (Asset meta), preferred to the channel's."""
    if editorial or project.style != "live":
        return None
    fc = source_fc or facecam_of(channel)
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


# ---------- three panels: streamer camera + editorial header + gameplay ----------

ORDERS = ("youtuber_top", "header_top")
BAND_DARK = 0.55  # black over the blurred frame behind the header
HEADER_PNG_H = 560  # cvm/render/editorial.HEADER_H at 1080 wide
HEADER_SHARE, CAM_SHARE = 0.10, 0.40  # the game takes the other 50%
CAM_COVER = 0.62  # the reaction cover: the camera's share of the height
REACTION_MAX = 45.0  # mean grey difference (0–255) above which a camera frame is something else, not a reaction


def source_facecam(meta: dict | None) -> dict | None:
    """The webcam box stored on a recording's Asset meta (`facecam`), or None (missing/invalid/turned off)."""
    try:
        return validate((meta or {}).get("facecam"))
    except ValueError:
        return None


def order_of(v) -> str:
    return v if v in ORDERS else ORDERS[0]


def three_wanted(editorial: bool, vertical: bool, source_fc: dict | None) -> bool:
    """Only vertical editorial Shorts (no avatar on screen) of a recording with a webcam box."""
    return bool(editorial and vertical and source_fc)


def _even(v: float) -> int:
    return int(round(v / 2) * 2)


def layout3(fc: dict, order: str, W: int, H: int, src_w: int = 1920, src_h: int = 1080) -> dict:
    """{"cam": (y, h), "header": (y, h), "game": (y, h), "order"} of the 3-panel frame. Fixed shares (owner,
    2026-11-25: "canal 10% da tela, 40% youtuber, 50% vídeo. Mantém esse padrão"): header HEADER_SHARE, camera
    CAM_SHARE, the game the rest (cropped to fill, away from the streamer's camera corner)."""
    header = _even(H * HEADER_SHARE)
    cam = _even(H * CAM_SHARE)
    game = H - header - cam
    if order_of(order) == "header_top":
        return {"header": (0, header), "cam": (header, cam), "game": (header + cam, game), "order": "header_top"}
    return {"cam": (0, cam), "header": (cam, header), "game": (cam + header, game), "order": "youtuber_top"}


def game_crop_x(fc: dict) -> str:
    """The game panel is cropped from the side away from the camera box, so his camera is not shown twice."""
    return "iw-ow" if fc["x"] + fc["w"] / 2 < 0.5 else "0"


def filter_graph3(fc: dict, lay: dict, W: int, fps: int) -> str:
    """-vf graph (one input) for the 3-panel layout."""
    ch, hh, gh = lay["cam"][1], lay["header"][1], lay["game"][1]
    blur = max(1, min(20, hh // 4 - 1, W // 4 - 1))  # boxblur: radius < half the chroma plane
    cam = (f"[a]crop=iw*{_f(fc['w'])}:ih*{_f(fc['h'])}:iw*{_f(fc['x'])}:ih*{_f(fc['y'])},"
           f"scale={W}:{ch}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{ch},setsar=1[cam]")
    band = (f"[b]scale={W}:{hh}:force_original_aspect_ratio=increase,crop={W}:{hh},boxblur={blur}:2,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{BAND_DARK}:t=fill,setsar=1[hd]")
    game = (f"[c]scale={W}:{gh}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={W}:{gh}:{game_crop_x(fc)}:(ih-oh)/2,setsar=1[game]")
    top = "[hd][cam][game]" if lay["order"] == "header_top" else "[cam][hd][game]"
    lines = "".join(f",drawbox=x=0:y={max(0, y - LINE // 2)}:w={W}:h={LINE}:color={ACCENT}:t=fill"
                    for y in sorted({lay["cam"][0], lay["header"][0], lay["game"][0]} - {0}))
    return f"split=3[a][b][c];{cam};{band};{game};{top}vstack=inputs=3{lines},fps={fps},setsar=1"


def clip3(src: Path, d: float, W: int, H: int, fps: int, out: Path, fc: dict, lay: dict, start: float = 0.0,
          hold: bool = False, intermediate: list[str] | None = None) -> Path:
    """One scene of the recording in the 3-panel layout (hold/loop like `clip`)."""
    seek = ["-ss", f"{start:.2f}"] if start > 0 else []
    vf = filter_graph3(fc, lay, W, fps) + (f",tpad=stop_mode=clone:stop_duration={d:.3f}" if hold else "")
    run_ffmpeg(*([] if hold else ["-stream_loop", "-1"]), *seek, "-i", str(src), "-t", f"{d:.3f}", "-vf", vf,
               *(intermediate or ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-an"]), str(out))
    return out


def header_place(lay: dict, W: int, png_h: int = HEADER_PNG_H) -> tuple[str, int, int]:
    """(scale filter + "," or "", x, y) to put the editorial header PNG (W×png_h) in its band: centred, scaled down
    to the band's height when the band is shorter."""
    y, h = lay["header"]
    if h >= png_h:
        return "", 0, y + (h - png_h) // 2
    w = _even(W * h / png_h)
    return f"scale={w}:{h},", (W - w) // 2, y


def caption_margin3(H: int) -> int:
    """Word captions at the bottom of the gameplay panel (the camera and the header stay clean)."""
    return int(H * 0.035)


def most_expressive(frames) -> int:
    """Index of the camera frame that differs most from its usual look (the reaction), leaving out frames so
    different they are probably something else (he left the camera, a scene change)."""
    import numpy as np

    f = np.asarray(frames, dtype=np.float32)
    if len(f) < 3:
        return 0
    med = np.median(f, axis=0)
    score = np.abs(f - med).mean(axis=(1, 2))
    ok = np.where(score <= REACTION_MAX)[0]
    return int(ok[np.argmax(score[ok])]) if len(ok) else int(np.argmax(score))


def reaction_time(src: Path, fc: dict, start: float, end: float, n: int = 8) -> float:
    """The moment of the cut whose camera looks most like a reaction (start + 30% when it can't be measured)."""
    import subprocess

    import numpy as np

    from cvm.config import settings

    span = max(0.5, end - start)
    frames, kept = [], []
    for k in range(n):
        t = start + span * (0.08 + 0.84 * k / max(1, n - 1))
        r = subprocess.run([settings.ffmpeg_bin, "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                            "-vf", f"crop=iw*{_f(fc['w'])}:ih*{_f(fc['h'])}:iw*{_f(fc['x'])}:ih*{_f(fc['y'])},"
                                   "scale=64:36", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                           capture_output=True, timeout=60)
        if r.returncode == 0 and len(r.stdout) == 64 * 36:
            frames.append(np.frombuffer(r.stdout, dtype=np.uint8).reshape(36, 64))
            kept.append(t)
    if not frames:
        return start + span * 0.3
    return kept[most_expressive(frames)]


def reaction_cover(src: Path, fc: dict, t: float, W: int, H: int, out: Path) -> Path:
    """9:16 cover background: the game blurred on top (the cover words go there), the streamer's camera big below."""
    ch = _even(H * CAM_COVER)
    gh = H - ch
    blur = max(1, min(18, gh // 4, W // 4))
    run_ffmpeg("-ss", f"{max(0.0, t):.2f}", "-i", str(src), "-frames:v", "1", "-filter_complex",
               f"[0:v]split[a][b];[a]crop=iw*{_f(fc['w'])}:ih*{_f(fc['h'])}:iw*{_f(fc['x'])}:ih*{_f(fc['y'])},"
               f"scale={W}:{ch}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{ch},setsar=1[cam];"
               f"[b]scale={W}:{gh}:force_original_aspect_ratio=increase,crop={W}:{gh},boxblur={blur}:2,setsar=1[g];"
               f"[g][cam]vstack=inputs=2,drawbox=x=0:y={gh - LINE // 2}:w={W}:h={LINE}:color={ACCENT}:t=fill",
               "-pix_fmt", "yuvj420p", "-q:v", "2", str(out))
    return out
