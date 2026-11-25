"""Install wizard: what each feature needs and whether this machine can run it. Nothing here downloads anything.

Each feature lists its engines as tiers, best first (owner, 2026-11-25: "não posso simplesmente remover um recurso"):
the machine gets the best tier that fits its memory (VRAM on Windows, RAM on the Mac) and passes the wizard's engine
test (cvm/setup/engine_test.py). A feature is blocked only when no tier fits. The Mac keeps one tier per feature.

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


@dataclass
class Tier:
    """One engine that can run a feature."""
    engine: str
    label: str
    need: Need
    requires: list[str] = field(default_factory=list)  # other features this engine runs on (kids on Windows)
    available: bool = True
    reason: str | None = None  # pt-BR: why it is not available yet


@dataclass
class Feature:
    label: str
    mac_tiers: list[Tier]
    windows_tiers: list[Tier]
    requires: list[str] = field(default_factory=list)

    def tiers(self, platform: str) -> list[Tier]:
        return self.mac_tiers if platform == "mac" else self.windows_tiers if platform == "windows" else []

    @property
    def mac(self) -> Need | None:
        """The Mac's (single) tier."""
        return self.mac_tiers[0].need if self.mac_tiers else None

    @property
    def windows(self) -> Need | None:
        """The best Windows tier (for "base", the installer's gate)."""
        return self.windows_tiers[0].need if self.windows_tiers else None


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


_DETECTED: list[Hardware] = []


def detected() -> Hardware:
    """detect() once per process (nvidia-smi is a subprocess): what the pipeline uses to pick engines."""
    if not _DETECTED:
        _DETECTED.append(detect())
    return _DETECTED[0]


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


_LTX2_1B = "LTX-2 completo no Windows ainda não foi ligado (parte 1B)"
_ACE = "ACE-Step-1.5-cuda"

FEATURES: dict[str, Feature] = {
    "base": Feature(
        "Base (voz, legendas e imagens de banco)",
        [Tier("base", "Base", Need(ram_gb=32, disk_gb=15, models=["voxcpm2", "whisper", "clip"]))],
        # Windows row: scripts/install/windows.ps1 repeats ram/vram/disk (keep in sync)
        [Tier("base", "Base", Need(ram_gb=32, vram_gb=8, disk_gb=25, models=["voxcpm2", "whisper", "clip"]))]),
    "ai_images": Feature(
        "Imagens por IA",
        [Tier("z-image", "Z-Image Turbo e FLUX schnell (MLX)",
              Need(ram_gb=32, disk_gb=30, tools=["VLMJudge"], models=["z-image-turbo", "flux-schnell", "seedvr2"],
                   shared=["gemma"]))],
        [Tier("flux-bf16", "FLUX schnell completo (bf16)", Need(vram_gb=24, disk_gb=35, models=["flux-schnell-bf16"])),
         Tier("flux-8bit", "FLUX schnell em 8 bits, com descarga para a RAM",
              Need(vram_gb=12, disk_gb=35, models=["flux-schnell-bf16"])),
         Tier("flux-nf4", "FLUX schnell em 4 bits, com descarga para a RAM",
              Need(vram_gb=8, disk_gb=15, models=["flux-schnell"]))],
        ["base"]),
    "avatar": Feature(
        "Avatar falando",
        [Tier("ltx-mlx", "LTX (MLX)", Need(ram_gb=48, disk_gb=1, tools=["LTX2MLX", "RobustVideoMatting"], models=[_LTX],
                                           shared=["ltx-pack", "ltx2mlx", "gemma", "ltx-refine"]))],
        [Tier("ltx2-cuda", "LTX-2 completo", Need(vram_gb=24), available=False, reason=_LTX2_1B),
         # official LTX-2.3 audio-to-video (Lightricks/LTX-2, A2VidPipelineTwoStage) in FP8 with the weights in RAM:
         # the same look as the Mac. Gemma 3 is gated by Google (licence accepted once on Hugging Face).
         Tier("ltx2-q", "LTX-2 em 8 bits, com descarga para a RAM (a mesma aparência do Mac)",
              Need(vram_gb=8, ram_gb=32, disk_gb=5, tools=["LTX-2"], models=["ltx2.3-dev", "gemma3-qat"])),
         Tier("latentsync", "LatentSync 1.5 (mexe a boca sobre um vídeo do avatar)",
              Need(vram_gb=8, disk_gb=12, tools=["LatentSync"])),
         Tier("musetalk", "MuseTalk 1.5 (mexe a boca, mais leve)", Need(vram_gb=6, disk_gb=8, tools=["MuseTalk"]))],
        ["base"]),
    "animation": Feature(
        "Animação de cenas",
        [Tier("ltx-mlx", "LTX (MLX)", Need(ram_gb=48, tools=["LTX2MLX"], models=[_LTX],
                                           shared=["ltx-pack", "ltx2mlx", "gemma", "ltx-refine"]))],
        [Tier("ltx2-cuda", "LTX-2", Need(vram_gb=24), available=False, reason=_LTX2_1B),
         Tier("wan-5b", "Wan 2.2 TI2V-5B, com descarga para a RAM", Need(vram_gb=12, disk_gb=35, models=["wan-ti2v-5b"])),
         Tier("ltxv-2b", "LTX-Video 2B (imagem para vídeo)", Need(vram_gb=8, disk_gb=28, models=["ltxv-2b"]))],
        ["base"]),
    "kids": Feature(
        "Séries infantis",
        [Tier("klein-ltx", "FLUX.2 Klein e LTX (MLX)",
              Need(ram_gb=48, disk_gb=10, tools=["LTX2MLX"], models=["flux2-klein", _LTX],
                   shared=["ltx-pack", "ltx2mlx", "gemma", "ltx-refine"]))],
        # the images and animation of this machine, plus the characters' voices (VoxCPM2, in base)
        [Tier("series", "As imagens e a animação deste computador", Need(vram_gb=8),
              requires=["ai_images", "animation"])],
        ["base"]),
    "music": Feature(
        "Música própria",
        [Tier("acestep", "ACE-Step 1.5", Need(ram_gb=32, disk_gb=15, tools=["ACE-Step-1.5"]))],
        [Tier("acestep", "ACE-Step 1.5 turbo", Need(vram_gb=16, disk_gb=15, tools=[_ACE])),
         Tier("acestep-offload", "ACE-Step 1.5 turbo, com descarga para a RAM", Need(vram_gb=8, disk_gb=15, tools=[_ACE]))],
        ["base"]),
}


