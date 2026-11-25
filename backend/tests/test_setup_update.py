"""'Atualizar' for installs cloned from the public repo. Real git on throwaway repos in tmp_path; uv, npm, the
restart script and the API (pause/resume, /jobs) are faked: nothing real is restarted or paused."""
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
from sqlmodel.pool import StaticPool

from cvm.setup import update


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make_repos(tmp_path, name="orto-cvm-ai.git", tags=("v1.0", "v1.2", "v1.10")):
    """A bare 'origin' with one commit per tag, and a clone of it checked out at the first tag."""
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "t@example.com")
    git(work, "config", "user.name", "t")
    for t in tags:
        (work / "VERSION").write_text(t)
        git(work, "add", "VERSION")
        git(work, "commit", "-q", "-m", t)
        git(work, "tag", t)
    origin = tmp_path / name
    git(tmp_path, "clone", "-q", "--bare", str(work), str(origin))
    root = tmp_path / "install"
    git(tmp_path, "clone", "-q", str(origin), str(root))
    git(root, "checkout", "-q", tags[0])
    (root / "backend").mkdir()
    (root / "frontend").mkdir()
    return root


class FakeApi:
    """GET /jobs answers from `boards` (one list per call, the last one repeats); pause-all answers from `pauses`
    (one list per call, then nothing new); records every call."""

    def __init__(self, boards=None, changed=(7, 9)):
        self.calls: list[tuple] = []
        self.boards = list(boards or [[]])
        self.pauses = [list(changed)]
        self.changed: list[int] = []  # every project paused so far
        self.on_get = lambda: None
        self.fail_resume = False

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/jobs/pause-all":
            new = (self.pauses.pop(0) if self.pauses else []) if body["paused"] else []
            self.changed += new
            return {"changed": new}
        if path.endswith("/pause") and self.fail_resume:
            raise update.StepFailed("a API do CVM não respondeu (connection refused)")
        if path == "/jobs" and method == "GET":
            self.on_get()
            board = self.boards.pop(0) if len(self.boards) > 1 else self.boards[0]
            # every job the update paused is there, paused, besides the board
            paused = [{"id": 100 + p, "project_id": p, "state": "voicing", "paused": True, "progress": {}}
                      for p in self.changed]
            return board + paused
        return {}


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(update, "_engine", lambda: engine)
    monkeypatch.setattr(update, "_sleep", lambda s: None)
    monkeypatch.setattr(update.sys, "platform", "darwin")
    update._LATEST.clear()
    root = make_repos(tmp_path)
    monkeypatch.setattr(update, "ROOT", root)
    log: list[tuple] = []

    def sh(cmd, cwd):
        log.append((tuple(cmd), cwd.name, git(root, "describe", "--tags", "--exact-match")))
    monkeypatch.setattr(update, "_sh", sh)
    api = FakeApi()
    monkeypatch.setattr(update, "_api", api)
    return {"root": root, "sh": log, "api": api, "tmp": tmp_path, "engine": engine}


def test_status_reads_tags(env):
    assert update.public_install()
    st = update.status()
    assert st == {"current": "v1.0", "latest": "v1.10", "available": True, "skipped": None}  # v1.10 > v1.2: numbers

    git(env["root"], "checkout", "-q", "v1.10")
    assert update.status()["available"] is False
    # the latest tag is cached for an hour: a new tag on origin is not seen at once
    work = env["tmp"] / "work"
    (work / "VERSION").write_text("v2.0")
    git(work, "commit", "-qam", "v2")
    git(work, "tag", "v2.0")
    git(work, "push", "-q", str(env["tmp"] / "orto-cvm-ai.git"), "v2.0")
    assert update.status()["latest"] == "v1.10"
    update._LATEST.clear()
    assert update.status()["latest"] == "v2.0"


