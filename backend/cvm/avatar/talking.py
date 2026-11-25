"""Talking presenter generated straight from the narration audio (LTX audio-to-video, distilled, MLX).

Unlike lipsync (a model redraws only the mouth over a looping base video), the whole person moves with the speech:
lips, blinks, eyebrows, head nods and hand gestures. The avatar's photo still fixes the identity. Long narrations
are generated in chunks of up to ~9.5 s cut at pauses; each chunk starts on the previous chunk's last frame, so the
joins don't jump, and is guided back to the photo still at its end, so the face doesn't drift. Cost on the M5 Pro: roughly 40–70 s of GPU per second of speech.

On the LTX-2.5 pack (settings.ltx_pack) the take uses 6 refine steps and a teeth/lips prompt, and each chunk is then
passed through the official Refine-Details IC-LoRA (`_refine`, settings.presenter_refine; the user's choice on
2026-11-16 after bakeoff/ltx25_refine). That pass follows the take's own motion, so lip sync is kept; it costs about
24 min more per ~6 s chunk (measured with the Mac in use), i.e. a chunk takes ~35 min instead of ~8–11.
"""

import contextvars
import logging
import math
import os
import subprocess
from pathlib import Path

from cvm.config import settings
from cvm.media import duration

log = logging.getLogger(__name__)

PROMPT = ("The person talks naturally and confidently to the camera like a YouTube presenter, eyes looking straight into "
          "the camera lens the whole time, lips moving in sync with the words, natural blinking, small head nods on "
          "emphasis, eyebrows move with the speech, calm hand gestures at chest height, static tripod camera, realistic, "
          "natural skin texture")
MOUTH_25 = ", clear natural teeth, well-defined lips, sharp mouth detail"  # LTX-2.5 draws a softer mouth than 2.3
STAGE2_STEPS_25 = 6
FPS, W, H = 24, 1024, 576
MAX_CHUNK, MIN_CHUNK = 9.5, 4.0
IDENTITY_GUIDE = 0.8  # strength of the photo still as a guide on each chunk's last frame


def still_for(base_video: Path) -> Path | None:
    """The avatar's identity image: the framed photo it was made from, else a frame of its base video."""
    if base_video.parent.name == "podcast":  # the podcast desk shot (data/avatars/<id>/podcast), not the plain photo
        candidates = (base_video.parent / "still.png", base_video.parent / "still.jpg")
    else:
        candidates = (base_video.parent.parent / "photo" / "framed.png", base_video.parent / "still.jpg")
    for p in candidates:
        if p.exists():
            return p
    return None


def cut_points(audio: Path) -> list[float]:
    """Chunk boundaries (seconds) of at most MAX_CHUNK, preferring the middle of a pause."""
    from cvm.avatar.capture import silences

    total = duration(audio)
    pauses = [(a + b) / 2 for a, b in silences(audio)]
    cuts, start = [], 0.0
    while total - start > MAX_CHUNK:
        inside = [p for p in pauses if start + MIN_CHUNK <= p <= start + MAX_CHUNK]
        nxt = max(inside) if inside else start + MAX_CHUNK
        cuts.append(round(nxt, 3))
        start = nxt
    return [0.0, *cuts, total]


def _frames(seconds: float) -> int:
    n = math.ceil(seconds * FPS) + 1
    return (n - 1 + 7) // 8 * 8 + 1  # LTX needs 8k+1 frames


REFINE_MIN_FREE = 0.12  # the refine pass is stopped below this share of free memory (it froze the 48 GB Mac at 4–10 %)


class MemoryGuard(RuntimeError):
    """A guarded LTX run was stopped because the Mac was running out of memory."""


def free_ratio() -> float:
    import psutil

    vm = psutil.virtual_memory()
    return vm.available / vm.total


def _run_ltx(cmd: list[str], timeout: int = 3600, guard: bool = False) -> None:
    """Run ltx-2-mlx; when it fails (usually Metal memory, ~21 GB peak at decode while the worker holds other
    models), retry once with --low-ram before giving up — giving up fails the step (no MuseTalk fallback any more).
    guard=True (the optional refine pass): watch free memory and stop the run below REFINE_MIN_FREE instead of
    letting it swap the Mac into a freeze (2026-11-22); raises MemoryGuard, no retry."""
    import time

    for extra in ([], ["--low-ram"]):
        proc = subprocess.Popen(cmd + extra, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env={**os.environ, "HF_HUB_OFFLINE": "1"})
        t0 = time.monotonic()
        while proc.poll() is None:
            if guard and (ratio := free_ratio()) < REFINE_MIN_FREE:
                proc.kill()
                proc.wait()
                raise MemoryGuard(f"memória livre {ratio:.0%}: passada interrompida para não travar o Mac")
            if time.monotonic() - t0 > timeout:
                proc.kill()
                proc.wait()
                raise subprocess.TimeoutExpired(cmd, timeout)
            time.sleep(2)
        out, err = proc.communicate()
        if not proc.returncode:
            return
        log.warning("ltx-2-mlx %s exit %s%s: %s", cmd[1], proc.returncode, " (low-ram)" if extra else "", (err or out)[-1500:])
        if guard:
            break
    raise RuntimeError(f"ltx-2-mlx {cmd[1]} exit {proc.returncode}: {(err or out)[-1500:]}")


