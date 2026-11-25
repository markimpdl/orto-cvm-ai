"""Install wizard: what each feature needs and whether this machine can run it. Nothing here downloads anything.

Model ids are the ones in models_registry (MODELS / CUDA) or model_catalog (M); tools are folders under models/.
"""

import platform as _platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from cvm.config import settings

DISK_RESERVE_GB = 20
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Hardware:
    platform: Literal["mac", "windows", "other"]
    chip: str
    ram_gb: float
    vram_gb: float
    disk_free_gb: float


@dataclass
class Need:
    ram_gb: float = 0
    vram_gb: float = 0
    disk_gb: float = 0
    tools: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    shared: list[str] = field(default_factory=list)  # components counted once across features (SHARED_GB)
    pending_1b: bool = False  # not on Windows yet (part 1B): the wizard says it needs a 24 GB card


@dataclass
class Feature:
    label: str
    mac: Need | None
    windows: Need | None
    requires: list[str] = field(default_factory=list)


@dataclass
class Blocker:
    feature: str
    reason: str


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def _disk_free_gb() -> float:
    try:
        return shutil.disk_usage(REPO_ROOT).free / 1e9
    except OSError:
        return 0.0


def detect() -> Hardware:
    disk = _disk_free_gb()
    if sys.platform == "darwin" and _platform.machine() == "arm64":
        try:
            ram = int(_run(["sysctl", "-n", "hw.memsize"]) or 0) / 1024**3
        except ValueError:
            ram = 0.0
        chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple Silicon"
        return Hardware("mac", chip, round(ram, 1), 0, disk)
    if sys.platform == "win32":
        chip, vram = "", 0.0
        if shutil.which("nvidia-smi"):
            line = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]).splitlines()
            if line:
                name, _, mem = line[0].rpartition(",")
                chip = name.strip()
                try:
                    vram = float(mem) / 1024
                except ValueError:
                    vram = 0.0
        ram = 0.0
        try:
            import psutil
            # whole GB: Windows reports the installed RAM minus what the hardware reserves (32 GB → ~31.8), and
            # scripts/install/windows.ps1 rounds the same way, so both gates agree
            ram = float(round(psutil.virtual_memory().total / 1024**3))
        except Exception:
            pass
        return Hardware("windows", chip, ram, round(vram, 1), disk)
    return Hardware("other", _platform.processor() or "", 0, 0, disk)


_LTX = f"ltx-{settings.ltx_pack}"  # model_catalog id of the pack in use (ltx-2.5 / ltx-2.3)

LTX_PACK_GB = {"2.5": 75, "2.3": 30}  # model_catalog sizes of dgrauet/ltx-<v>-mlx-q8


def shared_gb(key: str) -> float:
    """Disk of a component several features use; the LTX pack follows settings.ltx_pack at call time."""
    if key == "ltx-pack":
        return LTX_PACK_GB.get(settings.ltx_pack, LTX_PACK_GB["2.5"])
    if key == "ltx-refine":  # the presenter's Refine-Details LoRA comes with the 2.5 pack only (install.DEPS)
        return 1 if settings.ltx_pack not in LTX_PACK_GB or settings.ltx_pack == "2.5" else 0
    # gemma: Gemma 3 12B, LTX's text encoder and the visual judge's model (install.DEPS)
    return {"ltx2mlx": 5, "gemma": 9}[key]


