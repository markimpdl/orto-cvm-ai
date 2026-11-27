"""Third-party tools the pipeline runs from their own folders under settings.models_dir (own venvs, so the backend's
pins stay as they are). Each one: where to clone it from and at which commit, the commands that build it (run inside
its folder), our glue scripts copied into it (backend/cvm/setup/vendor/: our code, kept in git because models/ is not),
and ready(): the same file checks the pipeline makes before using it.

Windows rows (platform="windows", hardware tiers 2026-11-25): LTX-2 (official PyTorch, the presenter's a2v), LatentSync
and MuseTalk (lip-sync on small cards), ACE-Step on CUDA. Their venvs live at .venv\\Scripts\\python.exe
(venv_python). On CUDA machines matting uses the backend venv (cvm/avatar/matting.py).
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


def venv_python(folder: Path) -> Path:
    """A tool venv's interpreter: .venv\\Scripts\\python.exe on Windows, .venv/bin/python elsewhere."""
    return folder / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


# relative to the tool folder (setup commands run there); uv accepts forward slashes on Windows too
WIN_PY = ".venv/Scripts/python.exe"
CU128 = "https://download.pytorch.org/whl/cu128"  # RTX 50 (Blackwell, sm_120) needs torch ≥ 2.7 built for CUDA 12.8


def _pipw(*pkgs: str) -> list[str]:
    return ["uv", "pip", "install", "--python", WIN_PY, *pkgs]


