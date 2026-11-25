"""Install wizard: installs the tools and models of the chosen features in the background, resumable.

plan(features, platform) lists what they need and what is already there; start(features) spawns
`python -m cvm.setup.install run <features…>` detached and keeps a lease in Setting "setup_install"
({"pid", "features", "items", "at"}); status() reads it back (a dead pid → nothing claims to be running).
The run installs only what is missing: a tool = git clone + checkout of the pinned commit + its setup commands + our
glue scripts; a model = models_registry.download (resumes partial files) or, for weights the registry does not list
(LTX packs, Gemma, SeedVR2, the Refine LoRA), a snapshot_download of the same files the pipeline checks for.
One item failing (error in pt-BR) never stops the next. scripts/download-models.sh runs the same `run`
(it also takes model ids, as before).
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from cvm import models_registry
from cvm.config import REPO_ROOT, settings
from cvm.setup import tools
from cvm.setup.requirements import FEATURES, LTX_PACK_GB, _expand

KEY = "setup_install"
PLATFORM = "mac" if sys.platform == "darwin" else "windows"
GEMMA = "mlx-community/gemma-3-12b-it-4bit"  # LTX text encoder (sources.ltx_ready) and the visual judge
# LTX-2.3: only the files the distilled path reads (sources.py: the whole repo is ~87 GB); 2.5: the whole repo (~75 GB)
LTX23_FILES = ["LICENSE", "README.md", "audio_vae.safetensors", "config.json", "connector.safetensors",
               "embedded_config.json", "quantize_config.json", "spatial_upscaler_x1_5_v1_0_config.json",
               "spatial_upscaler_x2_v1_1.safetensors", "spatial_upscaler_x2_v1_1_config.json", "split_model.json",
               "temporal_upscaler_x2_v1_0_config.json", "transformer-distilled-1.1.safetensors",
               "vae_decoder.safetensors", "vae_encoder.safetensors", "vocoder.safetensors"]
# LTX-2.5: every file of the repo (the live snapshot), so a pack cut halfway is not taken as ready
LTX25_FILES = ["LICENSE", "README.md", "audio_vae.safetensors", "chat_template.jinja", "connector.safetensors",
               "duration_head.safetensors", "embedded_config.json", "generation_config.json",
               "ltx-2.5-22b-distilled-lora-450-bf16.safetensors", "processor_config.json", "quantize_config.json",
               "spatial_upscaler_x2_v1_0.safetensors", "spatial_upscaler_x2_v1_0_config.json", "split_model.json",
               "temporal_upscaler_x2_v1_0.safetensors", "temporal_upscaler_x2_v1_0_config.json",
               "text_encoder.safetensors", "text_encoder_config.json", "tokenizer.json", "tokenizer_config.json",
               "transformer-dev.safetensors", "transformer-distilled.safetensors", "vae_decoder_av.safetensors",
               "vae_decoder_conv.safetensors", "vae_encoder_av.safetensors", "vae_encoder_conv.safetensors",
               "vocoder.safetensors"]
# items that only work with another one beside them
DEPS = {"ltx-2.5": ["gemma", "ltx-refine"], "ltx-2.3": ["gemma"], "VLMJudge": ["gemma"]}

_PROC: subprocess.Popen | None = None  # the run this API process started (poll() reaps it)
_START = threading.Lock()  # two quick POSTs (FastAPI threadpool) must not both spawn a run


@dataclass
class Item:
    kind: Literal["tool", "model"]
    id: str
    size_gb: float
    state: Literal["ready", "missing", "running", "failed"]
    error: str | None = None


# ---------- models the registry does not list ----------

def _extra_spec(mid: str) -> dict:
    from cvm.visuals import sources, upscale

    if mid.startswith("ltx-") and mid[4:] in sources.LTX_PACKS:
        v = mid[4:]
        return {"repo": sources.LTX_PACKS[v][0], "allow": LTX23_FILES if v == "2.3" else None,
                "size": LTX_PACK_GB.get(v, 75)}
    return {
        "ltx-refine": {"repo": sources.LTX_REFINE_REPO, "allow": None, "size": 1},
        "gemma": {"repo": GEMMA, "allow": None, "size": 9},
        "seedvr2": {"repo": upscale.REPO, "allow": [*upscale.FILES, "config.json"], "size": 7},
    }[mid]


EXTRA = {"ltx-2.5", "ltx-2.3", "ltx-refine", "gemma", "seedvr2"}


def _snapshots(repo: str):
    return models_registry.HF_CACHE / ("models--" + repo.replace("/", "--")) / "snapshots"


def _complete(snap, files: list[str]) -> bool:
    """Every expected file is in the snapshot and points to a finished blob. huggingface_hub links a file into
    snapshots/<rev>/ only once it has finished, so a download cut halfway leaves some of them out."""
    return all((snap / f).exists() and (snap / f).resolve().exists() for f in files)


def _gemma_files(snap) -> list[str]:
    """config.json plus every shard model-XXXXX-of-NNNNN.safetensors, N read from the shard names themselves: mlx loads
    *.safetensors and the repo's index.json lists a different shard count (5) than the files it ships (2)."""
    names = [p.name for p in snap.glob("model-*-of-*.safetensors")] or [p.name for p in snap.glob("model.safetensors")]
    m = next((re.match(r"model-\d+-of-(\d+)\.safetensors$", n) for n in names if "-of-" in n), None)
    if m:
        n = int(m.group(1))
        names = [f"model-{i:05d}-of-{n:05d}.safetensors" for i in range(1, n + 1)]
    return ["config.json", *(names or ["model.safetensors"])]


