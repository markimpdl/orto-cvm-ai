"""'Atualizar' for installs cloned from the public repo (origin = .../orto-cvm-ai): move to the newest
release tag (vX.Y, made by scripts/export_public.py) with a safe restart and an automatic rollback.

public_install() is true only when origin is that repo, so the owner's repo never shows the button.
status() = {"current", "latest", "available", "skipped"}: current = `git describe --tags --exact-match`, latest = the
highest vX.Y from `git ls-remote --tags origin` (cached 1 h, dropped when a run finishes). A tag that failed here and was
rolled back (Setting "update_failed") is not offered again ("skipped") until a newer tag appears. start() spawns `python -m cvm.setup.update run` detached (it
outlives the API it restarts); run() goes through the queue the way the safe restart does: pause-all, wait until no
job is mid-step, then fetch + checkout <latest>, `uv sync`, `npm install`, `scripts/install/mac.sh restart` (Windows: `windows.ps1
stop` before the sync, since running programs lock the .venv, then `windows.ps1 restart`; this folder's API/worker/UI
only), and resume exactly the jobs it paused. Any failure checks the previous version out
again, re-syncs, restarts and leaves state "failed" with the error in pt-BR. State in Setting "update".
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from cvm.config import REPO_ROOT, settings
from cvm.setup.detach import detached, no_window

ROOT: Path = REPO_ROOT
KEY = "update"
FAILED_KEY = "update_failed"  # {"tags": [...]}: versions that broke here and were rolled back
PUBLIC_REPO = re.compile(r"[/:]orto-cvm-ai(\.git)?/?$")
TAG = re.compile(r"^v(\d+)(?:\.(\d+))?(?:\.(\d+))?$")
CACHE_S = 3600
POLL_S = 10
WAIT_MAX_S = 6 * 3600  # the longest step (a refined presenter chunk) is ~35 min; a stuck queue must not hold forever
# a job is mid-step when a process works on it right now (same check as scripts/cvm-stop.sh)
MID_STEP = {"scripting", "voicing", "sourcing_visuals", "lipsyncing", "composing", "rendering", "validating", "seo"}

_LATEST: dict[str, tuple[float, str | None, float]] = {}  # folder -> (monotonic, tag, wall clock)
_PROC: subprocess.Popen | None = None
_START = threading.Lock()


class StepFailed(Exception):
    """A step of the update failed; the message is pt-BR, shown to the user."""


def _sleep(s: float) -> None:
    time.sleep(s)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------- git ----------

def _git(*args: str, check: bool = True, timeout: float = 60) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise StepFailed(f"git {args[0]} falhou: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout.strip() if r.returncode == 0 else ""


def public_install() -> bool:
    try:
        url = _git("remote", "get-url", "origin", check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(url and PUBLIC_REPO.search(url))


def _version(tag: str) -> tuple[int, int, int] | None:
    m = TAG.match(tag)
    return tuple(int(g or 0) for g in m.groups()) if m else None  # type: ignore[return-value]


def current() -> str | None:
    return _git("describe", "--tags", "--exact-match", "HEAD", check=False, timeout=10) or None


def _remote_tags() -> list[str]:
    out = _git("ls-remote", "--tags", "origin", timeout=30)
    tags = {line.split("refs/tags/", 1)[1].removesuffix("^{}") for line in out.splitlines() if "refs/tags/" in line}
    return [t for t in tags if _version(t)]


def latest() -> str | None:
    """Highest vX.Y on origin, cached for an hour (a failed look is not cached)."""
    key = str(ROOT)
    hit = _LATEST.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_S and not _finished_since(hit[2]):
        return hit[1]
    tags = _remote_tags()
    tag = max(tags, key=_version) if tags else None
    _LATEST[key] = (time.monotonic(), tag, time.time())
    return tag


def _finished_since(wall: float) -> bool:
    """A run ended (done or failed) after the cache was filled: look at origin again."""
    try:
        fin = state().get("finished_at")
        return bool(fin) and datetime.fromisoformat(fin).timestamp() >= int(wall)
    except Exception:  # noqa: BLE001 — no DB yet / odd value: keep the cache
        return False


def _contains(tag: str) -> bool:
    """HEAD already has this tag's commit (an install on main past the tag)."""
    r = subprocess.run(["git", "merge-base", "--is-ancestor", f"refs/tags/{tag}", "HEAD"], cwd=ROOT,
                       capture_output=True, timeout=10)
    return r.returncode == 0