FEATURES: dict[str, Feature] = {
    "base": Feature("Base (voz, legendas e imagens de banco)",
                    Need(ram_gb=32, disk_gb=15, models=["voxcpm2", "whisper", "clip"]),
                    # Windows row: scripts/install/windows.ps1 repeats ram/vram/disk (keep in sync)
                    Need(ram_gb=32, vram_gb=8, disk_gb=25, models=["voxcpm2", "whisper", "clip"])),
    "ai_images": Feature("Imagens por IA",
                         Need(ram_gb=32, disk_gb=30, tools=["VLMJudge"],
                              models=["z-image-turbo", "flux-schnell", "seedvr2"], shared=["gemma"]),
                         Need(vram_gb=16, disk_gb=15, models=["flux-schnell"]), ["base"]),
    "avatar": Feature("Avatar falando",
                      Need(ram_gb=48, disk_gb=1, tools=["LTX2MLX", "RobustVideoMatting"], models=[_LTX],
                           shared=["ltx-pack", "ltx2mlx", "gemma", "ltx-refine"]),
                      Need(vram_gb=24, pending_1b=True), ["base"]),
    "animation": Feature("Animação de cenas",
                         Need(ram_gb=48, tools=["LTX2MLX"], models=[_LTX], shared=["ltx-pack", "ltx2mlx", "gemma", "ltx-refine"]),
                         Need(vram_gb=24, pending_1b=True), ["base"]),
    "kids": Feature("Séries infantis",
                    Need(ram_gb=48, disk_gb=10, tools=["LTX2MLX"], models=["flux2-klein", _LTX],
                         shared=["ltx-pack", "ltx2mlx", "gemma", "ltx-refine"]),
                    Need(vram_gb=24, pending_1b=True), ["base"]),
    "music": Feature("Música própria",
                     Need(ram_gb=32, disk_gb=15, tools=["ACE-Step-1.5"]),
                     Need(vram_gb=12, disk_gb=15), ["base"]),
}


def _need(feature: Feature, hw: Hardware) -> Need | None:
    return feature.mac if hw.platform == "mac" else feature.windows if hw.platform == "windows" else None


def _fmt(n: float) -> str:
    return f"{n:g}"


def _blocker(key: str, hw: Hardware) -> Blocker | None:
    f = FEATURES[key]
    need = _need(f, hw)
    if need is None:
        return Blocker(key, f"{f.label} não funciona neste computador")
    if need.pending_1b:
        return Blocker(key, f"{f.label} precisa de uma placa de vídeo com {_fmt(need.vram_gb)} GB e ainda não está "
                            "disponível no Windows")
    if need.ram_gb and hw.ram_gb < need.ram_gb:
        who = "seu Mac" if hw.platform == "mac" else "seu computador"
        return Blocker(key, f"{f.label} precisa de {_fmt(need.ram_gb)} GB de memória; {who} tem {_fmt(hw.ram_gb)} GB")
    if need.vram_gb and hw.vram_gb < need.vram_gb:
        return Blocker(key, f"{f.label} precisa de {_fmt(need.vram_gb)} GB de memória da placa de vídeo; "
                            f"a sua tem {_fmt(hw.vram_gb)} GB")
    return None


def gate(hw: Hardware) -> Blocker | None:
    """Base row only: a machine that fails it cannot run the factory at all."""
    return _blocker("base", hw)


def _expand(features: list[str]) -> list[str]:
    """The selection plus everything it requires (transitively), in a stable order."""
    out: list[str] = []

    def add(k: str) -> None:
        if k not in FEATURES:
            raise ValueError(f"Recurso desconhecido: {k!r}")
        if k in out:
            return
        out.append(k)
        for r in FEATURES[k].requires:
            add(r)

    for k in features:
        add(k)
    return out


def disk_gb(features: list[str], platform: str) -> float:
    """Disk the selection needs: each feature's own share plus the shared components once. Never less than what
    install.plan() downloads on an empty machine (the per-feature numbers also cover venvs and caches)."""
    feats = _expand(features)
    needs = [n for k in feats if (n := (FEATURES[k].mac if platform == "mac" else
                                         FEATURES[k].windows if platform == "windows" else None))]
    shared = {s for n in needs for s in n.shared}
    return sum(n.disk_gb for n in needs) + sum(shared_gb(s) for s in shared)


def check(features: list[str], hw: Hardware, missing_gb: float | None = None) -> list[Blocker]:
    """Hardware blockers per feature, plus "disk" when the install does not fit. `missing_gb` = what install.plan()
    still has to download (items already on this machine cost nothing); None = unknown → the static estimate."""
    features = _expand(features)
    out = [b for k in features if (b := _blocker(k, hw))]
    total = disk_gb(features, hw.platform) if missing_gb is None else round(missing_gb, 1)
    room = hw.disk_free_gb - DISK_RESERVE_GB
    if total > room:
        out.append(Blocker("disk", f"A instalação precisa de {_fmt(total)} GB livres (mais {DISK_RESERVE_GB} GB de folga); "
                                   f"o disco tem {_fmt(round(hw.disk_free_gb))} GB"))
    return out
