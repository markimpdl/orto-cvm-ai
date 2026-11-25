"""Wizard step "Teste dos motores" (owner, 2026-11-25: "Automático + teste"): after the models install, a short job per
feature runs ON THIS MACHINE with each tier that fits it, best first — one image, ~3 s of animation, ~5 s of music,
one sentence by the bundled Lucas avatar. Pass = the output file exists and is sane (not black, not silent, about
the right length). A tier that fails (out of memory, error, timeout) falls to the next one, whose items are installed
first when missing. The first tier that passes becomes the machine's engine (engines.set_feature_engine); the
pipeline reads it from there.

Avatar on Windows cards up to 16 GB: every option is tested (LTX-2 in FP8, LatentSync, MuseTalk) so the owner can
compare them, and the first that passes in that order is kept; the wizard lets him switch to another that passed.

Runs in the worker (tasks.engine_test_job), never in an API thread. Each tier runs in a child process
(`python -m cvm.setup.engine_test one <feature> <engine> <dir>`), so a CUDA out-of-memory crash or a hang is
contained and killed at the timeout; the model calls inside take gpu.section() exactly as the pipeline does.
Results: Setting "engine_tests" = {"running", "at", <feature>: {"chosen", "results": [{engine, label, ok, seconds,
error, at}]}}.
"""

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from cvm.config import REPO_ROOT, settings

KEY = "engine_tests"
TESTED = ("ai_images", "animation", "music", "avatar")  # kids has no engine of its own: it runs on images + animation
TIMEOUT_S = {"ai_images": 20 * 60, "animation": 60 * 60, "music": 30 * 60, "avatar": 2 * 3600}
ALL_AVATARS_VRAM = 16  # cards up to this size test every avatar option (owner's choice)
IMAGE_PROMPT = "a red vintage bicycle leaning on a white wall in the sun, photo"
CLIP_PROMPT = "the camera slowly pushes in, soft light moves across the scene"
CLIP_S, MUSIC_S, SPEECH_S = 3.0, 5.0, 4.0


# ---------------- state ----------------

def _engine():
    from cvm.db import engine

    return engine


def load() -> dict:
    from sqlmodel import Session

    from cvm.models import Setting

    with Session(_engine()) as s:
        row = s.get(Setting, KEY)
        return dict(row.value) if row and row.value else {}


def _save(value: dict) -> None:
    from sqlmodel import Session

    from cvm.models import Setting

    with Session(_engine()) as s:
        row = s.get(Setting, KEY) or Setting(key=KEY)
        row.value = value
        s.add(row)
        s.commit()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def platform() -> str:
    return "mac" if sys.platform == "darwin" else "windows"


def candidates(feature: str, hw) -> list:
    """Tiers of the feature that are available and fit this machine, best first."""
    from cvm.setup.requirements import fits, tiers

    return [t for t in tiers(feature, hw.platform) if t.available and fits(t, hw)]


def test_all(feature: str, hw) -> bool:
    return feature == "avatar" and hw.platform == "windows" and hw.vram_gb <= ALL_AVATARS_VRAM


# ---------------- plain pt-BR errors ----------------

def plain_error(text: str) -> str:
    t = (text or "").lower()
    if "out of memory" in t or "outofmemory" in t or "cuda error: out of memory" in t or "mps backend out of memory" in t:
        return "Faltou memória na placa de vídeo."
    if "timeout" in t or "passou do tempo" in t:
        return "Demorou demais e foi interrompido."
    if "401" in t or "403" in t or "gated" in t or "access to model" in t:
        return "O Hugging Face pediu login: aceite a licença do modelo no site e entre com hf auth login."
    if "no space left" in t:
        return "O disco encheu."
    tail = (text or "").strip().splitlines()[-1:] or [""]
    return f"Deu erro: {tail[0][:300]}" if tail[0] else "Deu erro sem mensagem."


# ---------------- checks of the output ----------------

class Bad(RuntimeError):
    pass


def _luma(image) -> tuple[float, float]:
    from PIL import ImageStat

    st = ImageStat.Stat(image.convert("L"))
    return st.mean[0], st.stddev[0]


def check_image(path: Path) -> None:
    from PIL import Image

    if not path.exists() or path.stat().st_size < 1000:
        raise Bad("a imagem não foi criada")
    mean, std = _luma(Image.open(path))
    if mean < 8 or std < 4:
        raise Bad("a imagem saiu preta ou lisa")


