"""LatentSync lipsync for content-video-maker (CUDA on Windows/NVIDIA, MPS on a Mac). Copied into models/LatentSync
by the install wizard (cvm/setup/tools.py); from bakeoff/lipsync/LatentSync/cvm_infer.py. On CUDA the patches below
are harmless (the attention chunking is numerically identical) and keep one script for both.

    <venv python> cvm_latentsync.py --video base.mp4 --audio narration.wav --out out.mp4 [--steps 20] [--fp32]
    CVM_LATENTSYNC_CONFIG=configs/unet/stage2.yaml → LatentSync 1.5 weights (256 px, 8 GB)

Mirrors upstream scripts/inference.py (stage2_512 config, DeepCache on, guidance 1.5, like inference.sh) but
monkeypatches the CUDA-only bits at runtime instead of editing upstream files (see CVM_PATCHES.md):
- `decord` (no macOS arm64 wheel) is stubbed; video is read with OpenCV (upstream's use_decord=False path) and
  audio with librosa;
- the pipeline's hard-coded `ImageProcessor(device="cuda")` is redirected to MPS;
- insightface (face detection + 106 landmarks) runs on the onnxruntime CPU provider instead of CUDA;
- pipeline/whisper device "cuda" -> "mps".
Output: H.264 mp4 at 25 fps, same resolution/framing as the input, audio muxed (upstream behaviour).
"""

import argparse
import os
import sys
import time
import types

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
REPO = os.path.dirname(os.path.abspath(__file__))
ORIG_CWD = os.getcwd()
os.chdir(REPO)  # upstream uses configs/, checkpoints/, latentsync/utils/mask.png relative paths
sys.path.insert(0, REPO)
FFMPEG_DIR = os.environ.get("CVM_FFMPEG_DIR", "/opt/homebrew/opt/ffmpeg-full/bin")
if os.path.isdir(FFMPEG_DIR):
    os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")

# --- decord stub: imported at module level by latentsync.utils.util / av_reader, never used on our path ---
_decord = types.ModuleType("decord")
_decord.VideoReader = _decord.AudioReader = None
sys.modules["decord"] = _decord

import numpy as np  # noqa: E402
import torch  # noqa: E402


