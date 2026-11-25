"""Third-party tools the pipeline runs from their own folders under settings.models_dir (own venvs, so the backend's
pins stay as they are). Each one: where to clone it from and at which commit, the commands that build it (run inside
its folder), our glue scripts copied into it (backend/cvm/setup/vendor/: our code, kept in git because models/ is not),
and ready(): the same file checks the pipeline makes before using it.

Mac only for now; the Windows rows come with part 1B (on CUDA machines matting uses the backend venv, see
cvm/avatar/matting.py).
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cvm.config import settings

VENDOR = Path(__file__).resolve().parent / "vendor"
RVM_WEIGHTS = "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3.pth"
ACE_CHECKPOINTS = "ACE-Step/Ace-Step1.5"  # model_catalog "ace-step"; music_gen.ready() looks in ./checkpoints


@dataclass
class Tool:
    repo_url: str | None
    commit: str | None
    folder: str
    setup: list[list[str]]
    ready: Callable[[], bool]
    vendor: list[str] = field(default_factory=list)  # files from VENDOR copied into the folder
    size_gb: float = 0
    platform: str = "mac"


def _dir(name: str) -> Path:
    return settings.models_dir / name  # read at call time (tests and .env can move models_dir)


# ---------- ready(): the pipeline's own checks, with paths taken at call time ----------

def _ltx_ready() -> bool:
    # model_catalog._ltx / sources.ltx_ready / avatar.talking: the CLI the pipeline runs
    return (_dir("LTX2MLX") / ".venv" / "bin" / "ltx-2-mlx").exists()


def _rvm_ready() -> bool:
    # avatar.matting.matting_ready
    rvm = _dir("RobustVideoMatting")
    return all(p.exists() for p in (rvm / "weights" / "rvm_mobilenetv3.pth", rvm / "cvm_matte.py",
                                    rvm / ".venv" / "bin" / "python"))


def _ace_ready() -> bool:
    # render.music_gen.ready
    ace = _dir("ACE-Step-1.5")
    ck = ace / "checkpoints"
    return (ace / ".venv" / "bin" / "python").exists() and (ace / "cvm_music.py").exists() and \
        (ck / "acestep-v15-turbo").exists() and any(ck.glob("acestep-5Hz-lm-*"))


def _judge_ready() -> bool:
    # visuals.judge.ready, tool half (venv + judge.py); its Gemma weights are the "gemma" model item and the
    # settings.visual_judge switch is a preference, not something to install
    vj = _dir("VLMJudge")
    return (vj / ".venv" / "bin" / "python").exists() and (vj / "judge.py").exists()


def _pip(*pkgs: str) -> list[str]:
    return ["uv", "pip", "install", "--python", ".venv/bin/python", *pkgs]


TOOLS: dict[str, Tool] = {
    # uv workspace with its own uv.lock; the live venv is Python 3.11
    "LTX2MLX": Tool("https://github.com/dgrauet/ltx-2-mlx", "895201df379b079c2154738bcf92005b7a371923", "LTX2MLX",
                    [["uv", "sync", "--python", "3.11"]], _ltx_ready, size_gb=5),
    # no installer of its own: a venv with what cvm_matte.py imports (torch, torchvision for model/, cv2, numpy),
    # pinned to the live venv (Python 3.10), plus the mobilenetv3 weights from the repo's v1.0.0 release
    "RobustVideoMatting": Tool(
        "https://github.com/PeterL1n/RobustVideoMatting", "53d74c6826735f01f4406b5ca9075eee27bec094", "RobustVideoMatting",
        [["uv", "venv", "--python", "3.10", "--allow-existing", ".venv"],
         _pip("torch==2.7.1", "torchvision==0.22.1", "opencv-python==4.9.0.80", "numpy==1.23.5"),
         ["curl", "-L", "--fail", "--create-dirs", "-o", "weights/rvm_mobilenetv3.pth", RVM_WEIGHTS]],
        _rvm_ready, vendor=["cvm_matte.py"], size_gb=1),
    # uv project with its own uv.lock (installs itself editable, as the live venv); weights into ./checkpoints
    "ACE-Step-1.5": Tool(
        "https://github.com/ACE-Step/ACE-Step-1.5.git", "ca1e85fe9430179831e6bc6be790c332190a3866", "ACE-Step-1.5",
        [["uv", "sync", "--python", "3.12"],
         [sys.executable, "-c", "from huggingface_hub import snapshot_download; "
                                f"snapshot_download({ACE_CHECKPOINTS!r}, local_dir='checkpoints')"]],
        _ace_ready, vendor=["cvm_music.py"], size_gb=11),
    # not a clone: a venv with mlx-vlm (pinned to the live venv) + our judge.py
    "VLMJudge": Tool(None, None, "VLMJudge",
                     [["uv", "venv", "--python", "3.12", "--allow-existing", ".venv"],
                      _pip("mlx==0.32.4", "mlx-vlm==0.7.12", "transformers==5.22.2")],
                     _judge_ready, vendor=["judge.py"], size_gb=1),
}