# set per video by the composer: projects listed in the Setting "presenter_refine_off" skip the refine pass
# (~8 min per chunk instead of ~35; user's choice for the GTA 6 Shorts, 2026-11-19)
NO_REFINE: contextvars.ContextVar[bool] = contextvars.ContextVar("presenter_no_refine", default=False)


def refine_off(session, project_id: int) -> bool:
    from cvm.models import Setting

    row = session.get(Setting, "presenter_refine_off")
    return project_id in ((row.value or {}).get("projects") or []) if row else False


def engine_mode() -> str:
    """"2.3", "2.5" or "2.5-refined": the LTX pack the presenter is generated with, and whether chunks are refined."""
    from cvm.visuals.sources import ltx_refine_lora, ltx_version

    v = ltx_version() or "2.3"
    refine = settings.presenter_refine and not NO_REFINE.get()
    return "2.5-refined" if v == "2.5" and refine and ltx_refine_lora() else v


def _refine(bin_: Path, raw: Path, anchor: Path, prompt: str, seconds: float) -> bool:
    """Refine-Details pass over a finished LTX-2.5 chunk (single stage, the take as control the whole way, so the
    lips keep their sync). The refined clip replaces `raw`; on any failure the plain take stays."""
    from cvm import gpu
    from cvm.visuals.sources import _ltx_model, ltx_refine_lora

    out = raw.with_name(f"{raw.stem}_refined.mp4")
    try:
        with gpu.section():
            _run_ltx([str(bin_), "ic-lora", "--model", str(_ltx_model()), "--lora", str(ltx_refine_lora()), "1.0",
                      "--video-conditioning", str(raw), "1.0", "--image", str(anchor), "0", "1.0",
                      "-p", prompt, "--single-stage", "-H", str(H), "-W", str(W), "-f", str(_frames(seconds)),
                      "--frame-rate", str(FPS), "--seed", "42", "-o", str(out)], timeout=3 * 3600, guard=True)
        if not out.exists() or motion(out) < STATIC_MOTION <= motion(raw):  # never trade a talking take for a frozen one
            raise RuntimeError("refined clip missing or frozen")
        out.replace(raw)
        return True
    except Exception as e:  # noqa: BLE001
        if isinstance(e, MemoryGuard):
            NO_REFINE.set(True)  # the rest of this video's chunks skip the pass: the next one would freeze the Mac too
        log.warning("ltx refine failed on %s, keeping the plain take: %s", raw.name, e)
        out.unlink(missing_ok=True)
        return False


STATIC_MOTION = 0.15  # mean frame difference below which a take came out frozen (talking takes measure ~0.3–0.9)
RETRY_SEEDS = (42, 7, 2026)  # first is the default; a frozen take is redone with the next one, the liveliest is kept


