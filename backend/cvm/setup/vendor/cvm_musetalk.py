"""MuseTalk 1.5 lipsync for content-video-maker (CUDA on Windows/NVIDIA, MPS on a Mac). Copied into models/MuseTalk
by the install wizard (cvm/setup/tools.py); from bakeoff/lipsync/MuseTalk/cvm_infer.py.

    <venv python> cvm_musetalk.py --video base.mp4 --audio narration.wav --out out.mp4 [--fp32] [--no-cache]

Differences from upstream scripts/inference.py (upstream files are NOT modified; see bakeoff/lipsync/MuseTalk/CVM_PATCHES.md):
- device cuda, else mps (PYTORCH_ENABLE_MPS_FALLBACK=1), else cpu;
- the face landmarks come from the `face-alignment` package (S3FD + 2DFAN4, the same iBUG-68 layout that
  DWPose's face keypoints 23:91 use) instead of mmpose/DWPose, which has no macOS arm64 build;
- frames stay in memory and are piped straight to ffmpeg (no PNG round trip); audio is muxed in the same pass;
- per-video face boxes + VAE latents are cached in cache/ (the avatar's base video is reused across jobs).
Output: H.264 mp4 at 25 fps, same resolution/framing as the input, duration = audio duration
(the base video is played forward/backward in a loop, as upstream does).
"""

import argparse
import hashlib
import os
import subprocess
import sys
import time

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
# torch>=2.6 defaults torch.load(weights_only=True); the official resnet18-5c106cde.pth used by the face parser
# is in the legacy .tar format, which weights_only cannot read.
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
REPO = os.path.dirname(os.path.abspath(__file__))
ORIG_CWD = os.getcwd()
os.chdir(REPO)  # upstream code uses ./models/... relative paths
sys.path.insert(0, REPO)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

