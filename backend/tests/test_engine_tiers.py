"""Hardware tiers: the wizard's engine test (fallback to the next tier, avatar options all tested on small cards),
its endpoints, and every pipeline stage reading this machine's engine. No model runs: children, installs and
pipelines are faked; the DB is in memory; the heavy-model lock is a MemoryHuey lock."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from huey import MemoryHuey
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm import db, engines, gpu
from cvm.models import Setting
from cvm.setup import engine_test, install, requirements
from cvm.setup.requirements import Hardware

WIN8 = Hardware("windows", "NVIDIA GeForce RTX 5060", 32, 8, 900)
WIN24 = Hardware("windows", "NVIDIA GeForce RTX 4090", 64, 24, 900)
MAC48 = Hardware("mac", "Apple M5 Pro", 48, 0, 500)


@pytest.fixture
def mem(monkeypatch, tmp_path):
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    monkeypatch.setattr(db, "engine", e)
    monkeypatch.setattr(engine_test, "_engine", lambda: e)
    monkeypatch.setattr(gpu, "lock", MemoryHuey("tests-tiers").lock_task("gpu"))
    from cvm.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    # engines reads the real Setting here (conftest stubs it): saved choices and failed tests come from this DB
    monkeypatch.setattr(engines, "_stored", lambda: _row(e, "engines"))
    monkeypatch.setattr(engines, "_failed", _failed_from(e))
    monkeypatch.setattr(engines, "CACHE_S", 0.0)
    monkeypatch.setattr(engine_test, "_ensure_installed", lambda f, t, hw: None)
    engines.invalidate()
    return e


def _row(e, key):
    with Session(e) as s:
        r = s.get(Setting, key)
        return dict(r.value) if r and r.value else {}


def _failed_from(e):
    def failed(feature):
        res = (_row(e, engine_test.KEY).get(feature) or {}).get("results") or []
        return {r["engine"] for r in res if r.get("ok") is False}
    return failed


def runner_failing(bad: set[str], log: list):
    def run(feature, engine, folder, timeout):
        log.append((feature, engine))
        if engine in bad:
            return {"ok": False, "seconds": 3.0, "error": "Faltou memória na placa de vídeo."}
        return {"ok": True, "seconds": 12.5, "error": None}
    return run


# ---------------- the run ----------------

def test_failed_tier_falls_to_the_next_and_is_saved(mem, monkeypatch):
    monkeypatch.setattr(requirements, "_DETECTED", [WIN24])
    calls = []
    st = engine_test.run(["ai_images", "music"], hw=WIN24, runner=runner_failing({"flux-bf16"}, calls))
    assert calls == [("ai_images", "flux-bf16"), ("ai_images", "flux-8bit"), ("music", "acestep")]
    assert st["ai_images"]["chosen"] == "flux-8bit" and not st["running"]
    assert [r["ok"] for r in st["ai_images"]["results"]] == [False, True]
    assert st["ai_images"]["results"][0]["error"] == "Faltou memória na placa de vídeo."
    engines.invalidate()
    assert engines.feature_engine("ai_images") == "flux-8bit"  # the pipeline reads the tier that passed


def test_avatar_on_small_cards_tests_all_three_and_keeps_the_first_pass(mem, monkeypatch):
    monkeypatch.setattr(requirements, "_DETECTED", [WIN8])
    calls = []
    st = engine_test.run(["avatar"], hw=WIN8, runner=runner_failing({"ltx2-q"}, calls))
    assert calls == [("avatar", "ltx2-q"), ("avatar", "latentsync"), ("avatar", "musetalk")]
    assert st["avatar"]["chosen"] == "latentsync"
    assert {r["engine"]: r["ok"] for r in st["avatar"]["results"]} == {"ltx2-q": False, "latentsync": True,
                                                                        "musetalk": True}
    # the owner switches to MuseTalk after watching both; a failed one cannot be picked
    engine_test.choose("avatar", "musetalk")
    engines.invalidate()
    assert engines.feature_engine("avatar") == "musetalk"
    with pytest.raises(ValueError):
        engine_test.choose("avatar", "ltx2-q")


def test_nothing_passes_leaves_no_engine(mem, monkeypatch):
    monkeypatch.setattr(requirements, "_DETECTED", [WIN8])
    st = engine_test.run(["animation"], hw=WIN8, runner=runner_failing({"ltxv-2b"}, []))
    assert st["animation"]["chosen"] is None
    engines.invalidate()
    assert engines.feature_engine("animation") is None  # the pipeline keeps stills instead of failing


def test_install_failure_counts_as_a_failed_tier(mem, monkeypatch):
    def boom(f, tier, hw):
        if tier.engine == "flux-bf16":
            raise RuntimeError("No space left on device")
    monkeypatch.setattr(engine_test, "_ensure_installed", boom)
    st = engine_test.run(["ai_images"], hw=WIN24, runner=runner_failing(set(), []))
    assert st["ai_images"]["results"][0]["error"].startswith("Não deu para instalar: O disco encheu")
    assert st["ai_images"]["chosen"] == "flux-8bit"


def test_mac_runs_its_single_tier(mem):
    calls = []
    st = engine_test.run(["ai_images", "animation", "music", "avatar"], hw=MAC48, runner=runner_failing(set(), calls))
    assert calls == [("ai_images", "z-image"), ("animation", "ltx-mlx"), ("music", "acestep"), ("avatar", "ltx-mlx")]
    assert st["avatar"]["chosen"] == "ltx-mlx"


def test_spawn_turns_a_crash_into_plain_portuguese(monkeypatch, tmp_path):
    class P:
        returncode, stdout = 1, ""
        stderr = "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB"
    monkeypatch.setattr(engine_test.subprocess, "run", lambda *a, **k: P())
    r = engine_test.spawn("ai_images", "flux-bf16", tmp_path, 60)
    assert r["ok"] is False and r["error"] == "Faltou memória na placa de vídeo."

    def slow(*a, **k):
        raise engine_test.subprocess.TimeoutExpired(a[0], 60)
    monkeypatch.setattr(engine_test.subprocess, "run", slow)
    assert "interrompido" in engine_test.spawn("animation", "ltxv-2b", tmp_path, 60)["error"]


def test_output_checks(tmp_path):
    from PIL import Image
    black = tmp_path / "black.png"
    Image.new("RGB", (64, 64)).save(black)
    with pytest.raises(engine_test.Bad):
        engine_test.check_image(black)
    with pytest.raises(engine_test.Bad):
        engine_test.check_image(tmp_path / "missing.png")


def test_child_reports_json(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("cvm.db.init_db", lambda: None)
    monkeypatch.setattr(engine_test, "run_one", lambda f, e, d: (_ for _ in ()).throw(RuntimeError("CUDA out of memory")))
    assert engine_test.child("ai_images", "flux-nf4", str(tmp_path)) == 1
    assert '"ok": false' in capsys.readouterr().out


# ---------------- endpoints ----------------

@pytest.fixture
def client(mem, monkeypatch):
    from cvm.api import setup as setup_api
    from cvm.db import get_session

    app = FastAPI()
    app.include_router(setup_api.router)

    def sess():
        with Session(mem) as s:
            yield s
    app.dependency_overrides[get_session] = sess
    monkeypatch.setattr(setup_api, "detect", lambda: WIN8)
    monkeypatch.setattr(requirements, "_DETECTED", [WIN8])
    return TestClient(app)


def test_engine_endpoints(client, monkeypatch, mem):
    from cvm.workers import tasks

    queued = []
    monkeypatch.setattr(tasks, "engine_test_job", lambda feats: queued.append(feats))
    client.put("/setup", json={"step": "engines", "choices": {"features": ["base", "kids", "avatar"]}})
    r = client.get("/setup/engines").json()
    assert r["platform"] == "windows" and r["features"]["avatar"]["all_tested"] is True
    tiers = {t["engine"]: t for t in r["features"]["avatar"]["tiers"]}
    assert tiers["ltx2-cuda"]["available"] is False and tiers["ltx2-cuda"]["fits"] is False
    assert r["features"]["avatar"]["engine"] == "ltx2-q"
    # kids brings the image and animation tests; music was not chosen
    st = client.post("/setup/engines/test", json={}).json()
    assert queued == [["ai_images", "animation", "avatar"]] and st["running"] is True
    assert client.post("/setup/engines/test", json={}).status_code == 409  # never two at once
    # the worker ran: the person picks another avatar that passed
    engine_test.run(["avatar"], hw=WIN8, runner=runner_failing(set(), []))
    assert client.put("/setup/engines", json={"feature": "avatar", "engine": "musetalk"}).status_code == 200
    assert client.get("/setup/engines").json()["features"]["avatar"]["engine"] == "musetalk"
    assert client.put("/setup/engines", json={"feature": "music", "engine": "acestep"}).status_code == 409


def test_wizard_has_the_engines_step():
    from cvm.setup import state
    assert state.STEPS.index("engines") == state.STEPS.index("models") + 1


# ---------------- pipeline stages read the machine's engine ----------------

@pytest.fixture
def cuda(monkeypatch):
    """Pretend to be the owner's Windows PC (models_registry.MLX is read at call time by every reader)."""
    import cvm.models_registry as reg
    monkeypatch.setattr(reg, "MLX", False)
    monkeypatch.setattr(requirements, "_DETECTED", [WIN8])
    return monkeypatch


