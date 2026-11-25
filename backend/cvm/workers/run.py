"""Start the Huey consumer: `uv run cvm-worker`."""

import sys

from huey.bin.huey_consumer import consumer_main


def _resume_interrupted() -> None:
    """Re-queue jobs left mid-step by a previous worker (restart/crash); the step reruns from its start."""
    from sqlmodel import Session, select

    from cvm.db import engine
    from cvm.models import Job
    from cvm.workers.tasks import run_job, running_states

    with Session(engine) as session:
        for job in session.exec(select(Job).where(Job.state.in_(running_states()))).all():
            print(f"retomando job {job.id} ({job.state}) interrompido")
            run_job(job.id)


def _log_to_file() -> None:
    """Also keep the worker's log in data/logs/worker.log (rotated): a worker started in the background (or whose
    Terminal window is gone) otherwise loses every warning, e.g. why an LTX presenter take failed."""
    import logging
    from logging.handlers import RotatingFileHandler

    from cvm.config import settings

    folder = settings.data_dir / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / "worker.log", maxBytes=20 * 2**20, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s:%(name)s:%(threadName)s:%(message)s"))
    # huey's per-task INFO lines (Added/Executing/executed every few seconds) would bury the useful ones
    handler.addFilter(lambda r: not (r.name.startswith("huey") and r.levelno < logging.WARNING))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)  # huey only sets its own logger's level; cvm's INFO lines were dropped


def main() -> None:
    from cvm.db import init_db

    _log_to_file()
    init_db()
    from cvm.scripting.claude_runner import stop_leftover_runs

    if stopped := stop_leftover_runs():
        print(f"encerrados claude -p órfãos: {stopped}")
    _resume_interrupted()
    sys.argv = ["huey_consumer", "cvm.workers.tasks.huey", "-w", "10", "-k", "thread", "-f"]  # 2 Claude + 1 GPU + 2 prep + 2 Crescer + daily research + periodic + spare  # -f: drop locks left by a killed worker
    consumer_main()
