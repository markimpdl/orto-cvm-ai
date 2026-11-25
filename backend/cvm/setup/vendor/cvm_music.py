"""CVM bridge: instrumental background tracks with ACE-Step 1.5 (2B turbo DiT + 0.6B LM), one model load per call.

Usage (from this folder's venv): python cvm_music.py jobs.json
jobs.json: [{"caption": str, "seconds": float, "seed": int, "out": "/abs/path.wav", "bpm"?: int}]
Prints one JSON line per job: {"out", "ok", "seconds_taken", "error"?}. Weights download to ./checkpoints on first use.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(k, None)

# CVM_ACE_OFFLOAD=1 (the "acestep-offload" tier, cards under 16 GB): the models wait in RAM and visit the GPU in turns
OFFLOAD = os.environ.get("CVM_ACE_OFFLOAD") == "1"

from acestep.handler import AceStepHandler  # noqa: E402
from acestep.inference import GenerationConfig, GenerationParams, generate_music  # noqa: E402
from acestep.llm_inference import LLMHandler  # noqa: E402


def main() -> None:
    jobs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    dit = AceStepHandler()
    msg, ok = dit.initialize_service(project_root=str(ROOT), config_path="acestep-v15-turbo", device="auto",
                                     offload_to_cpu=OFFLOAD)
    if not ok:
        raise SystemExit(f"DiT init failed: {msg}")
    lm = LLMHandler()
    # the 0.6B LM suits an 8 GB card; the main download ships the 1.7B one, used when the small one isn't there
    lm_name = next((n for n in ("acestep-5Hz-lm-0.6B", "acestep-5Hz-lm-1.7B") if (ROOT / "checkpoints" / n).exists()),
                   "acestep-5Hz-lm-0.6B")
    msg, ok = lm.initialize(checkpoint_dir=str(ROOT / "checkpoints"), lm_model_path=lm_name,
                            backend="mlx" if sys.platform == "darwin" else "pt", device="auto", offload_to_cpu=OFFLOAD)
    if not ok:
        raise SystemExit(f"LM init failed: {msg}")
    for job in jobs:
        t0 = time.time()
        out = Path(job["out"])
        tmp = out.parent / f".{out.stem}_acestep"
        try:
            params = GenerationParams(task_type="text2music", caption=job["caption"], lyrics="[Instrumental]",
                                      instrumental=True, duration=float(job["seconds"]), bpm=job.get("bpm"),
                                      seed=int(job.get("seed", 42)), shift=3.0, inference_steps=8)
            res = generate_music(dit, lm, params, GenerationConfig(batch_size=1, audio_format="wav"),
                                 save_dir=str(tmp))
            if not res.success or not res.audios:
                raise RuntimeError(res.error or "no audio")
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(res.audios[0]["path"], out)
            print(json.dumps({"out": str(out), "ok": True, "seconds_taken": round(time.time() - t0, 1)}), flush=True)
        except Exception as e:  # noqa: BLE001 — one bad job must not sink the batch
            print(json.dumps({"out": str(out), "ok": False, "error": str(e)[:300]}), flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