def _fetch(*args: str) -> list[str]:
    return [WIN_PY, "cvm_fetch.py", *args]


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
    return venv_python(ace).exists() and (ace / "cvm_music.py").exists() and \
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
    # ---------------- Windows + NVIDIA (hardware tiers; UNVERIFIED on CUDA until the owner's RTX 5060 run) ----------
    # same repo and commit as the Mac row; its pyproject pins torch 2.7.1+cu128 for win32 (uv sync picks it)
    "ACE-Step-1.5-cuda": Tool(
        "https://github.com/ACE-Step/ACE-Step-1.5.git", "ca1e85fe9430179831e6bc6be790c332190a3866", "ACE-Step-1.5",
        [["uv", "sync", "--python", "3.12"],
         [sys.executable, "-c", "from huggingface_hub import snapshot_download; "
                                f"snapshot_download({ACE_CHECKPOINTS!r}, local_dir='checkpoints')"]],
        lambda: _ace_ready(), vendor=["cvm_music.py"], size_gb=11, platform="windows"),
    # official LTX-2 (PyTorch): the presenter's audio-to-video (ltx_pipelines.a2vid_two_stage) for the "ltx2-q" tier;
    # weights are the models "ltx2.3-dev" and "gemma3-qat" (models_registry). The repo ships no uv.lock, so no
    # --frozen: uv resolves from the pyproject (torch from the cu132 index set in packages/ltx-core)
    "LTX-2": Tool("https://github.com/Lightricks/LTX-2", "c3a7efb18256cba2d76cc32f5b47811d7c9ad642", "LTX-2",
                  [["uv", "sync"]], lambda: _ltx2_ready(), size_gb=6, platform="windows"),
    # LatentSync 1.5 (8 GB; 1.6 needs 18 GB). Upstream pins torch 2.5.1/cu121, which has no RTX 50 kernels: the venv
    # takes the cu128 torch the Mac bake-off also ran with (2.7.1), decord is stubbed and insightface uses the CPU
    # onnxruntime (cvm_latentsync.py), as on the Mac
    "LatentSync": Tool(
        "https://github.com/bytedance/LatentSync", "a229c3948406bc2cf6eaf4873e662e70c6a04746", "LatentSync",
        [["uv", "venv", "--python", "3.10", "--allow-existing", ".venv"],
         _pipw("--index-url", CU128, "torch==2.7.1", "torchvision==0.22.1"),
         _pipw("diffusers==0.32.2", "transformers==4.48.0", "accelerate==0.26.1", "einops==0.7.0", "omegaconf==2.3.0",
               "opencv-python==4.9.0.80", "python_speech_features==0.6", "librosa==0.10.1", "setuptools<80",
               "ffmpeg-python==0.2.0", "imageio==2.31.1", "imageio-ffmpeg==0.5.1", "huggingface-hub==0.30.2",
               "numpy==1.26.4", "kornia==0.8.0", "insightface==0.7.3", "onnxruntime==1.21.0", "DeepCache==0.1.1",
               "soundfile==0.12.1", "tqdm", "packaging"),
         _fetch("hf", "ByteDance/LatentSync-1.5", "checkpoints", "latentsync_unet.pt", "whisper/tiny.pt"),
         [WIN_PY, "-c", "from huggingface_hub import snapshot_download; snapshot_download('stabilityai/sd-vae-ft-mse', "
                        "allow_patterns=['config.json', 'diffusion_pytorch_model.safetensors'])"],
         _fetch("zip", "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
                "checkpoints/auxiliary/models/buffalo_l", "det_10g.onnx", "2d106det.onnx")],
        lambda: _latentsync_ready(), vendor=["cvm_latentsync.py", "cvm_fetch.py"], size_gb=8, platform="windows"),
    # MuseTalk 1.5 (4–6 GB). mmcv/mmpose (DWPose) are replaced by face-alignment in cvm_musetalk.py, as on the Mac
    "MuseTalk": Tool(
        "https://github.com/TMElyralab/MuseTalk", "0a89dec45a0192b824e3cf4daf96c239440c5ed8", "MuseTalk",
        [["uv", "venv", "--python", "3.10", "--allow-existing", ".venv"],
         _pipw("--index-url", CU128, "torch==2.7.1", "torchvision==0.22.1"),
         _pipw("diffusers==0.30.2", "accelerate==0.28.0", "numpy==1.23.5", "opencv-python==4.9.0.80",
               "soundfile==0.12.1", "transformers==4.39.2", "huggingface_hub==0.30.2", "librosa==0.11.0",
               "einops==0.8.1", "omegaconf==2.3.0", "ffmpeg-python==0.2.0", "face-alignment==1.4.1", "tqdm",
               "more-itertools", "gdown==5.2.0"),
         _fetch("hf", "TMElyralab/MuseTalk", "models", "musetalkV15/musetalk.json", "musetalkV15/unet.pth"),
         _fetch("hf", "stabilityai/sd-vae-ft-mse", "models/sd-vae", "config.json", "diffusion_pytorch_model.bin"),
         _fetch("hf", "openai/whisper-tiny", "models/whisper", "config.json", "pytorch_model.bin",
                "preprocessor_config.json"),
         _fetch("hf", "ByteDance/LatentSync-1.5", "models/face-alignment", "auxiliary/s3fd-619a316812.pth",
                "auxiliary/2DFAN4-cd938726ad.zip"),
         _fetch("copy", "models/face-alignment/auxiliary/2DFAN4-cd938726ad.zip",
                "models/face-alignment/hub/checkpoints/2DFAN4-cd938726ad.zip"),
         _fetch("gdrive", "154JgKpzCPW82qINcVieuPH3fZ2e0P812", "models/face-parse-bisent/79999_iter.pth"),
         _fetch("url", "https://download.pytorch.org/models/resnet18-5c106cde.pth",
                "models/face-parse-bisent/resnet18-5c106cde.pth")],
        lambda: _musetalk_ready(), vendor=["cvm_musetalk.py", "cvm_fetch.py"], size_gb=6, platform="windows"),
}


def _ltx2_ready() -> bool:
    # avatar.ltx2_cuda.ready, tool half
    d = _dir("LTX-2")
    return venv_python(d).exists() and (d / "packages" / "ltx-pipelines").exists()


def _latentsync_ready() -> bool:
    # avatar.lipsync_cuda.ready("latentsync")
    d = _dir("LatentSync")
    return all(p.exists() for p in (venv_python(d), d / "cvm_latentsync.py", d / "checkpoints" / "latentsync_unet.pt",
                                    d / "checkpoints" / "whisper" / "tiny.pt",
                                    d / "checkpoints" / "auxiliary" / "models" / "buffalo_l" / "det_10g.onnx"))


def _musetalk_ready() -> bool:
    # avatar.lipsync_cuda.ready("musetalk")
    d = _dir("MuseTalk")
    m = d / "models"
    return all(p.exists() for p in (venv_python(d), d / "cvm_musetalk.py", m / "musetalkV15" / "unet.pth",
                                    m / "sd-vae" / "diffusion_pytorch_model.bin", m / "whisper" / "pytorch_model.bin",
                                    m / "face-parse-bisent" / "79999_iter.pth"))
