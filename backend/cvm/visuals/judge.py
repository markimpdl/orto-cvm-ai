"""Anti-slop pass: a vision model looks at every AI-made visual of a video before it goes into the edit.

Gemma 3 12B (vision, 4-bit; the same weights LTX-2.3 already uses as its text encoder) runs from
models/VLMJudge (its own venv with mlx-vlm, so the backend's MLX pins stay as they are). It scores each FLUX
still, and each LTX/Wan clip as a strip of three frames (start, middle, end), for deformed hands/faces, garbled
text and fake logos, plastic "AI art" looks, morphing, nonsense geometry and narration mismatch.

What happens on a fail (`review`):
- an animated clip goes back to its still (the composer still moves the camera over it);
- a still is drawn again with other seeds (up to RETRIES), then replaced by stock footage/photos for regular
  videos; kids episodes stay drawn (the best-scoring take is kept);
- verdicts are stored in Asset.meta["judge"] so the project page and render QA can show them.
Kids thumbnails (`pick_kids_thumbnail`, rule "kids_thumbnail"): the candidate scene images are judged for a
distorted face, an exaggerated open mouth, a scare or an upset character; the first one that passes becomes the
thumbnail background (YouTube's kids quality guidance; a big Arabic kids channel lost its reach over this).
Mac only for now (mlx-vlm); elsewhere, or without the model, the pass is skipped.
Known limit (calibration 2026-11-11): good at garbled text, deformed faces and narration mismatch on stills, and
no longer penalises cartoons; it does NOT reliably catch motion artefacts in clips (a morphing-hands LTX clip passed).
A clip whose subject leaves the frame or turns into another scene is caught before the judge, by
`clip_rank.steadiness` in `select._animate` (the judge gave such clips 10/10; 2026-11-16).
"""

import json
import logging
import subprocess
import tempfile
from pathlib import Path

from cvm.config import settings
from cvm.visuals.sources import is_ai_still

log = logging.getLogger(__name__)
JUDGE = settings.models_dir / "VLMJudge"
PYTHON = JUDGE / ".venv" / "bin" / "python"
MODEL = "mlx-community/gemma-3-12b-it-4bit"
PASS = 6.0  # score = 10 - sum(severity²) (judge.py v2): one severity-3 defect or two severity-2 fail; calibrated 2026-11-11 on 20 queue visuals
RETRIES = 2  # new FLUX seeds per failed still
SEVERE = ("hand", "finger", "limb", "face", "eye", "text", "logo", "letter", "morph", "melt", "extra", "merged",
          "flag", "bandeira")


def ready() -> bool:
    from cvm.models_registry import MLX
    from cvm.models_registry import HF_CACHE

    snap = HF_CACHE / ("models--" + MODEL.replace("/", "--")) / "snapshots"
    return bool(MLX and settings.visual_judge and PYTHON.exists() and (JUDGE / "judge.py").exists()
                and snap.exists() and any(snap.iterdir()))