def test_hidden_on_owner_repo(tmp_path, monkeypatch):
    root = make_repos(tmp_path, name="ortocvm.git")
    monkeypatch.setattr(update, "ROOT", root)
    assert not update.public_install()

    from cvm.api import setup as setup_api

    app = FastAPI()
    app.include_router(setup_api.router)
    c = TestClient(app)
    assert c.get("/setup/update").json() == {"public_install": False}
    assert c.post("/setup/update").status_code == 404


def test_update_moves_to_latest_tag(env):
    assert update.run() == 0
    root = env["root"]
    assert git(root, "describe", "--tags", "--exact-match") == "v1.10"
    cmds = [(c[0], cwd) for c, cwd, _ in env["sh"]]
    assert cmds == [("uv", "backend"), ("npm", "frontend"), ("bash", "install")]
    assert all(tag == "v1.10" for *_, tag in env["sh"])  # dependencies synced on the new version
    assert env["sh"][-1][0][1:] == (str(root / "scripts" / "install" / "mac.sh"), "restart")

    calls = env["api"].calls
    assert calls[0] == ("POST", "/jobs/pause-all", {"paused": True})
    # exactly the jobs the update paused are let go again (by job id), nothing else
    resumed = [(p, b) for m, p, b in calls if p.endswith("/pause") and p != "/jobs/pause-all"]
    assert resumed == [("/jobs/107/pause", {"paused": False}), ("/jobs/109/pause", {"paused": False})]
    st = update.state()
    assert st["state"] == "done" and st["from"] == "v1.0" and st["to"] == "v1.10"


def test_update_rolls_back_on_failure(env, monkeypatch):
    root = env["root"]
    log = env["sh"]

    def sh(cmd, cwd):
        tag = git(root, "describe", "--tags", "--exact-match")
        log.append((tuple(cmd), cwd.name, tag))
        if cmd[0] == "npm" and tag == "v1.10":
            raise update.StepFailed("npm install falhou")
    monkeypatch.setattr(update, "_sh", sh)

    assert update.run() == 1
    assert git(root, "describe", "--tags", "--exact-match") == "v1.0"
    # the old version gets its own dependencies back and the services restarted on it
    assert [(c[0], cwd, tag) for c, cwd, tag in log] == [
        ("uv", "backend", "v1.10"), ("npm", "frontend", "v1.10"),
        ("uv", "backend", "v1.0"), ("npm", "frontend", "v1.0"), ("bash", "install", "v1.0")]
    st = update.state()
    assert st["state"] == "failed"
    assert "v1.0" in st["error"] and "npm install falhou" in st["error"]
    resumed = [p for m, p, b in env["api"].calls if p in ("/jobs/107/pause", "/jobs/109/pause")]
    assert resumed == ["/jobs/107/pause", "/jobs/109/pause"]  # the queue goes on, on the old version


def test_waits_for_queue_boundary(env):
    mid = [{"id": 1, "project_id": 1, "state": "lipsyncing", "paused": False,
            "progress": {"pid": 123, "label": "lipsync"}}]
    waiting = [{"id": 2, "project_id": 2, "state": "voicing", "paused": False,
                "progress": {"pid": 5, "waiting": "gpu"}}]  # waiting for the lock: not mid-step
    env["api"].boards = [mid, mid, waiting]
    tags: list[str] = []
    env["api"].on_get = lambda: tags.append(git(env["root"], "describe", "--tags", "--exact-match"))
    seen: list[str] = []
    orig = update._save

    def save(value):
        seen.append(value.get("step", ""))
        orig(value)
    update._save = save
    try:
        assert update.run() == 0
    finally:
        update._save = orig
    calls = env["api"].calls
    pause_at = calls.index(("POST", "/jobs/pause-all", {"paused": True}))
    gets = [i for i, c in enumerate(calls) if c == ("GET", "/jobs", None)]
    assert pause_at < gets[0] and len(gets) >= 3  # paused first, then polled until no job was mid-step
    assert "waiting" in seen
    # nothing changed on disk while the lipsync was still running: the checkout came after the boundary
    assert tags[:3] == ["v1.0", "v1.0", "v1.0"]
    assert git(env["root"], "describe", "--tags", "--exact-match") == "v1.10"