def test_images_use_the_flux_variant_of_the_tier(cuda, tmp_path):
    from cvm.visuals import flux_cuda, sources

    cuda.setattr(engines, "feature_engine", lambda f: {"ai_images": "flux-8bit"}[f])
    cuda.setattr(flux_cuda, "ready", lambda v: v == "flux-8bit")
    drawn = []
    cuda.setattr(flux_cuda, "draw", lambda v, p, out, w, h, steps=4, seed=None: drawn.append((v, w, h)) or out)
    assert sources.image_engine() == "flux"
    sources.ai_image("x", tmp_path / "a.png", portrait=True, seed=1)
    assert drawn == [("flux-8bit", 768, 1344)]
    cuda.setattr(flux_cuda, "ready", lambda v: False)
    assert sources.image_engine() is None  # no tier ready → no AI stills (stock instead), never a crash


def test_animation_uses_the_video_tier(cuda, tmp_path):
    from cvm.visuals import select, sources, video_cuda
    from cvm.config import settings

    cuda.setattr(engines, "feature_engine", lambda f: "ltxv-2b")
    made = []
    cuda.setattr(video_cuda, "clip", lambda eng, image, prompt, out, seconds, portrait=True, seed=42, steps=None:
                 made.append((eng, seconds)) or out)
    out, name = sources.animate_clip(tmp_path / "s.png", "move", tmp_path / "c.mp4", 3.0, portrait=False)
    assert made == [("ltxv-2b", 3.0)] and name == "ltx-video-2b"
    cuda.setattr(video_cuda, "ready", lambda e: e == "ltxv-2b")
    cuda.setattr(settings, "ai_video_max_clips", 2)
    assert select.ai_video_ready() is True
    cuda.setattr(engines, "feature_engine", lambda f: None)
    assert select.ai_video_ready() is False