def log(msg):
    print(f"[latentsync {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def pick_device():
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    log("WARNING: MPS unavailable, falling back to CPU (extremely slow)")
    return "cpu"


def patch_upstream(device: str):
    import librosa
    import latentsync.pipelines.lipsync_pipeline as lp
    import latentsync.utils.face_detector as fd
    import latentsync.utils.image_processor as ip
    import latentsync.utils.util as util

    def read_audio(audio_path, audio_sample_rate=16000):
        samples, _ = librosa.load(audio_path, sr=audio_sample_rate, mono=True)
        return torch.from_numpy(samples)

    util.read_audio = lp.read_audio = read_audio

    def face_detector_init(self, device="cuda"):
        from insightface.app import FaceAnalysis

        self.app = FaceAnalysis(
            allowed_modules=["detection", "landmark_2d_106"],
            root="checkpoints/auxiliary",
            providers=["CPUExecutionProvider"],  # CoreML EP fails on det_10g (static shape mismatch)
        )
        self.app.prepare(ctx_id=0, det_size=(fd.INSIGHTFACE_DETECT_SIZE, fd.INSIGHTFACE_DETECT_SIZE))

    fd.FaceDetector.__init__ = face_detector_init

    orig_ip = ip.ImageProcessor

    class MPSImageProcessor(orig_ip):
        def __init__(self, resolution=512, device_="cpu", mask_image=None, **kw):
            # upstream passes device="cuda" literally; "cpu" would disable face detection, so use our device
            super().__init__(resolution, device=device, mask_image=mask_image)

    def make_ip(resolution=512, device="cpu", mask_image=None):
        return MPSImageProcessor(resolution, mask_image=mask_image)

    lp.ImageProcessor = make_ip

    # MPS has no memory-efficient SDPA kernel: spatial self-attention at 64x64 latents (4096 tokens) over
    # 16 frames x 2 (CFG) materialises ~8 GB per call and pushed the process to 55 GB + swap (12-200 s/step).
    # Chunk the query rows so each attention matrix stays under ~512 MB; results are identical.
    import latentsync.models.attention as attn_mod
    import latentsync.models.motion_module as mm_mod
    import torch.nn.functional as F

    limit = int(os.environ.get("CVM_ATTN_CHUNK_BYTES", 512 * 1024 * 1024))

    def chunked_sdpa(query, key, value, attn_mask=None, **kw):
        L, S = query.shape[-2], key.shape[-2]
        batch = query.numel() // (L * query.shape[-1])
        rows = max(1, limit // max(1, batch * S * query.element_size()))
        if rows >= L:
            return F.scaled_dot_product_attention(query, key, value, attn_mask=attn_mask, **kw)
        outs = []
        for i in range(0, L, rows):
            m = attn_mask
            if m is not None and m.dim() >= 2 and m.shape[-2] == L:
                m = m[..., i:i + rows, :]
            outs.append(F.scaled_dot_product_attention(query[..., i:i + rows, :], key, value, attn_mask=m, **kw))
        return torch.cat(outs, dim=-2)

    class _F:
        def __getattr__(self, name):
            return chunked_sdpa if name == "scaled_dot_product_attention" else getattr(F, name)

    attn_mod.F = mm_mod.F = _F()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    # defaults = upstream inference.sh; env overrides let the backend tune speed without code changes
    # (measured on M5 Pro: 20 steps + CFG 1.5 ~ RTF 60; 10 steps + guidance 1.0 ~ RTF 25, visually very close)
    ap.add_argument("--steps", type=int, default=int(os.environ.get("CVM_LATENTSYNC_STEPS", 20)))
    ap.add_argument("--guidance", type=float, default=float(os.environ.get("CVM_LATENTSYNC_GUIDANCE", 1.5)))
    ap.add_argument("--seed", type=int, default=1247)
    ap.add_argument("--fp32", action="store_true", help="full precision (default fp16 on MPS)")
    ap.add_argument("--no-deepcache", action="store_true")
    args = ap.parse_args()
    video, audio, out = (os.path.join(ORIG_CWD, p) for p in (args.video, args.audio, args.out))
    t0 = time.time()
    device = pick_device()
    dtype = torch.float32 if (args.fp32 or device == "cpu") else torch.float16
    patch_upstream(device)

    from accelerate.utils import set_seed
    from diffusers import AutoencoderKL, DDIMScheduler
    from omegaconf import OmegaConf
    from latentsync.models.unet import UNet3DConditionModel
    from latentsync.pipelines.lipsync_pipeline import LipsyncPipeline
    from latentsync.whisper.audio2feature import Audio2Feature

    # stage2.yaml (256 px) = LatentSync 1.5, 8 GB cards (the Windows tier); stage2_512.yaml = 1.6, 18 GB
    config = OmegaConf.load(os.environ.get("CVM_LATENTSYNC_CONFIG", "configs/unet/stage2_512.yaml"))
    scheduler = DDIMScheduler.from_pretrained("configs")
    audio_encoder = Audio2Feature(model_path="checkpoints/whisper/tiny.pt", device=device,
                                  num_frames=config.data.num_frames, audio_feat_length=config.data.audio_feat_length)
    vae = AutoencoderKL.from_pretrained("stabilityai/sd-vae-ft-mse", torch_dtype=dtype)
    vae.config.scaling_factor = 0.18215
    vae.config.shift_factor = 0
    unet, _ = UNet3DConditionModel.from_pretrained(OmegaConf.to_container(config.model), "checkpoints/latentsync_unet.pt", device="cpu")
    unet = unet.to(dtype=dtype)
    pipeline = LipsyncPipeline(vae=vae, audio_encoder=audio_encoder, unet=unet, scheduler=scheduler).to(device)
    pipeline.enable_vae_slicing()  # 1 frame at a time through the VAE: peak memory 41 GB -> 14 GB, and faster on MPS
    if not args.no_deepcache:
        from DeepCache import DeepCacheSDHelper

        helper = DeepCacheSDHelper(pipe=pipeline)
        helper.set_params(cache_interval=3, cache_branch_id=0)
        helper.enable()
    set_seed(args.seed)
    log(f"models loaded in {time.time() - t0:.1f}s (device={device}, dtype={dtype})")

    # Release MPS cached blocks after each 16-frame chunk: without this the allocator kept growing
    # between chunks (43 GB + swap on a 10 s clip, chunk time 28 s -> 110 s).
    orig_decode = pipeline.decode_latents

    def decode_latents(latents):
        out = orig_decode(latents)
        if device == "mps":
            torch.mps.synchronize()
            torch.mps.empty_cache()
            log(f"chunk decoded; mps allocated {torch.mps.current_allocated_memory() / 2**30:.1f} GB, "
                f"driver {torch.mps.driver_allocated_memory() / 2**30:.1f} GB")
        return out

    pipeline.decode_latents = decode_latents

    tmp_dir = os.path.join(REPO, "temp_cvm", str(os.getpid()))
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_out = out + ".tmp.mp4"
    t1 = time.time()
    pipeline(
        video_path=video, audio_path=audio, video_out_path=tmp_out,
        num_frames=config.data.num_frames, num_inference_steps=args.steps, guidance_scale=args.guidance,
        weight_dtype=dtype, width=config.data.resolution, height=config.data.resolution,
        mask_image_path=config.data.mask_image_path, temp_dir=tmp_dir,
    )
    if not os.path.exists(tmp_out):
        raise RuntimeError("LatentSync produced no output")
    os.replace(tmp_out, out)
    import shutil

    shutil.rmtree(tmp_dir, ignore_errors=True)
    log(f"done: {out} in {time.time() - t0:.1f}s total, pipeline {time.time() - t1:.1f}s")


if __name__ == "__main__":
    main()