def test_routes_on_public_install(env, monkeypatch):
    from cvm.api import setup as setup_api

    app = FastAPI()
    app.include_router(setup_api.router)
    c = TestClient(app)
    body = c.get("/setup/update").json()
    assert body["public_install"] and body["current"] == "v1.0" and body["latest"] == "v1.10" and body["available"]
    assert body["run"] == {"state": "idle"} and body["running"] is False
    started = []
    monkeypatch.setattr(update, "start", lambda: started.append(1))
    assert c.post("/setup/update").json() == {"started": True} and started == [1]



def test_start_refused_when_nothing_to_do_or_not_a_mac(env, monkeypatch):
    git(env["root"], "checkout", "-q", "v1.10")
    with pytest.raises(ValueError, match="mais nova"):
        update.check_can_start()
    git(env["root"], "checkout", "-q", "v1.0")
    (env["root"] / "VERSION").write_text("local change")
    with pytest.raises(ValueError, match="mudados"):
        update.check_can_start()
    git(env["root"], "checkout", "-q", "--", "VERSION")
    monkeypatch.setattr(update.sys, "platform", "linux")
    with pytest.raises(ValueError, match="não é suportada"):
        update.check_can_start()


def test_worker_holds_jobs_while_updating(env, monkeypatch):
    """(a) While an update runs, the worker starts no new step: the job waits and goes on once the hold clears."""
    import os

    from sqlmodel import Session

    from cvm import db, gpu
    from cvm.models import Job, Project
    from cvm.workers import tasks
    from huey import MemoryHuey

    engine = env["engine"]
    monkeypatch.setattr(tasks, "engine", engine)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(gpu, "lock", MemoryHuey("t-upd").lock_task("gpu"))
    scheduled = []
    monkeypatch.setattr(tasks, "run_job", type("T", (), {"schedule": staticmethod(
        lambda args, delay: scheduled.append(delay))}))
    ran = []
    monkeypatch.setitem(tasks.HANDLERS, "sourcing_visuals", lambda job, session: ran.append(1) or "lipsyncing")
    monkeypatch.setitem(tasks.HANDLERS, "lipsyncing", lambda job, session: None)
    with Session(engine) as s:
        p = Project(topic="t", title="t")
        s.add(p)
        s.commit()
        j = Job(project_id=p.id, state="sourcing_visuals")
        s.add(j)
        s.commit()
        jid = j.id

    update._save({"state": "running", "pid": os.getpid(), "step": "waiting"})
    tasks._drive(jid, set())
    assert not ran and scheduled
    with Session(engine) as s:
        job = s.get(Job, jid)
        assert job.state == "sourcing_visuals" and job.progress["waiting"] == "update"

    # a run whose process died does not hold the queue forever
    update._save({"state": "running", "pid": 999999, "step": "waiting"})
    assert not update.hold_active()
    update._save({"state": "done"})
    tasks._drive(jid, set())
    assert ran
    with Session(engine) as s:
        assert s.get(Job, jid).state == "lipsyncing"


def test_hold_is_on_during_the_run_and_released_at_the_end(env, monkeypatch):
    seen = []
    orig = env["api"]

    def api(method, path, body=None):
        seen.append((path, update.hold_active()))
        return orig(method, path, body)
    monkeypatch.setattr(update, "_api", api)
    assert update.run() == 0
    during = [h for p, h in seen if p == "/jobs/pause-all"]
    assert during and all(during)  # pausing and the checkout happen under the hold
    assert not update.hold_active()
    resumes = [h for p, h in seen if p.endswith("/pause") and p != "/jobs/pause-all"]
    assert resumes and not any(resumes)  # released before the queue is let go