def test_ltx2_clips_are_not_ready_yet(cuda, tmp_path):
    from cvm.visuals import video_cuda

    with pytest.raises(video_cuda.NotReady, match="1B"):
        video_cuda.clip("ltx2-cuda", tmp_path / "s.png", "x", tmp_path / "o.mp4", 3)


def test_avatar_uses_the_lipsync_tier(cuda, tmp_path):
    from cvm.avatar import lipsync, lipsync_cuda, ltx2_cuda

    base = tmp_path / "av" / "base" / "base.mp4"
    base.parent.mkdir(parents=True)
    base.write_bytes(b"x")
    cuda.setattr(engines, "feature_engine", lambda f: "latentsync")
    cuda.setattr(lipsync_cuda, "ready", lambda e: e == "latentsync")
    ran = []
    cuda.setattr(lipsync_cuda, "run", lambda eng, video, audio, out: ran.append(eng) or out)
    assert lipsync.wanted_engine(base) == "latentsync"
    _, eng = lipsync.lipsync(base, tmp_path / "a.wav", tmp_path / "o.mp4")
    assert eng == "latentsync" and ran == ["latentsync"]
    # the LTX-2 tier, when its tool and weights are there and the avatar has a photo
    photo = tmp_path / "av" / "photo" / "framed.png"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    cuda.setattr(engines, "feature_engine", lambda f: "ltx2-q")
    cuda.setattr(ltx2_cuda, "ready", lambda: True)
    cuda.setattr(ltx2_cuda, "talk", lambda still, audio, out: ran.append(("ltx2", still)) or out)
    assert lipsync.lipsync(base, tmp_path / "a.wav", tmp_path / "o2.mp4")[1] == "ltx2-a2v"
    assert ran[-1] == ("ltx2", photo)