def check_video(path: Path, seconds: float) -> None:
    from PIL import Image

    from cvm.media import duration

    if not path.exists() or path.stat().st_size < 1000:
        raise Bad("o vídeo não foi criado")
    d = duration(path)
    if d < seconds * 0.6:
        raise Bad(f"o vídeo tem {d:.1f} s, esperado ~{seconds:g} s")
    frame = path.with_suffix(".check.png")
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-ss", f"{d / 2:.2f}", "-i", str(path), "-frames:v", "1",
                    str(frame)], check=True, capture_output=True)
    mean, std = _luma(Image.open(frame))
    frame.unlink(missing_ok=True)
    if mean < 8 or std < 3:
        raise Bad("o vídeo saiu preto")


def check_audio(path: Path, seconds: float) -> None:
    import re

    from cvm.media import duration

    if not path.exists() or path.stat().st_size < 1000:
        raise Bad("a música não foi criada")
    d = duration(path)
    if d < seconds * 0.7:
        raise Bad(f"a música tem {d:.1f} s, esperado ~{seconds:g} s")
    r = subprocess.run([settings.ffmpeg_bin, "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
    if m and float(m.group(1)) < -50:
        raise Bad("a música saiu muda")


# ---------------- one test (child process) ----------------

def _test_still(folder: Path) -> Path:
    """A synthetic input picture for the animation test (no model needed to make it)."""
    from PIL import Image, ImageDraw

    p = folder / "still.png"
    im = Image.new("RGB", (832, 480))
    d = ImageDraw.Draw(im)
    for y in range(480):
        d.line([(0, y), (832, y)], fill=(40 + y // 4, 90 + y // 6, 160))
    d.ellipse([330, 140, 500, 310], fill=(240, 200, 60))
    d.rectangle([0, 380, 832, 480], fill=(60, 120, 60))
    im.save(p)
    return p


def _lucas_sample(folder: Path) -> tuple[Path, Path]:
    """(base video, ~4 s of speech) of the bundled Lucas avatar, installing it when missing."""
    from sqlmodel import Session

    from cvm.setup import lucas

    with Session(_engine()) as s:
        av = lucas.install(s)
        base = Path(av.base_video_dir) / "base.mp4"
        refs = [Path(v.ref_audio_path) for v in lucas._voices(s, av) if v.ref_audio_path and Path(v.ref_audio_path).exists()]
    if not refs:
        raise Bad("o avatar Lucas não tem voz de referência")
    wav = folder / "speech.wav"
    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-i", str(refs[0]), "-t", f"{SPEECH_S}", "-ac", "1",
                    "-ar", "16000", str(wav)], check=True, capture_output=True)
    return base, wav


def run_one(feature: str, engine: str, folder: Path) -> Path:
    """Make the feature's test output with that engine and check it; raises on a bad result."""
    folder.mkdir(parents=True, exist_ok=True)
    mac = sys.platform == "darwin"
    if feature == "ai_images":
        out = folder / "image.png"
        if mac:
            from cvm.visuals import sources

            sources.ai_image(IMAGE_PROMPT, out, portrait=False, seed=7)
        else:
            from cvm import gpu
            from cvm.visuals import flux_cuda

            with gpu.section():
                flux_cuda.draw(engine, IMAGE_PROMPT, out, 1344, 768, seed=7)
        check_image(out)
        return out
    if feature == "animation":
        out = folder / "clip.mp4"
        still = _test_still(folder)
        if mac:
            from cvm.visuals import sources

            sources.animate_clip(still, CLIP_PROMPT, out, CLIP_S, portrait=False)
        else:
            from cvm.visuals import video_cuda

            video_cuda.clip(engine, still, CLIP_PROMPT, out, CLIP_S, portrait=False)
        check_video(out, CLIP_S)
        return out
    if feature == "music":
        from cvm.render import music_gen

        out = folder / "music.wav"
        proc = music_gen.run_jobs([{"caption": music_gen.CAPTIONS["explainer"] + ", instrumental, no vocals",
                                    "seconds": MUSIC_S, "seed": 1, "out": str(out)}], timeout=TIMEOUT_S["music"],
                                  engine_id=engine)
        if not out.exists():
            raise Bad((proc.stderr or proc.stdout or "")[-600:] or "a música não foi criada")
        check_audio(out, MUSIC_S)
        return out
    if feature == "avatar":
        from cvm.avatar.lipsync import lipsync

        base, wav = _lucas_sample(folder)
        out = folder / "avatar.mp4"
        forced = {"ltx2-q": "ltx2-a2v", "latentsync": "latentsync", "musetalk": "musetalk"}.get(engine)
        _, used = lipsync(base, wav, out, engine=forced)
        if used == "static":
            raise Bad("o motor do avatar não está instalado")
        check_video(out, SPEECH_S)
        return out
    raise ValueError(f"recurso sem teste: {feature!r}")


def child(feature: str, engine: str, folder: str) -> int:
    from cvm.db import init_db

    init_db()
    try:
        run_one(feature, engine, Path(folder))
        print(json.dumps({"ok": True}), flush=True)
        return 0
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"[-1500:]}), flush=True)
        return 1


# ---------------- the run (worker) ----------------

def spawn(feature: str, engine: str, folder: Path, timeout: int) -> dict:
    """One tier in a child process; {ok, seconds, error} (error in plain pt-BR)."""
    t0 = time.monotonic()
    cmd = [sys.executable, "-m", "cvm.setup.engine_test", "one", feature, engine, str(folder)]
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT / "backend", capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "seconds": round(time.monotonic() - t0, 1),
                "error": f"Demorou mais de {timeout // 60} min e foi interrompido."}
    seconds = round(time.monotonic() - t0, 1)
    last = next((json.loads(l) for l in reversed(proc.stdout.splitlines()) if l.startswith("{")), None)
    if last and last.get("ok"):
        return {"ok": True, "seconds": seconds, "error": None}
    raw = (last or {}).get("error") or proc.stderr or proc.stdout or f"código {proc.returncode}"
    return {"ok": False, "seconds": seconds, "error": plain_error(raw), "detail": raw[-1500:]}


def _ensure_installed(feature: str, tier, hw) -> None:
    """Install what this tier needs and is missing (the next tier after a failure, typically)."""
    from cvm.setup import install

    for it in install.plan([feature], hw.platform, hw=hw, saved={feature: tier.engine}):
        if it.state == "ready":
            continue
        install.install_tool(it.id) if it.kind == "tool" else install.install_model(it.id)
        if not install._ready(it.kind, it.id):
            raise RuntimeError(f"{it.id} terminou de instalar, mas os arquivos não ficaram completos")


def run(features: list[str], hw=None, runner=None) -> dict:
    """Test each feature's tiers on this machine (see the module doc). `runner` replaces spawn (tests)."""
    from cvm import engines
    from cvm.setup.requirements import detect

    hw = hw or detect()
    runner = runner or spawn
    todo = [f for f in TESTED if f in features]
    state = {**load(), "running": True, "at": _now(), "features": todo}
    _save(state)
    try:
        for f in todo:
            results, chosen = [], None
            state[f] = {"chosen": None, "running": True, "results": results}
            _save(state)
            for tier in candidates(f, hw):
                res = {"engine": tier.engine, "label": tier.label, "at": _now()}
                try:
                    _ensure_installed(f, tier, hw)
                except Exception as e:  # noqa: BLE001
                    res.update(ok=False, seconds=0, error=f"Não deu para instalar: {plain_error(str(e))}")
                else:
                    folder = settings.data_dir / "engine_test" / f / tier.engine
                    res.update(runner(f, tier.engine, folder, TIMEOUT_S[f]))
                results.append(res)
                if res["ok"] and chosen is None:
                    chosen = tier.engine
                    engines.set_feature_engine(f, chosen)
                state[f] = {"chosen": chosen, "running": True, "results": results}
                _save(state)
                if chosen and not test_all(f, hw):
                    break
            if chosen is None:
                engines.set_feature_engine(f, None)
            state[f] = {"chosen": chosen, "running": False, "results": results}
            _save(state)
    finally:
        state["running"] = False
        for f in todo:
            if isinstance(state.get(f), dict):
                state[f]["running"] = False
        _save(state)
    return state


def choose(feature: str, engine: str) -> dict:
    """The person picks another engine that passed its test (avatar selector)."""
    from cvm import engines

    st = load()
    res = (st.get(feature) or {}).get("results") or []
    if not any(r["engine"] == engine and r.get("ok") for r in res):
        raise ValueError("esse motor não passou no teste desta máquina")
    engines.set_feature_engine(feature, engine)
    st[feature] = {**st[feature], "chosen": engine}
    _save(st)
    return st


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[0] == "one":
        return child(argv[1], argv[2], argv[3])
    print("uso: python -m cvm.setup.engine_test one <recurso> <motor> <pasta>")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