def strip(video: Path, out: Path) -> Path | None:
    """Start, middle and end frames of a clip side by side, so the judge can see morphing."""
    probe = subprocess.run([str(settings.ffprobe_bin), "-v", "error", "-show_entries", "format=duration", "-of",
                            "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip()
    try:
        d = float(probe)
    except ValueError:
        return None
    ins = [x for t in (min(0.2, d / 4), d / 2, max(0.0, d - 0.3)) for x in ("-ss", f"{t:.2f}", "-i", str(video))]
    subprocess.run([str(settings.ffmpeg_bin), "-v", "error", "-y", *ins, "-filter_complex",
                    "[0]scale=-2:512[a];[1]scale=-2:512[b];[2]scale=-2:512[c];[a][b][c]hstack=3",
                    "-frames:v", "1", str(out)], capture_output=True)
    return out if out.exists() else None


KIDS_THUMB_TRIES = 6  # scene images checked for a kids thumbnail (one model load)
KIDS_WORDS = ("face", "mouth", "scar", "fright", "angry", "anger", "cry", "tear", "shock", "distort", "deform",
              "monster", "dark", "teeth", "boca", "rosto", "susto", "medo")


def kids_thumbnail_ok(r: dict) -> bool:
    """kids_thumbnail: stricter than a scene — any noticeable face/mouth/scare problem fails."""
    if r.get("score") is None:
        return True
    bad = [p for p in r.get("problems", []) if any(w in p.lower() for w in KIDS_WORDS) and not p.rstrip().endswith("[1]")]
    return r["score"] >= PASS and not bad


def pick_kids_thumbnail(images: list[Path], title: str) -> Path | None:
    """First image that passes the kids_thumbnail rule, or None when none does."""
    if not images:
        return None
    verdicts = judge([{"image": str(p), "context": title, "kind": "image", "rule": "kids_thumbnail"} for p in images],
                     cartoon=True)
    for p, v in zip(images, verdicts):
        if kids_thumbnail_ok(v):
            if p != images[0]:
                log.info("kids thumbnail: %s refused (%s), using %s", images[0].name, verdicts[0].get("problems"), p.name)
            return p
    log.warning("kids thumbnail: no scene image passed the kids rule; keeping the chosen scene")
    return None


def judge(items: list[dict], cartoon: bool = False) -> list[dict]:
    """items: [{image, context, kind: image|clip, rule?: "kids_thumbnail"}] → [{score, problems, ok}] (same order).
    One model load."""
    from cvm import gpu

    if not items:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        inp, out = Path(tmp) / "in.json", Path(tmp) / "out.json"
        inp.write_text(json.dumps([{**it, "style": "cartoon" if cartoon else "photo"} for it in items], ensure_ascii=False))
        with gpu.section():
            proc = subprocess.run([str(PYTHON), str(JUDGE / "judge.py"), "--model", MODEL, "--in", str(inp),
                                   "--out", str(out)], capture_output=True, text=True, timeout=60 * 30,
                                  env={"HF_HUB_OFFLINE": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())})
        if proc.returncode != 0 or not out.exists():
            log.warning("visual judge failed: %s", proc.stderr[-800:])
            return [{"ok": True, "score": None, "problems": ["juiz indisponível"]} for _ in items]
        res = json.loads(out.read_text())
    return [{**r, "ok": verdict_ok(r)} for r in res]


GAZE_AWAY = ("to the side", "up")  # a presenter looking anywhere but the lens (blinks and nods down are natural)


def gaze(faces: list[Path]) -> list[str | None]:
    """Where the presenter looks in each face crop ("at the camera", "to the side"…); one model load. None = unknown."""
    from cvm import gpu

    if not faces:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        inp, out = Path(tmp) / "in.json", Path(tmp) / "out.json"
        inp.write_text(json.dumps([{"image": str(f), "rule": "gaze"} for f in faces]))
        with gpu.section():
            proc = subprocess.run([str(PYTHON), str(JUDGE / "judge.py"), "--model", MODEL, "--in", str(inp),
                                   "--out", str(out)], capture_output=True, text=True, timeout=60 * 60,
                                  env={"HF_HUB_OFFLINE": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())})
        if proc.returncode != 0 or not out.exists():
            log.warning("gaze check failed: %s", proc.stderr[-800:])
            return [None] * len(faces)
        return [r.get("looking") for r in json.loads(out.read_text())]


def verdict_ok(r: dict) -> bool:
    if r.get("score") is None:  # unreadable answer: don't block the video on the judge's mistake
        return True
    if r["score"] < PASS:
        return False
    severe = [p for p in r.get("problems", []) if any(w in p.lower() for w in SEVERE)]
    return not (severe and r["score"] < PASS + 2)


def _flag_problem(verdict: dict) -> bool:
    return any(w in p.lower() for p in verdict.get("problems") or [] for w in ("flag", "bandeira", "banner"))