FFMPEG = os.environ.get("CVM_FFMPEG", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
if not os.path.exists(FFMPEG):
    FFMPEG = "ffmpeg"
FPS = 25
CACHE_VERSION = "v1"


def log(msg):
    print(f"[musetalk {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def pick_device():
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    log("WARNING: MPS unavailable, falling back to CPU (very slow)")
    return torch.device("cpu")


def read_frames(video, max_frames=None):
    """Decode the video at 25 fps (ffmpeg resamples if needed) into a list of BGR frames."""
    cap = cv2.VideoCapture(video)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    cmd = [FFMPEG, "-v", "error", "-i", video, "-an", "-vf", f"fps={FPS}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    if max_frames:
        cmd[-5:-5] = ["-frames:v", str(max_frames)]
    proc = subprocess.run(cmd, capture_output=True, check=True)
    raw = np.frombuffer(proc.stdout, np.uint8)
    n = raw.size // (w * h * 3)
    return [f.copy() for f in raw[: n * w * h * 3].reshape(n, h, w, 3)], (w, h)


def face_boxes(frames, device):
    """MuseTalk crop box per frame, computed from 68 landmarks like upstream get_landmark_and_bbox (bbox_shift=0)."""
    import face_alignment

    torch.hub.set_dir(os.path.join(REPO, "models", "face-alignment", "hub"))  # holds 2DFAN4-cd938726ad.zip
    fa = face_alignment.FaceAlignment(
        face_alignment.LandmarksType.TWO_D, device=str(device), flip_input=False,
        face_detector_kwargs={"path_to_detector": os.path.join(REPO, "models", "face-alignment", "auxiliary", "s3fd-619a316812.pth")},
    )
    boxes = []
    for i, frame in enumerate(frames):
        h, w = frame.shape[:2]
        rgb = frame[:, :, ::-1]
        # detect on a downscaled copy (S3FD cost grows with pixels), landmarks on full resolution
        scale = min(1.0, 640 / max(h, w))
        small = cv2.resize(rgb, (int(w * scale), int(h * scale))) if scale < 1 else rgb
        dets = fa.face_detector.detect_from_image(small.copy())
        dets = [d for d in dets if d[4] > 0.5]
        if not dets:
            boxes.append(None)
            continue
        d = max(dets, key=lambda d: (d[2] - d[0]) * (d[3] - d[1]))
        det = np.array([d[0] / scale, d[1] / scale, d[2] / scale, d[3] / scale, d[4]])
        lm = fa.get_landmarks_from_image(rgb.copy(), detected_faces=[det])[0].astype(np.int32)
        half_face_y = lm[29][1]
        half_face_dist = np.max(lm[:, 1]) - half_face_y
        upper = max(0, half_face_y - half_face_dist)
        x1, y1, x2, y2 = int(np.min(lm[:, 0])), int(upper), int(np.max(lm[:, 0])), int(np.max(lm[:, 1]))
        if y2 - y1 <= 0 or x2 - x1 <= 0 or x1 < 0:
            x1, y1, x2, y2 = [int(v) for v in det[:4]]
        boxes.append([x1, y1, x2, y2])
        if i % 50 == 0:
            log(f"landmarks {i}/{len(frames)}")
    if all(b is None for b in boxes):
        raise RuntimeError("no face detected in the base video")
    # fill frames without a face with the nearest detected box, then smooth to remove landmark jitter
    last = next(b for b in boxes if b is not None)
    for i, b in enumerate(boxes):
        boxes[i] = last = (b if b is not None else last)
    arr = np.array(boxes, np.float32)
    k = 5
    pad = np.pad(arr, ((k // 2, k // 2), (0, 0)), mode="edge")
    smooth = np.stack([pad[i:i + len(arr)] for i in range(k)]).mean(0)
    return [tuple(int(round(v)) for v in b) for b in smooth]


def restore_texture(frame, orig, gen_face, box, alpha, fp):
    """Add the source frame's high frequencies (hair texture) to the generated lower face, except on the lips.
    Uses the exact source frame of this output frame, so nothing ghosts; the band above the nose is untouched."""
    from PIL import Image

    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    pil = Image.fromarray(cv2.cvtColor(gen_face, cv2.COLOR_BGR2RGB)).resize((512, 512), Image.BILINEAR)
    with torch.no_grad():
        lab = fp.net(torch.unsqueeze(fp.preprocess(pil), 0))[0].squeeze(0).cpu().numpy().argmax(0).astype(np.uint8)
    lab = cv2.resize(lab, (bw, bh), interpolation=cv2.INTER_NEAREST)
    k = max(3, bw // 40) | 1
    lips = cv2.dilate(np.isin(lab, [11, 12, 13]).astype(np.uint8), np.ones((k, k), np.uint8))
    skin = cv2.erode((lab == 1).astype(np.uint8), np.ones((k, k), np.uint8))
    m = (skin * (1 - lips)).astype(np.float32)
    m[: int(bh * 0.5)] = 0  # only below the nose, where MuseTalk replaced the face
    m = cv2.GaussianBlur(m, (k * 4 + 1, k * 4 + 1), 0)[..., None]
    o = orig[y1:y2, x1:x2].astype(np.float32)
    f = frame[y1:y2, x1:x2].astype(np.float32)
    # colour: the generated skin comes out pinker/brighter; match its mean/spread to the real frame (Lab, skin only)
    sel = m[..., 0] > 0.5
    if sel.sum() > 200:
        fl = cv2.cvtColor(f.astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
        ol = cv2.cvtColor(o.astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
        fm, fs = fl[sel].mean(0), fl[sel].std(0) + 1e-3
        om, os_ = ol[sel].mean(0), ol[sel].std(0) + 1e-3
        matched = cv2.cvtColor(np.clip((fl - fm) * (os_ / fs) + om, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR).astype(np.float32)
        f = f + m * (matched - f)
    hp = o - cv2.GaussianBlur(o, (0, 0), 2.5)
    out = frame.copy()
    out[y1:y2, x1:x2] = np.clip(f + alpha * m * hp, 0, 255).astype(np.uint8)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fp32", action="store_true", help="full precision (default fp16 on MPS)")
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--extra-margin", type=int, default=10)
    ap.add_argument("--parsing-mode", default="jaw")
    # narrower mask keeps more of the real face (beard, mustache, cheeks): cheek width in px of the 512 parsing map,
    # upper ratio = how far down the face box the replaced area starts (0.5 = from the nose)
    ap.add_argument("--cheek-width", type=int, default=90)
    ap.add_argument("--upper-ratio", type=float, default=0.5)
    # put the real frame's fine detail (beard/mustache hairs) back on the generated lower face, outside the lips;
    # MuseTalk generates at 256 px and smooths everything around the mouth. 0 = off.
    ap.add_argument("--texture", type=float, default=0.0)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    video, audio, out = (os.path.join(ORIG_CWD, p) for p in (args.video, args.audio, args.out))
    t0 = time.time()
    device = pick_device()
    dtype = torch.float32 if (args.fp32 or device.type == "cpu") else torch.float16

    from transformers import WhisperModel
    from musetalk.utils.audio_processor import AudioProcessor
    from musetalk.utils.blending import get_image
    from musetalk.utils.face_parsing import FaceParsing
    from musetalk.utils.utils import datagen, load_all_model

    vae, unet, pe = load_all_model(
        unet_model_path="models/musetalkV15/unet.pth", vae_type="sd-vae",
        unet_config="models/musetalkV15/musetalk.json", device=device,
    )
    pe = pe.to(device, dtype)
    vae.vae = vae.vae.to(device, dtype)
    unet.model = unet.model.to(device, dtype).eval()
    timesteps = torch.tensor([0], device=device)

    audio_processor = AudioProcessor(feature_extractor_path="models/whisper")
    whisper = WhisperModel.from_pretrained("models/whisper").to(device=device, dtype=dtype).eval()
    whisper.requires_grad_(False)

    fp = FaceParsing(left_cheek_width=args.cheek_width, right_cheek_width=args.cheek_width)
    net = fp.net.to(device)
    fp.net = lambda x: net(x.to(device))  # upstream only moves the input to cuda
    log(f"models loaded in {time.time() - t0:.1f}s (device={device}, dtype={dtype})")

    with torch.no_grad():
        feats, n_samples = audio_processor.get_audio_feature(audio)
        chunks = audio_processor.get_whisper_chunk(feats, device, dtype, whisper, n_samples, fps=FPS,
                                                   audio_padding_length_left=2, audio_padding_length_right=2)
    n_out = len(chunks)

    # Only the first n_out frames can be used (ping-pong loop), so don't decode more than that.
    frames, (w, h) = read_frames(video, max_frames=n_out)
    st = os.stat(video)
    key = hashlib.sha1(f"{CACHE_VERSION}|{video}|{st.st_size}|{st.st_mtime_ns}|{len(frames)}|{args.extra_margin}|{dtype}".encode()).hexdigest()[:16]
    cache_path = os.path.join(REPO, "cache", f"{key}.pt")
    if os.path.exists(cache_path) and not args.no_cache:
        cached = torch.load(cache_path)
        boxes, latents = cached["boxes"], [l.to(device) for l in cached["latents"]]
        log(f"using cached face boxes/latents {cache_path}")
    else:
        t1 = time.time()
        boxes = face_boxes(frames, device)
        boxes = [(x1, y1, x2, min(y2 + args.extra_margin, h)) for x1, y1, x2, y2 in boxes]
        log(f"face boxes for {len(frames)} frames in {time.time() - t1:.1f}s")
        latents = []
        with torch.no_grad():
            for (x1, y1, x2, y2), frame in zip(boxes, frames):
                crop = cv2.resize(frame[y1:y2, x1:x2], (256, 256), interpolation=cv2.INTER_LANCZOS4)
                latents.append(vae.get_latents_for_unet(crop))
        if not args.no_cache:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            torch.save({"boxes": boxes, "latents": [l.cpu() for l in latents]}, cache_path)
    log(f"preprocessing done at {time.time() - t0:.1f}s; generating {n_out} frames")

    frames_c = frames + frames[::-1]
    boxes_c = boxes + boxes[::-1]
    latents_c = latents + latents[::-1]

    tmp_out = out + ".tmp.mp4"
    enc = subprocess.Popen(
        [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-",
         "-i", audio, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", tmp_out],
        stdin=subprocess.PIPE,
    )
    t2 = time.time()
    t_model = 0.0
    t_blend = [0.0]

    def blend_and_write(decoded, first):
        # runs in a worker thread so CPU blending overlaps with the next UNet/VAE batch on the GPU
        ta = time.time()
        with torch.no_grad():
            for k, res in enumerate(decoded):
                j = (first + k) % len(frames_c)
                x1, y1, x2, y2 = boxes_c[j]
                res = cv2.resize(res.astype(np.uint8), (x2 - x1, y2 - y1))
                frame = get_image(frames_c[j].copy(), res, [x1, y1, x2, y2], upper_boundary_ratio=args.upper_ratio,
                                  mode=args.parsing_mode, fp=fp)
                if args.texture > 0:
                    frame = restore_texture(frame, frames_c[j], res, (x1, y1, x2, y2), args.texture, fp)
                enc.stdin.write(np.ascontiguousarray(frame).tobytes())
        t_blend[0] += time.time() - ta

    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=1)
    pending = None
    idx = 0
    with torch.no_grad():
        for whisper_batch, latent_batch in datagen(chunks, latents_c, batch_size=args.batch_size, device=device):
            ta = time.time()
            audio_feat = pe(whisper_batch.to(dtype))
            pred = unet.model(latent_batch.to(dtype), timesteps, encoder_hidden_states=audio_feat).sample
            decoded = vae.decode_latents(pred)
            t_model += time.time() - ta
            if pending is not None:
                pending.result()
            pending = pool.submit(blend_and_write, decoded, idx)
            idx += len(decoded)
            if idx % 50 < args.batch_size:
                log(f"frame {idx}/{n_out} ({idx / (time.time() - t2):.2f} fps)")
    if pending is not None:
        pending.result()
    pool.shutdown()
    t_blend = t_blend[0]
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("ffmpeg encode failed")
    os.replace(tmp_out, out)
    log(f"done: {out} ({n_out} frames, {n_out / FPS:.2f}s) in {time.time() - t0:.1f}s total, generation {time.time() - t2:.1f}s (unet+vae {t_model:.1f}s, blend+encode {t_blend:.1f}s)")


if __name__ == "__main__":
    main()