def status() -> dict:
    cur, new = current(), latest()
    skipped = new if new and new in failed_tags() else None
    if not new or skipped:
        available = False
    elif cur and _version(cur):
        available = _version(new) > _version(cur)
    else:
        available = not _contains(new)
    return {"current": cur, "latest": new, "available": available, "skipped": skipped}


def _dirty() -> bool:
    return bool(_git("status", "--porcelain", "--untracked-files=no", check=False, timeout=20))


# ---------- state (Setting "update") ----------

def _engine():
    from cvm.db import engine

    return engine


def state() -> dict:
    from sqlmodel import Session

    from cvm.models import Setting

    with Session(_engine()) as s:
        row = s.get(Setting, KEY)
        return dict(row.value) if row else {"state": "idle"}


def _save(value: dict, key: str = KEY) -> None:
    from sqlmodel import Session

    from cvm.models import Setting

    with Session(_engine()) as s:
        row = s.get(Setting, key)
        if row is None:
            s.add(Setting(key=key, value=value))
        else:
            row.value = value
        s.commit()


def failed_tags() -> list[str]:
    from sqlmodel import Session

    from cvm.models import Setting

    with Session(_engine()) as s:
        row = s.get(Setting, FAILED_KEY)
        return list((row.value or {}).get("tags") or []) if row else []


def _remember_failed(tag: str) -> None:
    tags = failed_tags()
    if tag not in tags:
        _save({"tags": [*tags, tag]}, FAILED_KEY)


def _alive(pid) -> bool:
    if not pid:
        return False
    if _PROC is not None and _PROC.pid == pid:
        return _PROC.poll() is None
    try:
        import psutil

        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except Exception:  # noqa: BLE001 — gone between the two calls
        return False


def hold_active(session=None) -> bool:
    """True while an update runs (state "running" and its process alive): the worker starts no new step then
    (cvm/workers/tasks._drive). A run that died does not hold the queue."""
    if session is None:
        st = state()
    else:
        from cvm.models import Setting

        row = session.get(Setting, KEY)
        st = dict(row.value) if row else {}
    return st.get("state") == "running" and _alive(st.get("pid"))


def running() -> bool:
    st = state()
    return st.get("state") == "running" and _alive(st.get("pid"))


# ---------- outside world: commands and the API ----------

def _sh(cmd: list[str], cwd: Path) -> None:
    print("→", " ".join(cmd), f"({cwd})", flush=True)
    env = {**os.environ, "CVM_NO_OPEN": "1"}
    env.pop("VIRTUAL_ENV", None)  # uv sync must use the folder's own .venv
    r = subprocess.run(cmd, cwd=cwd, env=env, **no_window())
    if r.returncode != 0:
        # no install path in the app's message, no flags: "bash mac.sh restart", "powershell windows.ps1 restart"
        words = [cmd[0], *cmd[-2:]] if cmd[0] == "powershell" else [w for w in cmd if not w.startswith("-")][:3]
        shown = " ".join(re.split(r"[\\/]", w)[-1] for w in words)
        raise StepFailed(f"o comando “{shown}” falhou (código {r.returncode}); veja data/logs/update.log")


def _api(method: str, path: str, body: dict | None = None):
    url = f"http://{settings.api_host}:{settings.api_port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"null")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise StepFailed(f"a API do CVM não respondeu ({e})") from e


def _mid_step(jobs: list[dict]) -> list[dict]:
    out = []
    for j in jobs:
        p = j.get("progress") or {}
        if p.get("pid") and not p.get("waiting") and j.get("state") in MID_STEP:
            out.append(j)
    return out


