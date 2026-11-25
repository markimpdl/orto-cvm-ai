"""Presenter cut-out: RobustVideoMatting (models/RobustVideoMatting, GPL-3.0, run locally as an external tool)
turns the lipsynced presenter video into a .mov with alpha, so the presenter can sit over every scene.
The wrapper lives in bakeoff/matting/cvm_matte.py and is copied next to the RVM repo; it runs in RVM's own venv
(torch + torchvision; it was MuseTalk's venv, moved there when MuseTalk was removed on 2026-11-21)."""

import os
import subprocess
import sys
from pathlib import Path

from cvm.config import settings

RVM = settings.models_dir / "RobustVideoMatting"
PYTHON = RVM / ".venv" / "bin" / "python"
if sys.platform != "darwin" and not PYTHON.exists():  # CUDA machines: the backend venv already has torch/torchvision
    PYTHON = Path(sys.executable)


def matting_ready() -> bool:
    return all(p.exists() for p in (RVM / "weights" / "rvm_mobilenetv3.pth", RVM / "cvm_matte.py", PYTHON))


def matte(video: Path, out: Path) -> Path:
    from cvm import gpu

    with gpu.light():  # RVM is tiny: no need to wait for a long LTX/FLUX run
        proc = subprocess.run([str(PYTHON), str(RVM / "cvm_matte.py"), "--video", str(video), "--out", str(out)],
                              capture_output=True, text=True,
                              env={**os.environ, "CVM_FFMPEG": settings.ffmpeg_bin, "PYTORCH_ENABLE_MPS_FALLBACK": "1"},
                              timeout=1800)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"recorte do apresentador falhou: {proc.stderr[-1500:] or proc.stdout[-1500:]}")
    return out
