"""Install wizard: background installer for tools and models. No git, uv or network here: subprocess and the
downloads are faked, and every path points into tmp_path."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm import models_registry
from cvm.config import settings
from cvm.models import Setting
from cvm.setup import install, tools


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(install, "_engine", lambda: engine)
    monkeypatch.setattr(settings, "models_dir", tmp_path / "models")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "ltx_pack", "2.5")
    monkeypatch.setattr(models_registry, "HF_CACHE", tmp_path / "hf")

    ready: set[str] = set()
    calls: dict[str, list] = {"download": [], "hf": [], "run": []}
    monkeypatch.setattr(models_registry, "is_ready", lambda n: n in ready)

    def download(names):
        calls["download"].append(list(names))
        ready.update(names)
    monkeypatch.setattr(models_registry, "download", download)

    def hf(mid):
        calls["hf"].append(mid)
        ready.add(mid)
    monkeypatch.setattr(install, "_hf_download", hf)
    monkeypatch.setattr(install, "_extra_ready", lambda mid: mid in ready)

    def fake_run(cmd, **kw):
        calls["run"].append([str(c) for c in cmd])
        if list(cmd[:2]) == ["git", "clone"]:
            (Path(cmd[3]) / ".git").mkdir(parents=True)

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()
    monkeypatch.setattr(install.subprocess, "run", fake_run)
    return engine, ready, calls


def ids(items):
    return {i.id for i in items}


def test_plan_lists_only_selected_features_for_platform(env):
    assert ids(install.plan(["base"], "mac")) == {"voxcpm2", "whisper", "clip"}


def test_plan_resolves_ltx_pack_at_call_time(env, monkeypatch):
    monkeypatch.setattr(settings, "ltx_pack", "2.3")
    got = ids(install.plan(["animation"], "mac"))
    assert "ltx-2.3" in got and "ltx-2.5" not in got
    assert {"LTX2MLX", "gemma"} <= got  # the LTX text encoder (sources.ltx_ready) comes along


def test_plan_items_carry_size_and_state(env):
    _, ready, _ = env
    ready.add("whisper")
    items = {i.id: i for i in install.plan(["base"], "mac")}
    assert items["whisper"].state == "ready" and items["voxcpm2"].state == "missing"
    assert items["whisper"].kind == "model" and items["whisper"].size_gb == pytest.approx(1.6)


def _win(vram):
    from cvm.setup.requirements import Hardware
    return Hardware("windows", "RTX", 32, vram, 900)


def test_plan_windows_follows_the_card(env):
    feats = ["base", "ai_images", "avatar", "animation", "kids", "music"]
    small = install.plan(feats, "windows", hw=_win(8), saved={})
    assert ids(small) == {"voxcpm2", "whisper", "clip", "flux-schnell", "ltxv-2b", "LTX-2", "ltx2.3-dev", "gemma3-qat",
                          "ACE-Step-1.5-cuda"}
    mid = install.plan(feats, "windows", hw=_win(12), saved={})
    assert {"flux-schnell-bf16", "wan-ti2v-5b"} <= ids(mid) and not {"flux-schnell", "ltxv-2b"} & ids(mid)
    # the engine test chose LatentSync for the avatar: its tool replaces LTX-2's items
    picked = install.plan(feats, "windows", hw=_win(8), saved={"avatar": "latentsync"})
    assert "LatentSync" in ids(picked) and not {"LTX-2", "ltx2.3-dev", "gemma3-qat"} & ids(picked)
    # no Mac-only tool on Windows
    assert not ids(small) & {"LTX2MLX", "VLMJudge", "gemma", "seedvr2", "ACE-Step-1.5"}


def test_plan_rejects_unknown_names(env):
    with pytest.raises(ValueError):
        install.plan(["nope"], "mac")


def test_install_resumes_only_missing(env):
    _, ready, calls = env
    ready.update({"voxcpm2", "clip"})
    assert install.run(["base"]) == 0
    assert calls["download"] == [["whisper"]]


def test_run_accepts_model_ids_like_the_old_script(env):
    _, _, calls = env
    install.run(["whisper"])
    assert calls["download"] == [["whisper"]]


def test_failed_item_keeps_others(env, monkeypatch):
    _, ready, calls = env
    ready.add("voxcpm2")

    def download(names):
        if names == ["whisper"]:
            raise OSError("connection reset")
        ready.update(names)
    monkeypatch.setattr(models_registry, "download", download)
    assert install.run(["base"]) == 1
    items = {i["id"]: i for i in install.status()["items"]}
    assert items["whisper"]["state"] == "failed"
    assert "Não foi possível" in items["whisper"]["error"] and "connection reset" in items["whisper"]["error"]
    assert items["clip"]["state"] == "ready"


def test_download_that_leaves_files_incomplete_fails(env, monkeypatch):
    monkeypatch.setattr(models_registry, "download", lambda names: None)  # "finished" but nothing is ready
    install.run(["whisper"])
    item = install.status()["items"][0]
    assert item["state"] == "failed" and item["error"]


def test_tool_cloned_at_pinned_commit(env):
    _, _, calls = env
    install.install_tool("LTX2MLX")
    t = tools.TOOLS["LTX2MLX"]
    partial = str(settings.models_dir / "LTX2MLX.partial")
    assert ["git", "clone", t.repo_url, partial] in calls["run"]
    assert ["git", "-C", partial, "checkout", "895201df379b079c2154738bcf92005b7a371923"] in calls["run"]
    assert (settings.models_dir / "LTX2MLX" / ".git").exists() and not Path(partial).exists()
    assert ["uv", "sync", "--python", "3.11"] in calls["run"]


def test_killed_clone_is_started_over(env, monkeypatch):
    _, _, calls = env
    partial = settings.models_dir / "RobustVideoMatting.partial"
    (partial / ".git").mkdir(parents=True)  # a clone SIGKILLed halfway, objects missing
    (partial / ".git" / "junk").write_text("x")
    install.install_tool("RobustVideoMatting")
    assert any(c[:2] == ["git", "clone"] for c in calls["run"])
    assert not partial.exists() and (settings.models_dir / "RobustVideoMatting" / ".git").exists()
    assert not (settings.models_dir / "RobustVideoMatting" / ".git" / "junk").exists()


def test_failed_checkout_leaves_no_clone_in_place(env, monkeypatch):
    real = install.subprocess.run

    def run(cmd, **kw):
        r = real(cmd, **kw)
        if "checkout" in cmd:
            r.returncode, r.stderr = 128, "fatal: reference is not a tree"
        return r
    monkeypatch.setattr(install.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="not a tree"):
        install.install_tool("LTX2MLX")
    assert not (settings.models_dir / "LTX2MLX").exists()  # the retry clones again


def test_tool_commits_are_full_shas():
    for name in ("LTX2MLX", "RobustVideoMatting", "ACE-Step-1.5"):
        assert len(tools.TOOLS[name].commit) == 40
    assert tools.TOOLS["VLMJudge"].repo_url is None


def test_tool_copies_our_glue_script(env):
    _, _, calls = env
    install.install_tool("VLMJudge")
    folder = settings.models_dir / "VLMJudge"
    assert (folder / "judge.py").read_text() == (tools.VENDOR / "judge.py").read_text()
    assert not any(c[0] == "git" for c in calls["run"])
    assert any("mlx-vlm==0.7.12" in c for c in calls["run"])


def test_tool_refuses_a_foreign_folder(env):
    folder = settings.models_dir / "RobustVideoMatting"
    folder.mkdir(parents=True)
    (folder / "something").write_text("x")
    with pytest.raises(RuntimeError, match="já existe"):
        install.install_tool("RobustVideoMatting")


def test_tool_reinstall_reuses_existing_clone(env):
    _, _, calls = env
    (settings.models_dir / "ACE-Step-1.5" / ".git").mkdir(parents=True)
    install.install_tool("ACE-Step-1.5")
    assert not any(c[:2] == ["git", "clone"] for c in calls["run"])
    assert any(c[-2:] == ["checkout", tools.TOOLS["ACE-Step-1.5"].commit] for c in calls["run"])
    assert (settings.models_dir / "ACE-Step-1.5" / "cvm_music.py").exists()


def test_stale_pid_resets_running(env, monkeypatch):
    engine, _, _ = env
    with Session(engine) as s:
        s.add(Setting(key="setup_install", value={"pid": 999999, "at": "x", "items": [
            {"kind": "model", "id": "whisper", "size_gb": 1.6, "state": "running", "error": None},
            {"kind": "model", "id": "clip", "size_gb": 0.6, "state": "failed", "error": "Não foi possível: x"}]}))
        s.commit()
    monkeypatch.setattr(install, "_alive", lambda pid: False)
    st = install.status()
    items = {i["id"]: i for i in st["items"]}
    assert st["running"] is False
    assert items["whisper"]["state"] == "missing"
    assert items["clip"]["state"] == "failed"  # a recorded failure keeps its message for the wizard


def test_alive_sees_this_process():
    import os

    assert install._alive(os.getpid()) is True
    assert install._alive(None) is False


def test_start_spawns_detached_run_once(env, monkeypatch):
    spawned = []

    class P:
        pid = 4242

        def poll(self):
            return None

    def popen(cmd, **kw):
        spawned.append((cmd, kw))
        return P()
    monkeypatch.setattr(install.subprocess, "Popen", popen)
    install.start(["base"])
    cmd, kw = spawned[0]
    assert cmd[1:] == ["-m", "cvm.setup.install", "run", "base"] and kw["start_new_session"] is True
    st = install.status()
    assert st["running"] is True and {i["id"] for i in st["items"]} == {"voxcpm2", "whisper", "clip"}
    install.start(["base"])  # a live run is not started twice
    assert len(spawned) == 1


def test_windows_wizard_install_runs_detached_and_offers_no_mac_tools(env, monkeypatch):
    # the wizard on a Windows PC: start() must not crash (no start_new_session there) and only the CUDA models of the
    # basic install are listed; Mac-only tools (LTX2MLX, VLMJudge, MLX Gemma) are never offered
    import sys

    from cvm.setup import requirements

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(install, "PLATFORM", "windows")
    monkeypatch.setattr(requirements, "_DETECTED", [_win(8)])  # the owner's RTX 5060
    spawned = []

    class P:
        pid = 4343

        def poll(self):
            return None
    monkeypatch.setattr(install.subprocess, "Popen", lambda cmd, **kw: spawned.append((cmd, kw)) or P())
    install.start(["base", "ai_images", "music", "avatar", "animation", "kids"])
    cmd, kw = spawned[0]
    assert "start_new_session" not in kw and kw["creationflags"] == 0x00000008 | 0x00000200
    got = {i["id"] for i in install.status()["items"]}
    # every feature has an 8 GB tier now (nothing is left out of the PC)
    assert got == {"voxcpm2", "whisper", "clip", "flux-schnell", "ltxv-2b", "LTX-2", "ltx2.3-dev", "gemma3-qat",
                   "ACE-Step-1.5-cuda"}
    assert not got & {"LTX2MLX", "VLMJudge", "gemma", "seedvr2", "RobustVideoMatting", "ACE-Step-1.5"}


def test_routes(env, monkeypatch):
    from cvm.api import setup as setup_api

    started = []
    monkeypatch.setattr(install, "start", lambda f: started.append(f))
    app = FastAPI()
    app.include_router(setup_api.router)
    c = TestClient(app)
    r = c.get("/setup/models", params={"features": "base"})
    assert r.status_code == 200 and {i["id"] for i in r.json()["items"]} == {"voxcpm2", "whisper", "clip"}
    assert r.json()["missing_gb"] > 0
    assert c.get("/setup/models", params={"features": "nope"}).status_code == 400
    assert c.post("/setup/models", json={"features": ["base"]}).status_code == 200
    assert started == [["base"]]
    assert c.get("/setup/models/status").json()["items"] == []


# ---------- each tool's ready() is the pipeline's own check ----------

def test_tool_ready_matches_pipeline_checks(tmp_path, monkeypatch):
    from cvm.avatar import matting
    from cvm.render import music_gen
    from cvm.visuals import judge

    monkeypatch.setattr(settings, "models_dir", tmp_path)
    monkeypatch.setattr(matting, "RVM", tmp_path / "RobustVideoMatting")
    monkeypatch.setattr(matting, "PYTHON", tmp_path / "RobustVideoMatting" / ".venv" / "bin" / "python")
    monkeypatch.setattr(music_gen, "ACE", tmp_path / "ACE-Step-1.5")
    monkeypatch.setattr(judge, "JUDGE", tmp_path / "VLMJudge")
    monkeypatch.setattr(judge, "PYTHON", tmp_path / "VLMJudge" / ".venv" / "bin" / "python")

    def touch(p: Path):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")

    assert not any(t.ready() for t in tools.TOOLS.values())
    rvm = tmp_path / "RobustVideoMatting"
    for p in (rvm / "cvm_matte.py", rvm / ".venv" / "bin" / "python"):
        touch(p)
    assert tools.TOOLS["RobustVideoMatting"].ready() is False is matting.matting_ready()
    touch(rvm / "weights" / "rvm_mobilenetv3.pth")
    assert tools.TOOLS["RobustVideoMatting"].ready() is True is matting.matting_ready()

    ace = tmp_path / "ACE-Step-1.5"
    for p in (ace / "cvm_music.py", ace / ".venv" / "bin" / "python"):
        touch(p)
    assert tools.TOOLS["ACE-Step-1.5"].ready() is False is music_gen.ready()
    (ace / "checkpoints" / "acestep-v15-turbo").mkdir(parents=True)
    (ace / "checkpoints" / "acestep-5Hz-lm-1.7B").mkdir(parents=True)
    assert tools.TOOLS["ACE-Step-1.5"].ready() is True is music_gen.ready()

    touch(tmp_path / "LTX2MLX" / ".venv" / "bin" / "ltx-2-mlx")
    assert tools.TOOLS["LTX2MLX"].ready() is True

    vj = tmp_path / "VLMJudge"
    touch(vj / ".venv" / "bin" / "python")
    assert tools.TOOLS["VLMJudge"].ready() is False
    touch(vj / "judge.py")
    assert tools.TOOLS["VLMJudge"].ready() is True  # the model half of judge.ready() is the "gemma" item


def test_model_ready_reuses_pipeline_checks(tmp_path, monkeypatch):
    from cvm.visuals import upscale

    monkeypatch.setattr(models_registry, "HF_CACHE", tmp_path)
    assert install._extra_ready("ltx-2.5") is False
    snap = tmp_path / "models--dgrauet--ltx-2.5-mlx-q8" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "transformer-distilled.safetensors").write_text("x")
    (snap / "connector.safetensors").write_text("x")
    assert install._extra_ready("ltx-2.5") is False  # the pipeline's check passes, but VAE/vocoder… are missing
    for f in install.LTX25_FILES:
        (snap / f).write_text("x")
    assert install._extra_ready("ltx-2.5") is True
    (snap / "vocoder.safetensors").unlink()
    (snap / "vocoder.safetensors").symlink_to(tmp_path / "no-such-blob")  # link to a blob that never finished
    assert install._extra_ready("ltx-2.5") is False
    s23 = tmp_path / "models--dgrauet--ltx-2.3-mlx-q8" / "snapshots" / "r"
    s23.mkdir(parents=True)
    for f in install.LTX23_FILES[:-1]:
        (s23 / f).write_text("x")
    assert install._extra_ready("ltx-2.3") is False
    (s23 / install.LTX23_FILES[-1]).write_text("x")
    assert install._extra_ready("ltx-2.3") is True
    seed = tmp_path / ("models--" + upscale.REPO.replace("/", "--")) / "snapshots" / "s"
    seed.mkdir(parents=True)
    assert install._extra_ready("seedvr2") is False
    for f in upscale.FILES:
        (seed / f).write_text("x")
    assert install._extra_ready("seedvr2") is True
    gem = tmp_path / "models--mlx-community--gemma-3-12b-it-4bit" / "snapshots" / "g"
    assert install._extra_ready("gemma") is False
    gem.mkdir(parents=True)
    (gem / "config.json").write_text("{}")
    assert install._extra_ready("gemma") is False  # no weights yet
    # the live repo's index names 5 shards but ships 2: the shard names are what counts
    (gem / "model.safetensors.index.json").write_text('{"weight_map": {"a": "model-00005-of-00005.safetensors"}}')
    (gem / "model-00001-of-00002.safetensors").write_text("x")
    assert install._extra_ready("gemma") is False  # cut after the first shard
    (gem / "model-00002-of-00002.safetensors").write_text("x")
    assert install._extra_ready("gemma") is True


def test_vendor_scripts_are_in_the_repo():
    for f in ("judge.py", "cvm_matte.py", "cvm_music.py"):
        assert (tools.VENDOR / f).stat().st_size > 1000


def test_start_twice_at_once_spawns_one_run(env, monkeypatch):
    import threading
    import time

    spawned = []

    class P:
        pid = 4343

        def poll(self):
            return None

    def popen(cmd, **kw):
        time.sleep(0.2)  # the window between the lease check and the lease write
        spawned.append(cmd)
        return P()
    monkeypatch.setattr(install.subprocess, "Popen", popen)
    ts = [threading.Thread(target=install.start, args=(["base"],)) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(spawned) == 1


def test_second_runner_waits_for_the_first(env):
    import fcntl
    import threading

    _, ready, calls = env
    lock = settings.data_dir / "logs" / "setup-install.lock"
    lock.parent.mkdir(parents=True)
    with open(lock, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)  # another runner (the wizard's or download-models.sh) is installing
        t = threading.Thread(target=install.run, args=(["whisper"],))
        t.start()
        t.join(0.3)
        assert t.is_alive() and calls["download"] == []
        ready.add("whisper")  # …and it installed whisper meanwhile
        fcntl.flock(f, fcntl.LOCK_UN)
    t.join(5)
    assert not t.is_alive() and calls["download"] == []  # nothing left to do once it got the lock


@pytest.mark.parametrize("pack", ["2.5", "2.3"])
def test_disk_gate_covers_every_download(env, monkeypatch, pack):
    """requirements.check() must never let through a selection whose downloads (empty machine) do not fit."""
    from itertools import combinations

    from cvm.setup.requirements import FEATURES, disk_gb

    monkeypatch.setattr(settings, "ltx_pack", pack)
    keys = list(FEATURES)
    for n in range(1, len(keys) + 1):
        for combo in combinations(keys, n):
            downloads = sum(i.size_gb for i in install.plan(list(combo), "mac"))
            assert disk_gb(list(combo), "mac") >= downloads - 0.05, combo