def motion(video: Path) -> float:
    """Mean luma difference between consecutive frames (160 px wide): ~0.1 for a frozen face, 0.3+ when talking."""
    r = subprocess.run([settings.ffmpeg_bin, "-v", "info", "-i", str(video), "-vf",
                        "scale=160:-2,tblend=all_mode=difference,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                        "-f", "null", "-"], capture_output=True, text=True)
    vals = [float(l.rsplit("=", 1)[1]) for l in r.stderr.splitlines() if "signalstats.YAVG=" in l]
    return sum(vals) / len(vals) if vals else 0.0


GAZE_STEP_S = 1.0  # one face crop per second of each chunk (a side glance lasts ~1–2 s)
GAZE_MAX_AWAY = 1  # more sampled frames than this looking away = the chunk is redone once with other seeds
GAZE_SEEDS = (1234, 99)


def _chunk(bin_: Path, seg: Path, anchor: Path, still: Path, prompt: str, seconds: float, raw: Path,
           seeds: tuple[int, ...]) -> float:
    """One LTX a2v take of a chunk into `raw`; LTX sometimes returns the photo barely moving (mouth shut) for a
    seed, so a frozen take is redone with the next seed and the liveliest is kept. Returns its motion."""
    from cvm import gpu
    from cvm.visuals.sources import _ltx_model, ltx_version

    v25 = ltx_version() == "2.5"
    best, takes = None, []
    for seed in seeds:
        take = raw.with_name(f"{raw.stem}_s{seed}.mp4")
        with gpu.section():
            _run_ltx([str(bin_), "a2v", "--distilled", "--model", str(_ltx_model()), "--audio", str(seg),
                      "--image", str(anchor), "0", "1.0",
                      # the identity still also guides the chunk's end, so the face never drifts over a long
                      # narration (chained last frames alone let it slowly change: rounder face by the end)
                      "--image", str(still), "last", str(IDENTITY_GUIDE), "guide",
                      "-p", prompt, "-H", str(H), "-W", str(W), *(["--stage2-steps", str(STAGE2_STEPS_25)] if v25 else []),
                      "-f", str(_frames(seconds)), "--frame-rate", str(FPS), "--seed", str(seed), "-o", str(take)])
        m = motion(take)
        takes.append(take)
        log.info("ltx talk %s seed %d motion %.2f", raw.name, seed, m)
        if best is None or m > best[0]:
            best = (m, take)
        if m >= STATIC_MOTION:
            break
    best[1].replace(raw)
    for t in takes:
        t.unlink(missing_ok=True)
    return best[0]


def _encode_part(raw: Path, seconds: float, part: Path) -> None:
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-i", str(raw), "-t", f"{seconds:.3f}", "-an",
                    "-vf", "scale=1920:1080:flags=lanczos,fps=25,setsar=1", "-c:v", "libx264", "-crf", "16",
                    "-pix_fmt", "yuv420p", str(part)], check=True, capture_output=True)


def face_box(still: Path) -> tuple[float, float, float, float]:
    """(x, y, w, h) of the face as fractions of the frame, with room around it; the tripod shot keeps it there.
    Falls back to the top middle of the frame when OpenCV is missing or finds no face."""
    try:
        import cv2

        img = cv2.imread(str(still))
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        found = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml").detectMultiScale(
            gray, 1.1, 5, minSize=(40, 40))
        if len(found):
            x, y, w, h = max(found, key=lambda f: f[2] * f[3])
            H_, W_ = gray.shape
            pad = 0.35
            x0, y0 = max(0, x - w * pad), max(0, y - h * pad)
            x1, y1 = min(W_, x + w * (1 + pad)), min(H_, y + h * (1 + pad))
            return x0 / W_, y0 / H_, (x1 - x0) / W_, (y1 - y0) / H_
    except Exception:  # noqa: BLE001
        pass
    return 0.33, 0.0, 0.34, 0.55


def _away_frames(raw: Path, seconds: float, box: tuple[float, float, float, float], tmp: Path) -> list[Path]:
    x, y, w, h = box
    crops = []
    t = min(0.5, seconds / 2)
    while t < seconds:
        f = tmp / f"{raw.stem}_{t:05.1f}.png"
        subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(raw), "-frames:v", "1",
                        "-vf", f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},scale=512:-2", str(f)],
                       capture_output=True)
        if f.exists():
            crops.append(f)
        t += GAZE_STEP_S
    return crops