def test_lipsync_base_video_from_the_photo_once(cuda, tmp_path):
    from cvm.avatar import lipsync_cuda

    photo = tmp_path / "av" / "photo" / "framed.png"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    missing = tmp_path / "av" / "base" / "base.mp4"
    import cvm.visuals.sources as sources
    cuda.setattr(sources, "animate_clip", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no engine")))
    made = []
    cuda.setattr(lipsync_cuda, "still_motion", lambda still, out, seconds=8.0: made.append(still) or out.write_bytes(b"v"))
    got = lipsync_cuda.base_video(missing)
    assert got == tmp_path / "av" / lipsync_cuda.BASE_NAME and made == [photo]
    assert lipsync_cuda.base_video(missing) == got and len(made) == 1  # cached per avatar


def test_lipsync_run_uses_the_tool_venv(cuda, tmp_path):
    from cvm.avatar import lipsync_cuda
    from cvm.config import settings

    cuda.setattr(settings, "models_dir", tmp_path / "models")
    cuda.setattr(lipsync_cuda, "ready", lambda e: True)
    cuda.setattr(lipsync_cuda, "duration", lambda p: 4.0)
    base = tmp_path / "base.mp4"
    base.write_bytes(b"x")
    seen = {}

    def fake(cmd, cwd=None, env=None, **k):
        seen.update(cmd=cmd, cwd=cwd, env=env)
        Path(cmd[cmd.index("--out") + 1]).write_bytes(b"v")

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()
    cuda.setattr(lipsync_cuda.subprocess, "run", fake)
    lipsync_cuda.run("musetalk", base, tmp_path / "a.wav", tmp_path / "o.mp4")
    assert seen["cmd"][1] == "cvm_musetalk.py" and Path(seen["cwd"]).name == "MuseTalk"
    assert seen["env"]["HF_HUB_OFFLINE"] == "1"


def test_music_offload_follows_the_tier(cuda, tmp_path):
    from cvm.render import music_gen

    cuda.setattr(engines, "feature_engine", lambda f: "acestep-offload")
    seen = {}

    def fake(cmd, cwd=None, env=None, **k):
        seen["env"] = env

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()
    cuda.setattr(music_gen.subprocess, "run", fake)
    music_gen.run_jobs([{"caption": "x", "seconds": 5, "seed": 1, "out": str(tmp_path / "m.wav")}])
    assert seen["env"]["CVM_ACE_OFFLOAD"] == "1"
    music_gen.run_jobs([], engine_id="acestep")
    assert seen["env"]["CVM_ACE_OFFLOAD"] == "0"


def test_ltx2_command_uses_fp8_and_ram_offload(cuda, tmp_path):
    from cvm.avatar import ltx2_cuda

    cuda.setattr(ltx2_cuda, "_snapshot", lambda model, need=None: tmp_path / model)
    cmd = ltx2_cuda.command(tmp_path / "s.wav", tmp_path / "a.png", "talk", 6.0, tmp_path / "o.mp4")
    assert cmd[1:3] == ["-m", "ltx_pipelines.a2vid_two_stage"]
    i = cmd.index("--quantization")
    assert cmd[i + 1] == "fp8-cast" and cmd[cmd.index("--offload") + 1] == "cpu"
    assert cmd[cmd.index("--image") + 1:cmd.index("--image") + 4] == [str(tmp_path / "a.png"), "0", "1.0"]


def test_windows_tool_paths(monkeypatch, tmp_path):
    import sys

    from cvm.setup import tools
    monkeypatch.setattr(sys, "platform", "win32")
    assert tools.venv_python(tmp_path / "X") == tmp_path / "X" / ".venv" / "Scripts/python.exe"
    for name in ("LatentSync", "MuseTalk", "LTX-2", "ACE-Step-1.5-cuda"):
        t = tools.TOOLS[name]
        assert t.platform == "windows" and len(t.commit) == 40
        # (sys.executable is the backend's own python, Scripts\python.exe on the PC)
        assert not any(".venv/bin" in " ".join(str(x) for x in c if x != sys.executable) for c in t.setup)
    for f in ("cvm_latentsync.py", "cvm_musetalk.py", "cvm_fetch.py"):
        assert (tools.VENDOR / f).exists()


def test_install_runs_a_tool_venv_python_by_absolute_path(monkeypatch, tmp_path):
    from cvm.config import settings
    from cvm.setup import tools

    monkeypatch.setattr(settings, "models_dir", tmp_path)
    ran = []
    monkeypatch.setattr(install, "_sh", lambda cmd, cwd=None: ran.append(cmd))
    (tmp_path / "MuseTalk" / ".git").mkdir(parents=True)
    install.install_tool("MuseTalk")
    fetches = [c for c in ran if "cvm_fetch.py" in c]
    assert fetches and all(Path(c[0]).is_absolute() and c[0].endswith("python.exe") for c in fetches)
    assert (tmp_path / "MuseTalk" / "cvm_fetch.py").exists()
    assert tools.TOOLS["MuseTalk"].ready() is False