def tiers(key: str, platform: str) -> list[Tier]:
    return FEATURES[key].tiers(platform)


def fits(tier: Tier, hw: Hardware) -> bool:
    n = tier.need
    return not (n.ram_gb and hw.ram_gb < n.ram_gb) and not (n.vram_gb and hw.vram_gb < n.vram_gb)


def pick(key: str, platform: str, hw: Hardware | None = None, saved: str | None = None, failed=()) -> Tier | None:
    """The tier this machine uses for a feature: `saved` (the engine test's or the person's choice) when it is an
    available tier of this platform, else the best available tier that fits `hw` (any, when hw is unknown) and did
    not fail its test. None = no tier can run here."""
    ts = tiers(key, platform)
    by = {t.engine: t for t in ts}
    if saved in by and by[saved].available:
        return by[saved]
    return next((t for t in ts if t.available and t.engine not in failed and (hw is None or fits(t, hw))), None)


def _fmt(n: float) -> str:
    return f"{n:g}"


def _blocker(key: str, hw: Hardware) -> Blocker | None:
    """Blocked only when NO tier fits this machine; the reason names what the lightest tier needs."""
    f = FEATURES[key]
    ts = f.tiers(hw.platform)
    if not ts:
        return Blocker(key, f"{f.label} não funciona neste computador")
    if pick(key, hw.platform, hw) is not None:
        return None
    usable = [t for t in ts if t.available]
    if not usable:
        return Blocker(key, f"{f.label} ainda não está disponível aqui: {ts[0].reason or 'em preparo'}")
    need = usable[-1].need  # the lightest one
    if need.ram_gb and hw.ram_gb < need.ram_gb:
        who = "seu Mac" if hw.platform == "mac" else "seu computador"
        return Blocker(key, f"{f.label} precisa de {_fmt(need.ram_gb)} GB de memória; {who} tem {_fmt(hw.ram_gb)} GB")
    return Blocker(key, f"{f.label} precisa de {_fmt(need.vram_gb)} GB de memória da placa de vídeo; "
                        f"a sua tem {_fmt(hw.vram_gb)} GB")


def gate(hw: Hardware) -> Blocker | None:
    """Base row only: a machine that fails it cannot run the factory at all."""
    return _blocker("base", hw)


def _expand(features: list[str], platform: str | None = None, hw: Hardware | None = None,
            saved: dict | None = None) -> list[str]:
    """The selection plus everything it requires (transitively), in a stable order. With a platform, what the tier
    picked there requires counts too (kids on Windows runs on ai_images + animation)."""
    out: list[str] = []

    def add(k: str) -> None:
        if k not in FEATURES:
            raise ValueError(f"Recurso desconhecido: {k!r}")
        if k in out:
            return
        out.append(k)
        for r in FEATURES[k].requires:
            add(r)
        if platform and (t := pick(k, platform, hw, (saved or {}).get(k))):
            for r in t.requires:
                add(r)

    for k in features:
        add(k)
    return out


def chosen(features: list[str], platform: str, hw: Hardware | None = None, saved: dict | None = None) -> dict[str, Tier]:
    """{feature: tier} for the selection (requires included) on that platform. A feature no tier of which fits `hw`
    gets its lightest available tier (it is blocked anyway; the plan still shows what it would take); features with
    no available tier at all are left out."""
    out: dict[str, Tier] = {}
    for k in _expand(features, platform, hw, saved):
        t = pick(k, platform, hw, (saved or {}).get(k)) or next(
            (x for x in reversed(tiers(k, platform)) if x.available), None)
        if t is not None:
            out[k] = t
    return out


def disk_gb(features: list[str], platform: str, hw: Hardware | None = None, saved: dict | None = None) -> float:
    """Disk the selection needs: each picked tier's own share plus the shared components once. Never less than what
    install.plan() downloads on an empty machine (the per-feature numbers also cover venvs and caches)."""
    needs = [t.need for t in chosen(features, platform, hw, saved).values()]
    shared = {s for n in needs for s in n.shared}
    return sum(n.disk_gb for n in needs) + sum(shared_gb(s) for s in shared)


def check(features: list[str], hw: Hardware, missing_gb: float | None = None, saved: dict | None = None) -> list[Blocker]:
    """Hardware blockers per feature, plus "disk" when the install does not fit. `missing_gb` = what install.plan()
    still has to download (items already on this machine cost nothing); None = unknown → the static estimate."""
    features = _expand(features, hw.platform, hw, saved)
    out = [b for k in features if (b := _blocker(k, hw))]
    total = disk_gb(features, hw.platform, hw, saved) if missing_gb is None else round(missing_gb, 1)
    room = hw.disk_free_gb - DISK_RESERVE_GB
    if total > room:
        out.append(Blocker("disk", f"A instalação precisa de {_fmt(total)} GB livres (mais {DISK_RESERVE_GB} GB de folga); "
                                   f"o disco tem {_fmt(round(hw.disk_free_gb))} GB"))
    return out
