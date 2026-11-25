"""LTX pack choice (2.5 when downloaded) and the presenter's Refine-Details pass."""
from pathlib import Path

import pytest

from cvm.avatar import talking
from cvm.visuals import sources


def _pack(hub: Path, version: str) -> Path:
    repo, transformer = sources.LTX_PACKS[version]
    snap = hub / ("models--" + repo.replace("/", "--")) / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / transformer).write_bytes(b"x")
    (snap / "connector.safetensors").write_bytes(b"x")
    return snap


def test_pack_follows_the_setting_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr("cvm.models_registry.HF_CACHE", tmp_path)
    assert sources.ltx_version() is None and sources._ltx_model() is None
    old = _pack(tmp_path, "2.3")
    monkeypatch.setattr(sources.settings, "ltx_pack", "2.5")
    assert sources.ltx_version() == "2.3" and sources._ltx_model() == old  # 2.5 not downloaded here
    new = _pack(tmp_path, "2.5")
    assert sources.ltx_version() == "2.5" and sources._ltx_model() == new
    monkeypatch.setattr(sources.settings, "ltx_pack", "2.3")
    assert sources._ltx_model() == old


@pytest.mark.parametrize("version,refine,lora,mode", [("2.5", True, True, "2.5-refined"), ("2.5", False, True, "2.5"),
                                                       ("2.5", True, False, "2.5"), ("2.3", True, True, "2.3"), (None, True, True, "2.3")])
def test_engine_mode(monkeypatch, version, refine, lora, mode):
    monkeypatch.setattr(sources, "ltx_version", lambda: version)
    monkeypatch.setattr(sources, "ltx_refine_lora", lambda: Path("lora.safetensors") if lora else None)
    monkeypatch.setattr(talking.settings, "presenter_refine", refine)
    assert talking.engine_mode() == mode


def _refine_env(monkeypatch, tmp_path, run, motions):
    from cvm import gpu
    from huey import MemoryHuey

    monkeypatch.setattr(gpu, "lock", MemoryHuey().lock_task("gpu"))
    monkeypatch.setattr(sources, "_ltx_model", lambda: tmp_path / "pack")
    monkeypatch.setattr(sources, "ltx_refine_lora", lambda: tmp_path / "lora.safetensors")
    monkeypatch.setattr(talking, "_run_ltx", run)
    monkeypatch.setattr(talking, "motion", lambda v: motions[Path(v).name])
    raw = tmp_path / "raw_00.mp4"
    raw.write_bytes(b"take")
    return raw


def test_refined_chunk_replaces_the_take(tmp_path, monkeypatch):
    cmds = []

    def run(cmd, timeout=3600, guard=False):
        cmds.append(cmd)
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"refined")

    raw = _refine_env(monkeypatch, tmp_path, run, {"raw_00.mp4": 0.5, "raw_00_refined.mp4": 0.5})
    assert talking._refine(Path("ltx"), raw, tmp_path / "anchor.png", "p", 6.0) is True
    cmd = cmds[0]
    assert raw.read_bytes() == b"refined" and cmd[1] == "ic-lora" and "--single-stage" in cmd
    assert cmd[cmd.index("--video-conditioning") + 1] == str(raw) and cmd[cmd.index("-f") + 1] == str(talking._frames(6.0))


def test_failed_or_frozen_refinement_keeps_the_take(tmp_path, monkeypatch):
    def boom(cmd, timeout=3600, guard=False):
        raise RuntimeError("metal out of memory")

    raw = _refine_env(monkeypatch, tmp_path, boom, {})
    assert talking._refine(Path("ltx"), raw, tmp_path / "a.png", "p", 6.0) is False and raw.read_bytes() == b"take"

    def frozen(cmd, timeout=3600, guard=False):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"refined")

    raw = _refine_env(monkeypatch, tmp_path, frozen, {"raw_00.mp4": 0.5, "raw_00_refined.mp4": 0.05})
    assert talking._refine(Path("ltx"), raw, tmp_path / "a.png", "p", 6.0) is False and raw.read_bytes() == b"take"
    assert not (tmp_path / "raw_00_refined.mp4").exists()


def test_refine_off_per_project(monkeypatch):
    from cvm.avatar import talking
    from cvm.visuals import sources

    monkeypatch.setattr(sources, "ltx_version", lambda: "2.5")
    monkeypatch.setattr(sources, "ltx_refine_lora", lambda: "lora.safetensors")
    monkeypatch.setattr(talking.settings, "presenter_refine", True)
    assert talking.engine_mode() == "2.5-refined"
    token = talking.NO_REFINE.set(True)
    try:
        assert talking.engine_mode() == "2.5"
    finally:
        talking.NO_REFINE.reset(token)


def test_refine_off_also_covers_scene_avatars(monkeypatch):
    # 2026-11-22: project 559 was on the refine-off list but its "cena com avatar" clips (lipsyncing and composing
    # steps) still ran the refine pass and took the Mac to 4 % free memory
    from types import SimpleNamespace

    from cvm.avatar import talking
    from cvm.render import compose
    from cvm.workers import tasks

    seen = []
    monkeypatch.setattr(talking, "refine_off", lambda session, pid: pid == 559)
    monkeypatch.setattr(compose, "prepare_avatar", lambda session, project: seen.append(talking.NO_REFINE.get()))
    monkeypatch.setattr(compose, "platforms_for", lambda session, project: ["youtube"])
    monkeypatch.setattr(compose, "compose", lambda *a, **k: seen.append(talking.NO_REFINE.get()))
    session = SimpleNamespace(get=lambda model, pid: SimpleNamespace(id=pid, languages=["pt-BR"]))
    for pid in (559, 560):
        job = SimpleNamespace(project_id=pid)
        tasks._lipsync(job, session)
        tasks._composing(job, session)
    assert seen == [True, True, False, False]
    assert talking.NO_REFINE.get() is False


def test_refine_pass_is_stopped_when_memory_runs_out(tmp_path, monkeypatch):
    # 2026-11-22: the refine pass took 64–70 GB on the 48 GB Mac and froze it (4–10 % free) many times overnight;
    # now the pass is stopped by itself, the chunk keeps its plain take and the rest of the video skips refining
    import sys
    import time

    from cvm.avatar import talking

    monkeypatch.setattr(talking, "free_ratio", lambda: 0.05)
    t0 = time.monotonic()
    with pytest.raises(talking.MemoryGuard):
        talking._run_ltx([sys.executable, "-c", "import time; time.sleep(30)"], guard=True)
    assert time.monotonic() - t0 < 10


def test_memory_guard_turns_refine_off_for_the_rest_of_the_video(tmp_path, monkeypatch):
    from cvm.avatar import talking

    def boom(cmd, timeout=3600, guard=False):
        raise talking.MemoryGuard("memória livre 5%")

    raw = _refine_env(monkeypatch, tmp_path, boom, {})
    token = talking.NO_REFINE.set(False)
    try:
        assert talking._refine(Path("ltx"), raw, tmp_path / "a.png", "p", 6.0) is False
        assert talking.NO_REFINE.get() is True
    finally:
        talking.NO_REFINE.reset(token)
