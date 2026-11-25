"""Cut the presenter out of a video with RobustVideoMatting (models/RobustVideoMatting, GPL-3.0, local use only).

Output: ProRes 4444 .mov with alpha (the original pixels + RVM's alpha), audio dropped.
Run with the RVM venv (torch + torchvision):
  models/RobustVideoMatting/.venv/bin/python models/RobustVideoMatting/cvm_matte.py --video in.mp4 --out out.mov
Kept in bakeoff/matting/ and copied next to the RVM repo (like the lipsync wrappers).
"""

import argparse
import os
import subprocess
import sys
import time

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

from model import MattingNetwork  # noqa: E402

FFMPEG = os.environ.get("CVM_FFMPEG", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--downsample", type=float, default=0.0, help="RVM downsample ratio (0 = auto: ~512 px on the long side)")
    args = ap.parse_args()

    t0 = time.time()
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    model = MattingNetwork("mobilenetv3").eval().to(device)
    model.load_state_dict(torch.load(os.path.join(REPO, "weights", "rvm_mobilenetv3.pth"), map_location="cpu"))

    cap = cv2.VideoCapture(args.video)
    w, h, fps = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), cap.get(cv2.CAP_PROP_FPS) or 25
    ratio = args.downsample or min(1.0, 512 / max(w, h))
    enc = subprocess.Popen(
        [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-",
         "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", args.out],
        stdin=subprocess.PIPE,
    )
    rec = [None] * 4
    n = 0
    with torch.no_grad():
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            src = torch.from_numpy(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).to(device).permute(2, 0, 1)[None].float() / 255
            _fgr, pha, *rec = model(src, *rec, downsample_ratio=ratio)
            alpha = (pha[0, 0].clamp(0, 1) * 255).byte().cpu().numpy()
            enc.stdin.write(np.dstack([frame, alpha]).tobytes())
            n += 1
    enc.stdin.close()
    enc.wait()
    print(f"[rvm] {n} frames {w}x{h} in {time.time() - t0:.1f}s -> {args.out}", flush=True)
    if enc.returncode:
        sys.exit(enc.returncode)


if __name__ == "__main__":
    main()
