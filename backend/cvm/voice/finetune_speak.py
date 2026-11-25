"""Generate test sentences with VoxCPM2 (+ a LoRA checkpoint) — standalone, no cvm imports, so it runs in the
training environment (models/VoxCPMTrain on the Mac, the backend venv on a CUDA PC).

    python finetune_speak.py jobs.json
jobs.json: {"pretrained": dir, "groups": [{"lora": dir | null, "items": [{"text", "out", "ref_audio", "ref_text"}]}]}
One model load per group (= per checkpoint); existing outputs are skipped.
"""

import json
import sys
from pathlib import Path


def lora_config_of(lora_dir: str):
    """The LoRAConfig a checkpoint was trained with (its lora_config.json): without it VoxCPM builds the default
    r=8 layers and a r=32 training fails to load ("size of tensor a (8) must match … (32)")."""
    import json
    from pathlib import Path as _P

    f = _P(lora_dir) / "lora_config.json"
    if not f.exists():
        return None
    try:
        from voxcpm.model.voxcpm2 import LoRAConfig
    except ImportError:
        from voxcpm.model.voxcpm import LoRAConfig
    return LoRAConfig(**json.loads(f.read_text(encoding="utf-8"))["lora_config"])


def main(spec_path: str) -> None:
    import soundfile as sf
    import torch
    from voxcpm import VoxCPM

    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    for g in spec["groups"]:
        todo = [it for it in g["items"] if not Path(it["out"]).exists()]
        if not todo:
            continue
        kw = {"lora_weights_path": g["lora"], "lora_config": lora_config_of(g["lora"])} if g.get("lora") else {}
        model = VoxCPM.from_pretrained(spec["pretrained"], load_denoiser=False, local_files_only=True, optimize=False,
                                       device=device, **kw)
        for it in todo:
            torch.manual_seed(1234)
            wav = model.generate(text=it["text"], reference_wav_path=it["ref_audio"], prompt_wav_path=it["ref_audio"],
                                 prompt_text=it["ref_text"])
            Path(it["out"]).parent.mkdir(parents=True, exist_ok=True)
            sf.write(it["out"], wav, model.tts_model.sample_rate)
            print(json.dumps({"done": it["out"]}), flush=True)
        del model
        if device == "cuda":
            torch.cuda.empty_cache()
        elif device == "mps":
            torch.mps.empty_cache()


if __name__ == "__main__":
    main(sys.argv[1])