def _resume(projects: list[int]) -> None:
    """Let go exactly the jobs the update paused (pause-all answers with project ids; /jobs/{id}/pause takes job ids)."""
    if not projects:
        return
    wanted = set(projects)
    for j in _api("GET", "/jobs") or []:
        if j.get("project_id") in wanted and j.get("paused"):
            _api("POST", f"/jobs/{j['id']}/pause", {"paused": False})


def supported() -> bool:
    """Systems with an installer script that can restart this folder's services."""
    return sys.platform in ("darwin", "win32")


def _installer(action: str) -> list[str]:
    """The installer of this system with a subcommand (restart / stop), for THIS folder's API, worker and UI."""
    if sys.platform == "win32":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(ROOT / "scripts" / "install" / "windows.ps1"), action]
    return ["bash", str(ROOT / "scripts" / "install" / "mac.sh"), action]


def _installer_name() -> str:
    return "INSTALAR.bat" if sys.platform == "win32" else "INSTALAR.command"


def _restart() -> None:
    _sh(_installer("restart"), ROOT)


def _stop_for_sync() -> None:
    """Windows locks the files a running program uses: uv sync cannot replace the .venv while the API and the worker
    run from it, so they stop first (the restart after the sync starts them again). The Mac syncs under them."""
    if sys.platform == "win32":
        _sh(_installer("stop"), ROOT)


def _exe(name: str) -> str:
    """Windows runs npm as npm.cmd, which CreateProcess finds only by its full name."""
    if sys.platform == "win32":
        import shutil

        return shutil.which(name) or name
    return name


def _sync() -> None:
    _stop_for_sync()
    _sh([_exe("uv"), "sync"], ROOT / "backend")
    _sh([_exe("npm"), "install", "--no-audit", "--no-fund"], ROOT / "frontend")
    # npm/uv may rewrite their lock files; the tree was clean before, so put them back (the next update checks it)
    _git("checkout", "-q", "--", ".", check=False)


# ---------- start / run ----------

def check_can_start() -> None:
    """Raises ValueError (pt-BR) when an update cannot start now."""
    if not supported():
        raise ValueError("A atualização pelo app ainda não é suportada neste sistema. Use o git na pasta do CVM.")
    if not public_install():
        raise ValueError("Esta pasta não veio do repositório público do CVM.")
    if running():
        raise ValueError("Já tem uma atualização em andamento.")
    if _dirty():
        raise ValueError("Há arquivos do CVM mudados nesta pasta. Desfaça as mudanças (ou guarde uma cópia) antes "
                         "de atualizar.")
    try:
        st = status()
        available = st["available"]
    except (StepFailed, OSError, subprocess.SubprocessError) as e:
        raise ValueError("Não deu para ver se há versão nova. Confira a internet e tente de novo.") from e
    if not available:
        if st.get("skipped"):
            raise ValueError(f"A versão {st['skipped']} não funcionou neste computador e o CVM voltou para a anterior. "
                             "O botão volta quando sair uma versão mais nova.")
        raise ValueError("Você já está na versão mais nova.")


def start() -> None:
    """Spawn the run detached (it survives the restart of the API that started it)."""
    global _PROC

    with _START:
        check_can_start()
        log = settings.data_dir / "logs" / "update.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        _PROC = subprocess.Popen([sys.executable, "-m", "cvm.setup.update", "run"], cwd=ROOT / "backend",
                                 stdout=log.open("w"), stderr=subprocess.STDOUT, **detached())
        _save({"state": "running", "pid": _PROC.pid, "step": "starting", "label": "Começando a atualização",
               "started_at": _now()})