def _extra_ready(mid: str) -> bool:
    """The pipeline's checks (sources._ltx_pack / ltx_refine_lora, judge.ready's Gemma snapshot, upscale.ready's files;
    without the on/off switches, which are preferences), plus every file the download brings, so an interrupted
    download is resumed instead of being taken as installed."""
    from cvm.visuals import sources, upscale

    if mid in ("ltx-2.5", "ltx-2.3") and not sources._ltx_pack(mid[4:]):
        return False
    if mid == "ltx-refine":
        return bool(sources.ltx_refine_lora())
    repo = _extra_spec(mid)["repo"]
    snap = _snapshots(repo)
    if not snap.exists() or models_registry._pending_parts(snap.parent):
        return False
    files = {"ltx-2.5": LTX25_FILES, "ltx-2.3": LTX23_FILES, "seedvr2": list(upscale.FILES)}.get(mid)
    return any(_complete(d, files if files is not None else _gemma_files(d)) for d in snap.iterdir() if d.is_dir())


def _hf_download(mid: str) -> None:
    from huggingface_hub import snapshot_download

    s = _extra_spec(mid)
    snapshot_download(s["repo"], allow_patterns=s["allow"])


def ltx_id() -> str:
    """The LTX pack to install, from settings.ltx_pack at call time (the FEATURES table bound it at import)."""
    from cvm.visuals import sources

    return f"ltx-{settings.ltx_pack if settings.ltx_pack in sources.LTX_PACKS else '2.5'}"


# ---------- plan ----------

def _size_gb(text: str) -> float:
    m = re.search(r"([\d.]+)\s*(GB|MB)", text or "")
    return 0.0 if not m else round(float(m.group(1)) / (1000 if m.group(2) == "MB" else 1), 2)


def _model_ready(mid: str) -> bool:
    return _extra_ready(mid) if mid in EXTRA else models_registry.is_ready(mid)


def _ready(kind: str, iid: str) -> bool:
    try:
        return tools.TOOLS[iid].ready() if kind == "tool" else _model_ready(iid)
    except Exception:  # noqa: BLE001 — a check that breaks means "not there"
        return False


def _model_item(mid: str) -> Item:
    size = _extra_spec(mid)["size"] if mid in EXTRA else _size_gb(models_registry.MODELS[mid]["size"])
    return Item("model", mid, float(size), "ready" if _ready("model", mid) else "missing")


def _tool_item(name: str) -> Item:
    t = tools.TOOLS[name]
    return Item("tool", name, float(t.size_gb), "ready" if _ready("tool", name) else "missing")


def plan(features: list[str], platform: str) -> list[Item]:
    """Items for the features (plus what they require) on that platform, base first. Model ids and tool names are
    taken too (scripts/download-models.sh). Unknown names → ValueError."""
    feats = [n for n in features if n in FEATURES]
    loose = [n for n in features if n not in FEATURES]
    for n in loose:
        if n not in models_registry.MODELS and n not in EXTRA and n not in tools.TOOLS:
            raise ValueError(f"Recurso ou modelo desconhecido: {n!r}")
    expanded = set(_expand(feats))
    wanted: list[tuple[str, str]] = []
    for k in [k for k in FEATURES if k in expanded]:
        need = FEATURES[k].mac if platform == "mac" else FEATURES[k].windows
        if need is None or need.pending_1b:
            continue
        wanted += [("tool", t) for t in need.tools]
        wanted += [("model", ltx_id() if m in ("ltx-2.5", "ltx-2.3") else m) for m in need.models]
    wanted += [("tool" if n in tools.TOOLS else "model", n) for n in loose]

    out: list[Item] = []
    seen: set[str] = set()

    def add(kind: str, iid: str) -> None:
        if iid in seen:
            return
        if kind == "tool" and (iid not in tools.TOOLS or tools.TOOLS[iid].platform != platform):
            return  # Windows tools: part 1B
        seen.add(iid)
        out.append(_tool_item(iid) if kind == "tool" else _model_item(iid))
        for dep in DEPS.get(iid, []):
            add("model", dep)

    for kind, iid in wanted:
        add(kind, iid)
    return out


def missing_gb(items: list[Item]) -> float:
    """Disk the items not yet on this machine take: the one number the wizard shows and the disk check uses."""
    return round(sum(i.size_gb for i in items if i.state != "ready"), 1)


# ---------- installing ----------

def _sh(cmd: list[str], cwd=None) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        tail = (r.stderr or r.stdout or "").strip()[-400:]
        raise RuntimeError(f"o comando {' '.join(cmd[:3])} falhou: {tail or f'código {r.returncode}'}")


