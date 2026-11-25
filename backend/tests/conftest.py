import pytest


@pytest.fixture(autouse=True)
def _no_gpu_memory_check(monkeypatch, tmp_path_factory):
    """The free-RAM check before a heavy model (cvm.gpu.memory_short) depends on the machine running the tests."""
    from cvm import gpu
    from cvm.config import settings

    monkeypatch.setattr(settings, "gpu_min_free_gb", 0.0)
    # the studio timezone (CVM_DEFAULT_TZ) is fixed so daily/weekly gates and calendars don't depend on the machine
    monkeypatch.setattr(settings, "default_tz", "Asia/Dubai")
    # finished videos are copied to a readable folder (cvm/export_folder.py): never the user's ~/Movies in tests
    monkeypatch.setattr(settings, "export_dir", tmp_path_factory.mktemp("export"))
    monkeypatch.setattr(gpu, "_owner", None)
    # importing cvm.workers.tasks wires this to the live DB: real waiting jobs would make section() yield for minutes
    monkeypatch.setattr(gpu, "waiters", lambda: False)
    # dropping in-process models imports mlx/voice modules (slow, machine-dependent): tested on its own
    monkeypatch.setattr(gpu, "free_inprocess", lambda: None)
    # the light slot is a lock in the live Huey storage: a test must never wait on the worker's real RVM/whisper run
    from huey import MemoryHuey

    monkeypatch.setattr(gpu, "light_lock", MemoryHuey("tests-light").lock_task("gpu-light"))
    # the user's priority list lives in the live DB: tests never read it
    import cvm.workers.tasks as tasks

    monkeypatch.setattr(tasks, "priority_projects", lambda: set())


# tests of run_claude itself (they fake the subprocess, never start claude)
RUN_CLAUDE_TESTS = {"test_limit_message_stops_claude_until_the_reset", "test_run_claude_passes_disallowed_tools"}


@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch, request):
    """Tests never start a real `claude -p` (subscription, vidIQ credits): a test that needs one passes its own fake."""
    from cvm.scripting import claude_runner

    if request.node.name in RUN_CLAUDE_TESTS:
        return

    def refuse(*a, **k):
        raise RuntimeError("real claude -p called from a test")
    monkeypatch.setattr(claude_runner, "run_claude", refuse)


@pytest.fixture(autouse=True)
def _no_real_codex(monkeypatch):
    """Nor a real `codex exec` (ChatGPT subscription): light calls may fall back to Codex at Claude's limit.
    Tests of run_codex's argv fake subprocess.Popen and patch this back."""
    from cvm.scripting import codex_runner

    monkeypatch.setattr(codex_runner, "installed", lambda: False)


@pytest.fixture(autouse=True)
def _no_live_engines_or_keychain(monkeypatch):
    """Engine choices (Setting "engines") come from the live DB and API keys from the real Keychain: tests see only
    the .env defaults and an empty in-memory key store (tests of those modules patch them back on their own)."""
    from cvm import engines, keystore

    store: dict[str, str] = {}
    monkeypatch.setattr(engines, "_stored", lambda: {})
    engines.invalidate()
    keystore._cache.clear()
    monkeypatch.setattr(keystore, "_store_get", lambda name: store.get(name))
    monkeypatch.setattr(keystore, "_store_set", lambda name, value: store.__setitem__(name, value))
    monkeypatch.setattr(keystore, "_store_delete", lambda name: store.pop(name, None))
    yield
    engines.invalidate()
    keystore._cache.clear()


class EditorialSet:
    """One editorial script with its word timeline and resolved plan (tests/fixtures/editorial = a small synthetic
    script; private/fixtures/editorial = the owner's approved reference video, absent in the public copy)."""

    def __init__(self, name, folder):
        import json

        self.name, self.dir = name, folder
        self.cenas = json.loads((folder / "cortes.json").read_text())["cenas"]
        self.narration = " ".join(b["text"] for b in json.loads((folder / "narracao.json").read_text()))
        self.timeline = json.loads((folder / "timeline.json").read_text())
        self.plan = json.loads((folder / "plan.json").read_text())


@pytest.fixture(params=["public", "owner"])
def editorial_set(request):
    from pathlib import Path

    from cvm import private

    folder = (Path(__file__).parent / "fixtures" / "editorial" if request.param == "public"
              else private.PRIVATE_DIR / "fixtures" / "editorial")
    if not (folder / "cortes.json").exists():
        pytest.skip("no private/fixtures/editorial (public copy)")
    return EditorialSet(request.param, folder)