def review(chosen: list[dict], scenes: dict[int, dict], redo_still, fallback, cartoon: bool,
           work: Path) -> list[dict]:
    """chosen: one dict per scene (as built by select.source_visuals, with "_scene"). AI visuals are judged in one
    batch; failures are fixed as described above. `redo_still(scene, seed)` draws a new FLUX still,
    `fallback(scene)` finds stock for it. Returns the list with replacements."""
    work.mkdir(exist_ok=True)

    def item(c: dict) -> dict | None:
        sc = scenes[c["_scene"]]
        ctx = f"{sc.get('narration', '')} | {c.get('prompt') or ''}"
        if c.get("kind") == "video" and c.get("still"):
            s = strip(Path(c["path"]), work / f"strip_{c['_scene']:02d}.jpg")
            return {"image": str(s), "context": f"{ctx} | motion: {c.get('motion_prompt', '')}", "kind": "clip"} if s else None
        if c.get("kind") == "image" and is_ai_still(c.get("source")):
            return {"image": c["path"], "context": ctx, "kind": "image"}
        return None

    todo = [(i, it) for i, c in enumerate(chosen) if (it := item(c))]
    if not todo:
        return chosen
    verdicts = judge([it for _, it in todo], cartoon)
    out = list(chosen)
    again = []  # stills to draw again
    reverted = []  # clips sent back to their still
    for (i, it), v in zip(todo, verdicts):
        c = {**out[i], "judge": {k: v.get(k) for k in ("score", "problems", "ok")}}
        if not v["ok"] and it["kind"] == "clip":
            log.info("judge: scene %s clip rejected (%s %s), back to the still", c["_scene"], v["score"], v["problems"])
            still = {k: val for k, val in c.items() if k not in ("still", "still_source", "motion_prompt", "hold_last_frame")}
            c = {**still, "kind": "image", "path": c["still"], "source": c.get("still_source") or "flux-schnell (local)",
                 "judge": {**c["judge"], "action": "clipe trocado pela imagem"}}
            if (s := item(c)) is not None:
                reverted.append((i, s))  # the still itself still has to pass
        elif not v["ok"]:
            again.append((i, it, 0))
        out[i] = c
    # a flag the AI drew on its own is never redrawn by the AI: straight to real footage/photos
    flagged = [(i, it, r) for i, it, r in again if not cartoon and _flag_problem(out[i].get("judge", {}))]
    again = [x for x in again if x not in flagged]
    for (i, it), v in zip(reverted, judge([it for _, it in reverted], cartoon)):
        out[i] = {**out[i], "judge": {**out[i]["judge"], "still_score": v.get("score")}}
        if not v["ok"]:
            again.append((i, it, 0))
    for round_ in range(1, RETRIES + 1):
        if not again:
            break
        fresh = []
        for i, _, _ in again:
            sc = scenes[out[i]["_scene"]]
            if (new := redo_still(sc, 1000 * round_ + sc["index"])) is not None:
                fresh.append((i, {"image": new["path"], "context": f"{sc.get('narration', '')} | {new.get('prompt') or ''}",
                                  "kind": "image"}, new))
        vs = judge([it for _, it, _ in fresh], cartoon)
        again = []
        for (i, it, new), v in zip(fresh, vs):
            best = out[i].get("judge", {}).get("score") or 0
            if v["ok"]:
                out[i] = {**out[i], **new, "kind": "image", "judge": {**{k: v.get(k) for k in ("score", "problems", "ok")},
                                                                      "action": f"refeita (tentativa {round_})"}}
            else:
                if (v.get("score") or 0) > best:  # keep the better take in case nothing passes
                    out[i] = {**out[i], **new, "kind": "image", "judge": {**{k: v.get(k) for k in ("score", "problems", "ok")}}}
                again.append((i, it, round_))
    for i, _, _ in again + flagged:  # nothing passed
        sc = scenes[out[i]["_scene"]]
        if not cartoon and (stock := fallback(sc)):
            out[i] = {**stock, "_scene": out[i]["_scene"], "type": out[i].get("type"),
                      "judge": {**out[i].get("judge", {}), "action": "trocada por vídeo/foto real"}}
        else:
            out[i]["judge"] = {**out[i].get("judge", {}), "action": "mantida (melhor tentativa)"}
    return out