def install_tool(name: str) -> None:
    """Clone into <folder>.partial and move it into place only once the pinned commit is checked out: a clone that
    was killed halfway never sits in the real folder looking like a clone (a retry starts the clone over)."""
    t = tools.TOOLS[name]
    folder = settings.models_dir / t.folder
    if t.repo_url:
        if not (folder / ".git").exists():
            if folder.exists() and any(folder.iterdir()):
                raise RuntimeError(f"a pasta {folder} já existe e não é um clone do {name}; "
                                   "mova ou apague a pasta e tente de novo")
            partial = folder.with_name(folder.name + ".partial")
            shutil.rmtree(partial, ignore_errors=True)
            folder.parent.mkdir(parents=True, exist_ok=True)
            _sh(["git", "clone", t.repo_url, str(partial)])
            _sh(["git", "-C", str(partial), "checkout", t.commit])
            if folder.exists():
                folder.rmdir()  # empty (checked above)
            partial.rename(folder)
        else:
            _sh(["git", "-C", str(folder), "checkout", t.commit])
    else:
        folder.mkdir(parents=True, exist_ok=True)
    for f in t.vendor:
        shutil.copy2(tools.VENDOR / f, folder / f)
    for cmd in t.setup:
        _sh(cmd, cwd=folder)


def install_model(mid: str) -> None:
    if mid in EXTRA:
        _hf_download(mid)
    else:
        models_registry.download([mid])


# ---------- lease ----------

def _engine():
    from cvm.db import engine

    return engine


def _load() -> dict | None:
    from sqlmodel import Session

    from cvm.models import Setting

    with Session(_engine()) as s:
        row = s.get(Setting, KEY)
        return dict(row.value) if row else None


def _save(value: dict) -> None:
    from sqlmodel import Session

    from cvm.models import Setting

    with Session(_engine()) as s:
        row = s.get(Setting, KEY)
        if row is None:
            s.add(Setting(key=KEY, value=value))
        else:
            row.value = value
        s.commit()


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


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def status() -> dict:
    lease = _load() or {}
    items = [dict(i) for i in lease.get("items", [])]
    running = _alive(lease.get("pid"))
    if not running:  # a run that died mid-item: nothing is "running"; recheck what is on disk now
        for it in items:
            if it["state"] == "failed":
                continue
            it["state"] = "ready" if _ready(it["kind"], it["id"]) else "missing"
    return {"running": running, "features": lease.get("features", []), "items": items, "at": lease.get("at")}


def start(features: list[str]) -> None:
    """Spawn the run detached (it outlives an API restart); a live run is not started twice."""
    global _PROC

    with _START:
        lease = _load()
        if lease and _alive(lease.get("pid")):
            return
        items = plan(features, PLATFORM)  # also validates the names before anything starts
        log = settings.data_dir / "logs" / "setup-install.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        _PROC = subprocess.Popen([sys.executable, "-m", "cvm.setup.install", "run", *features],
                                 cwd=REPO_ROOT / "backend", stdout=log.open("w"), stderr=subprocess.STDOUT,
                                 start_new_session=True)
        # the child rewrites this lease with the same pid as it goes
        _save({"pid": _PROC.pid, "features": list(features), "items": [asdict(i) for i in items], "at": _now()})


@contextlib.contextmanager
def _run_lock():
    """One runner per machine, whoever started it (the wizard or scripts/download-models.sh): a second one waits
    here for the first to finish, then installs only what is still missing."""
    path = settings.data_dir / "logs" / "setup-install.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as f:
        if sys.platform == "win32":
            import msvcrt
            import time

            f.seek(0)
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(2)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def run(names: list[str]) -> int:
    """Install every missing item; 1 when any failed (download-models.sh retries), else 0."""
    plan(names, PLATFORM)  # unknown names fail before waiting for the lock
    with _run_lock():
        return _run(names)


def _run(names: list[str]) -> int:
    items = plan(names, PLATFORM)  # after the lock: what an earlier runner installed is ready now
    lease = {"pid": os.getpid(), "features": list(names), "items": [], "at": _now()}

    def save() -> None:
        lease["items"] = [asdict(i) for i in items]
        _save(lease)

    save()
    failed = False
    for it in items:
        if it.state == "ready":
            continue
        it.state, it.error = "running", None
        save()
        print(f"instalando {it.id} ({it.size_gb:g} GB)…", flush=True)
        try:
            install_tool(it.id) if it.kind == "tool" else install_model(it.id)
            if not _ready(it.kind, it.id):
                raise RuntimeError("terminou, mas os arquivos não ficaram completos")
            it.state = "ready"
        except Exception as e:  # noqa: BLE001 — one item never stops the next
            it.state, it.error = "failed", f"Não foi possível instalar {it.id}: {e}"
            failed = True
            print(it.error, flush=True)
        save()
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "run":
        print("uso: python -m cvm.setup.install run <recurso ou modelo…>")
        return 2
    from cvm.db import init_db

    init_db()
    return run(argv[1:] or ["base"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