def run() -> int:
    """The update itself (in the detached process). 0 = updated, 1 = failed (rolled back as far as possible).

    While Setting "update" says running (with this live pid) the worker starts no new step (hold_active), so nothing
    begins between the boundary check and the restart. The hold ends (finally) before the paused jobs are let go."""
    st: dict = {"state": "running", "pid": os.getpid(), "started_at": _now(), "paused": [], "warning": None}

    def step(name: str, label: str) -> None:
        st.update(step=name, label=label)
        _save(dict(st))
        print(label, flush=True)

    def pause() -> None:
        for p in _api("POST", "/jobs/pause-all", {"paused": True}).get("changed") or []:
            if p not in st["paused"]:
                st["paused"].append(p)
        _save(dict(st))

    previous: str | None = None
    target: str | None = None
    moved = False
    result = 1
    try:
        try:
            step("checking", "Conferindo a versão nova")
            if _dirty():
                raise StepFailed("há arquivos do CVM mudados nesta pasta")
            _LATEST.pop(str(ROOT), None)
            target = latest()
            if not target:
                raise StepFailed("não achei nenhuma versão no repositório público")
            previous = current() or _git("rev-parse", "HEAD")
            st.update({"from": previous, "to": target})

            step("pausing", "Pausando a fila")
            pause()
            _wait_boundary(step)
            # second net: a job that started meanwhile (approved, resumed by hand) is paused and waited for too
            pause()
            _wait_boundary(step)

            step("downloading", f"Baixando a versão {target}")
            _git("fetch", "--tags", "origin", timeout=300)
            _git("checkout", "-q", target, timeout=120)
            moved = True
            step("installing", "Atualizando as bibliotecas (pode levar alguns minutos)")
            _sync()
            step("restarting", "Reiniciando o CVM")
            _restart()
            st.update(state="done", error=None)
            result = 0
        except Exception as e:  # noqa: BLE001 — every failure ends in a rollback and a readable state
            reason = str(e) if isinstance(e, StepFailed) else f"erro inesperado: {e}"
            error = f"A atualização para {target or 'a versão nova'} não deu certo: {reason}."
            if moved and target:
                try:
                    _remember_failed(target)  # not offered again until a newer tag appears
                except Exception:  # noqa: BLE001 — the rollback matters more
                    pass
            if moved and previous:
                try:
                    step("rollback", f"Voltando para a versão {previous}")
                    _git("checkout", "-q", "-f", previous, timeout=120)
                    _sync()
                    _restart()
                    error += f" O CVM voltou para a versão {previous}."
                except Exception as e2:  # noqa: BLE001
                    error += (f" Também não deu para voltar para a versão {previous} ({e2}). Abra a pasta do CVM e "
                              f"rode {_installer_name()} de novo.")
            st.update(state="failed", error=error)
            print(error, flush=True)
    finally:  # releases the hold, whatever happened (an interrupted run included)
        if st["state"] == "running":
            st.update(state="failed", error="A atualização foi interrompida. Veja data/logs/update.log.")
        st["finished_at"] = _now()
        _LATEST.pop(str(ROOT), None)
        step(st["state"], f"Atualizado para a versão {target}" if st["state"] == "done" else "A atualização falhou")
    try:
        _resume(st["paused"])
    except Exception as e:  # noqa: BLE001 — the update itself stands; only the queue needs a hand
        st["warning"] = f"A fila ficou pausada ({e}). Retome os vídeos na Fábrica."
        _save(dict(st))
        print(st["warning"], flush=True)
    return result


def _wait_boundary(step) -> None:
    """Paused jobs stop after their current step: wait until no job is mid-step."""
    waited = 0.0
    while True:
        busy = _mid_step(_api("GET", "/jobs") or [])
        if not busy:
            return
        if waited >= WAIT_MAX_S:
            raise StepFailed("a fila não chegou ao fim de uma etapa em 6 horas")
        names = ", ".join(f"#{j.get('project_id')}" for j in busy[:5])
        step("waiting", f"Esperando {len(busy)} vídeo(s) terminarem a etapa atual ({names})")
        _sleep(POLL_S)
        waited += POLL_S


def main(argv: list[str]) -> int:
    if argv[:1] != ["run"]:
        print("uso: python -m cvm.setup.update run")
        return 2
    from cvm.db import init_db

    init_db()
    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