def test_job_started_during_the_wait_is_paused_too(env):
    """(b) A job that starts after the first pause-all (approved meanwhile) is paused right before the checkout and
    resumed at the end with the others."""
    env["api"].pauses = [[7, 9], [11]]
    assert update.run() == 0
    calls = env["api"].calls
    pauses = [i for i, c in enumerate(calls) if c == ("POST", "/jobs/pause-all", {"paused": True})]
    assert len(pauses) == 2
    gets = [i for i, c in enumerate(calls) if c == ("GET", "/jobs", None)]
    assert any(i > pauses[1] for i in gets)  # polled again after the second pause
    st = update.state()
    assert st["paused"] == [7, 9, 11]
    resumed = [p for m, p, b in calls if p.endswith("/pause") and p != "/jobs/pause-all"]
    assert resumed == ["/jobs/107/pause", "/jobs/109/pause", "/jobs/111/pause"]


def test_resume_failure_keeps_the_installed_update(env):
    env["api"].fail_resume = True
    assert update.run() == 0
    assert git(env["root"], "describe", "--tags", "--exact-match") == "v1.10"  # no rollback
    assert [c[0][0] for c in env["sh"]] == ["uv", "npm", "bash"]
    st = update.state()
    assert st["state"] == "done" and "pausad" in st["warning"]


def test_offline_click_is_a_409(env, monkeypatch):
    def offline():
        raise update.StepFailed("git ls-remote falhou: Could not resolve host")
    monkeypatch.setattr(update, "_remote_tags", offline)
    from cvm.api import setup as setup_api

    app = FastAPI()
    app.include_router(setup_api.router)
    r = TestClient(app).post("/setup/update")
    assert r.status_code == 409
    assert "internet" in r.json()["detail"]


def test_failed_command_names_it_without_the_folder_path(monkeypatch, tmp_path):
    # the error is shown in the app: "mac.sh restart", not the install's absolute path
    import subprocess as sp

    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: sp.CompletedProcess(a, 1))
    with pytest.raises(update.StepFailed) as e:
        update._sh(["bash", str(tmp_path / "scripts" / "install" / "mac.sh"), "restart"], tmp_path)
    assert "“bash mac.sh restart”" in str(e.value) and str(tmp_path) not in str(e.value)


def push_tag(env, tag):
    work = env["tmp"] / "work"
    (work / "VERSION").write_text(tag)
    git(work, "commit", "-qam", tag)
    git(work, "tag", tag)
    git(work, "push", "-q", str(env["tmp"] / "orto-cvm-ai.git"), tag)


def test_failed_tag_is_not_offered_again_until_a_newer_one(env, monkeypatch):
    root = env["root"]

    def sh(cmd, cwd):
        if cmd[0] == "bash" and git(root, "describe", "--tags", "--exact-match") == "v1.10":
            raise update.StepFailed("o CVM não ligou")
    monkeypatch.setattr(update, "_sh", sh)
    assert update.run() == 1
    assert git(root, "describe", "--tags", "--exact-match") == "v1.0"
    assert update.failed_tags() == ["v1.10"]

    st = update.status()
    assert st["latest"] == "v1.10" and st["skipped"] == "v1.10" and st["available"] is False
    with pytest.raises(ValueError, match="v1.10 não funcionou"):
        update.check_can_start()

    push_tag(env, "v1.11")
    update._LATEST.clear()
    st = update.status()
    assert st == {"current": "v1.0", "latest": "v1.11", "available": True, "skipped": None}


def test_failure_before_the_checkout_does_not_mark_the_tag(env, monkeypatch):
    real = update._git

    def git_offline(*args, **kw):
        if args[0] == "fetch":
            raise update.StepFailed("git fetch falhou: sem internet")
        return real(*args, **kw)
    monkeypatch.setattr(update, "_git", git_offline)
    assert update.run() == 1
    assert update.failed_tags() == [] and update.status()["available"] is True


def test_tag_cache_is_dropped_when_a_run_finishes(env):
    assert update.status()["latest"] == "v1.10"
    push_tag(env, "v2.0")
    assert update.status()["latest"] == "v1.10"  # cached for an hour
    # a run (in its own process) ends: the API's next GET looks at origin again
    update._save({"state": "failed", "finished_at": update._now()})
    assert update.status()["latest"] == "v2.0"
    assert update.status()["latest"] == "v2.0"