def _fix_gaze(work: Path, bounds: list[float], still: Path, prompt: str, bin_: Path, refined: bool = False) -> None:
    """The presenter must look into the lens: LTX now and then turns the eyes to the side for a second or two
    (235/236 en/ar, full-frame). Face crops of every chunk go through the vision judge in one load; a chunk with
    more than GAZE_MAX_AWAY frames looking away is redone once with other seeds, and kept only if it improves."""
    import tempfile

    from cvm.visuals import judge

    if not (settings.visual_judge and judge.ready()):
        return
    box = face_box(still)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        chunks = [(i, a, b) for i, (a, b) in enumerate(zip(bounds, bounds[1:])) if not (work / f"gaze_ok_{i:02d}").exists()]
        crops = {i: _away_frames(work / f"raw_{i:02d}.mp4", b - a, box, tmp) for i, a, b in chunks}
        flat = [c for i, _, _ in chunks for c in crops[i]]
        looks = iter(judge.gaze(flat))
        away = {i: sum(1 for _ in crops[i] if next(looks) in judge.GAZE_AWAY) for i, _, _ in chunks}
        bad = [(i, a, b) for i, a, b in chunks if away[i] > GAZE_MAX_AWAY]
        for i, _, _ in chunks:
            if away[i] <= GAZE_MAX_AWAY:
                (work / f"gaze_ok_{i:02d}").touch()
        if not bad:
            return
        log.warning("presenter gaze: %d chunk(s) look away from the camera, redoing: %s", len(bad),
                    ", ".join(f"{i + 1} ({away[i]} frames)" for i, _, _ in bad))
        for i, a, b in bad:
            raw, seg = work / f"raw_{i:02d}.mp4", work / f"seg_{i:02d}.wav"
            note = work / f"anchor_in_{i:02d}.txt"
            anchor = Path(note.read_text()) if note.exists() and Path(note.read_text()).exists() else still
            redo = raw.with_name(f"redo_{i:02d}.mp4")
            _chunk(bin_, seg, anchor, still, prompt, b - a, redo, GAZE_SEEDS)
            again = sum(1 for x in judge.gaze(_away_frames(redo, b - a, box, tmp)) if x in judge.GAZE_AWAY)
            log.info("presenter gaze: chunk %d redo looks away in %d frames (was %d)", i + 1, again, away[i])
            if again < away[i]:
                redo.replace(raw)
                if refined:
                    _refine(bin_, raw, anchor, prompt, b - a)
                _encode_part(raw, b - a, work / f"part_{i:02d}.mp4")
            else:
                redo.unlink(missing_ok=True)
            (work / f"gaze_ok_{i:02d}").touch()


def ltx_talk(still: Path, audio: Path, out: Path, prompt: str = PROMPT) -> Path:
    ff = settings.ffmpeg_bin
    work = out.parent / f"{out.stem}_ltx"
    # chunks are cached by index: only valid for the same audio + identity still, else start over
    import hashlib
    import shutil

    mode = engine_mode()
    refined = mode == "2.5-refined"
    if mode != "2.3":
        prompt += MOUTH_25
    # the engine is part of the stamp (2.3 keeps the old one): chunks of two engines never mix in one presenter
    stamp = hashlib.sha1(audio.read_bytes() + str(still).encode() + str(IDENTITY_GUIDE).encode()
                         + (b"" if mode == "2.3" else mode.encode())).hexdigest()
    if work.exists() and (work / "stamp").exists() and (work / "stamp").read_text() != stamp:
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    if not (work / "stamp").exists():
        if any(work.glob("raw_*.mp4")) or any(work.glob("part_*.mp4")):  # made before stamps existed: unknown audio
            for f in [*work.glob("raw_*.mp4"), *work.glob("part_*.mp4")]:
                f.unlink()
        (work / "stamp").write_text(stamp)
    bounds = cut_points(audio)
    anchor, parts = still, []
    bin_ = settings.models_dir / "LTX2MLX" / ".venv" / "bin" / "ltx-2-mlx"
    from cvm import progress

    chunks = len(bounds) - 1
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        progress.report(i, chunks, f"apresentador falando (LTX {mode.replace('-refined', ' refinado')}): trecho {i + 1}/{chunks}, "
                                   f"~{35 if refined else 8} min cada")
        seg, raw, part = work / f"seg_{i:02d}.wav", work / f"raw_{i:02d}.mp4", work / f"part_{i:02d}.mp4"
        subprocess.run([ff, "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(audio),
                        # LTX rounds the clip up to 8k+1 frames and refuses audio shorter than that: pad with silence
                        "-af", "apad=pad_dur=1", str(seg)],
                       check=True, capture_output=True)
        if not raw.exists():
            _chunk(bin_, seg, anchor, still, prompt, b - a, raw, RETRY_SEEDS)
            (work / f"anchor_in_{i:02d}.txt").write_text(str(anchor))  # for a gaze redo of this chunk
        done = work / f"refined_{i:02d}"  # tried once per chunk, also after a restart in the middle of the pass
        if refined and not done.exists():
            _refine(bin_, raw, anchor, prompt, b - a)
            done.touch()
        _encode_part(raw, b - a, part)
        nxt = work / f"anchor_{i:02d}.png"  # the next chunk continues from this chunk's last frame
        subprocess.run([ff, "-v", "error", "-y", "-sseof", "-0.1", "-i", str(raw), "-frames:v", "1", "-update", "1", str(nxt)],
                       check=True, capture_output=True)
        anchor = nxt if nxt.exists() else still
        parts.append(part)
        log.info("ltx talk chunk %d/%d (%.1fs)", i + 1, len(bounds) - 1, b - a)
    _fix_gaze(work, bounds, still, prompt, bin_, refined)
    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run([ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", str(out)],
                   check=True, capture_output=True)
    return out