# ---------- Windows (INSTALAR.bat / scripts/install/windows.ps1) ----------

def _ps(root):
    return ("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(root / "scripts" / "install" / "windows.ps1"))


def test_windows_update_stops_before_sync_and_restarts_with_powershell(env, monkeypatch):
    monkeypatch.setattr(update.sys, "platform", "win32")
    monkeypatch.setattr("shutil.which", lambda n: f"C:\\Tools\\{n}.cmd" if n == "npm" else None)
    assert update.run() == 0
    ps = _ps(env["root"])
    # the running API/worker lock the .venv on Windows: stop, sync, then restart
    assert [c for c, _cwd, _tag in env["sh"]] == [
        (*ps, "stop"), ("uv", "sync"), ("C:\\Tools\\npm.cmd", "install", "--no-audit", "--no-fund"), (*ps, "restart")]
    assert update.state()["state"] == "done"


def test_windows_rollback_stops_again_before_resyncing(env, monkeypatch):
    monkeypatch.setattr(update.sys, "platform", "win32")
    monkeypatch.setattr("shutil.which", lambda n: None)
    root, log = env["root"], env["sh"]

    def sh(cmd, cwd):
        tag = git(root, "describe", "--tags", "--exact-match")
        log.append((tuple(cmd), cwd.name, tag))
        if cmd[0] == "npm" and tag == "v1.10":
            raise update.StepFailed("npm install falhou")
    monkeypatch.setattr(update, "_sh", sh)
    assert update.run() == 1
    assert [c[-1] if c[0] == "powershell" else c[0] for c, *_ in log] == \
        ["stop", "uv", "npm", "stop", "uv", "npm", "restart"]
    st = update.state()
    assert st["state"] == "failed" and "voltou para a versão v1.0" in st["error"]


def test_rollback_failure_names_the_installer_of_this_system(env, monkeypatch):
    monkeypatch.setattr(update.sys, "platform", "win32")
    monkeypatch.setattr("shutil.which", lambda n: None)

    def sh(cmd, cwd):
        if cmd[0] == "npm":
            raise update.StepFailed("npm install falhou")
    monkeypatch.setattr(update, "_sh", sh)
    assert update.run() == 1
    assert "INSTALAR.bat" in update.state()["error"]


def test_supported_systems(monkeypatch):
    for plat, ok in (("darwin", True), ("win32", True), ("linux", False)):
        monkeypatch.setattr(update.sys, "platform", plat)
        assert update.supported() is ok


def test_failed_powershell_command_is_named_without_flags_or_path(monkeypatch, tmp_path):
    import subprocess as sp

    monkeypatch.setattr(update.sys, "platform", "win32")
    seen = {}

    def run(*a, **k):
        seen.update(k)
        return sp.CompletedProcess(a, 1)
    monkeypatch.setattr(update.subprocess, "run", run)
    with pytest.raises(update.StepFailed) as e:
        update._sh(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    "C:\\CVM\\scripts\\install\\windows.ps1", "restart"], tmp_path)
    assert "“powershell windows.ps1 restart”" in str(e.value) and "CVM\\" not in str(e.value)
    assert seen["creationflags"] == 0x08000000  # CREATE_NO_WINDOW: no console flashing from the detached run


def test_start_is_detached_on_windows(env, monkeypatch):
    from cvm.config import settings

    monkeypatch.setattr(update.sys, "platform", "win32")
    monkeypatch.setattr(update, "check_can_start", lambda: None)
    monkeypatch.setattr(settings, "data_dir", env["tmp"] / "data")
    spawned = []

    class P:
        pid = 4321

        def poll(self):
            return None
    monkeypatch.setattr(update.subprocess, "Popen", lambda cmd, **kw: spawned.append(kw) or P())
    update.start()
    assert "start_new_session" not in spawned[0]
    assert spawned[0]["creationflags"] == 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
