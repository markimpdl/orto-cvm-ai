"""Fine-tune VoxCPM2 (LoRA) on the avatar owner's voice — on a CUDA PC (≥ 12 GB VRAM) or on the Mac (MPS).

    cd backend
    # CUDA PC:  uv sync --group voicetrain   (the Mac makes its own training env in models/VoxCPMTrain)
    uv run python -m cvm.voice.finetune --dataset http://<mac>:8790/avatars/1/studio-export.zip --register
    # Mac, with a LoRA trained on the PC:  uv run python -m cvm.voice.finetune --from-lora <step dir> --register

Steps (each one is skipped when its output already exists, so a stopped run can just be started again):
1. dataset: the studio export (cvm/voice/studio.py) — a URL or a local .zip/folder — into data/voice_ft/<name>/dataset
2. VoxCPM's own training code: its GitHub repo at the installed package's version, cloned once into tools/VoxCPM
   (Mac: plus a separate venv models/VoxCPMTrain with PyTorch, so the app's packages are never touched)
3. LoRA config (VoxCPM 2 docs: sample_rate 16000, lora on LM + DiT, lr 1e-4, val manifest) and training with
   scripts/train_voxcpm_finetune.py, checkpoints every SAVE_EVERY steps (Mac: under the heavy-model lock, so no
   video step runs meanwhile — start it at night)
4. the last checkpoints read held-out sentences (never trained on); whisper scores them; the lowest error wins
5. Mac: the winner is merged into the base weights (W + alpha/r · B·A) and converted to MLX 8-bit, the format
   the Mac's voice engine runs
6. blind test in data/voice_ft/<name>/blind_test: real recording, today's clone, trained voice (and its Mac
   version) under shuffled letters; key.json says which is which
7. --register: the avatar voice "Voz real (treinada)" on THIS machine (CUDA: VoxCPM2 + LoRA; Mac: the MLX model)
"""

import argparse
import io
import json
import math
import os
import random
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from cvm import private
from cvm.config import settings

REPO_URL = "https://github.com/OpenBMB/VoxCPM"
ROOT = Path(__file__).resolve().parents[3]  # repo root
SAVE_EVERY = 100
EVAL_CHECKPOINTS = 4  # the last N checkpoints are scored (earlier ones are usually undertrained)
EVAL_SENTENCES = 12
MAC_ENV = settings.models_dir / "VoxCPMTrain"
SPEAK = Path(__file__).with_name("finetune_speak.py")


def log(msg: str) -> None:
    print(f"[voz] {msg}", flush=True)


def device() -> tuple[str, float]:
    """('cuda' | 'mps', memory in GB usable for training)."""
    import torch

    if torch.cuda.is_available():
        return "cuda", torch.cuda.get_device_properties(0).total_memory / 2**30
    if torch.backends.mps.is_available():
        import psutil

        return "mps", psutil.virtual_memory().total / 2**30 * 0.6  # unified memory, leave room for the system
    sys.exit("sem GPU: o treino precisa de uma placa NVIDIA (CUDA) ou de um Mac com Apple Silicon.")


def train_python(dev: str, repo: Path) -> str:
    """CUDA: the backend venv already has voxcpm + torch. Mac: a separate venv (the app's MLX pins stay as they are)."""
    if dev != "mps":
        return sys.executable
    py = MAC_ENV / ".venv" / "bin" / "python"
    if not py.exists():
        log("preparando o ambiente de treino do Mac (models/VoxCPMTrain, uma vez só)")
        MAC_ENV.mkdir(parents=True, exist_ok=True)
        subprocess.run(["uv", "venv", "--python", "3.12", str(MAC_ENV / ".venv")], check=True)
    # also when a previous setup stopped half-way: the venv exists but its packages don't
    ok = subprocess.run([str(py), "-c", "import voxcpm, argbind, tensorboardX, librosa, soundfile"],
                        capture_output=True).returncode == 0
    if not ok:
        subprocess.run(["uv", "pip", "install", "--python", str(py), "-e", str(repo), "tensorboardX", "argbind",
                        "librosa", "soundfile"], check=True)
    return str(py)


def get_dataset(src: str, dest: Path) -> Path:
    if (dest / "train.jsonl").exists():
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    if src.startswith("http"):
        log(f"baixando gravações de {src}")
        req = urllib.request.Request(src, headers={"User-Agent": "cvm-voice-finetune/1.0"})
        with urllib.request.urlopen(req, timeout=600) as r:
            zipfile.ZipFile(io.BytesIO(r.read())).extractall(dest)
    elif src.endswith(".zip"):
        zipfile.ZipFile(src).extractall(dest)
    else:
        shutil.copytree(src, dest, dirs_exist_ok=True)
    if not (dest / "train.jsonl").exists():
        sys.exit(f"{src} não tem train.jsonl (use a exportação do Estúdio de voz).")
    return dest


def get_repo() -> Path:
    """VoxCPM's repo at the installed package's version when there is one (its training script must match)."""
    repo = ROOT / "tools" / "VoxCPM"
    if (repo / "scripts" / "train_voxcpm_finetune.py").exists():
        return repo
    try:
        from importlib.metadata import version

        tags = [version("voxcpm"), f"v{version('voxcpm')}", None]
    except Exception:  # noqa: BLE001 — the Mac has no voxcpm in the app venv
        tags = ["2.0.3", "v2.0.3", None]
    repo.parent.mkdir(parents=True, exist_ok=True)
    for tag in tags:
        cmd = ["git", "clone", "--depth", "1", *(["--branch", tag] if tag else []), REPO_URL, str(repo)]
        if subprocess.run(cmd).returncode == 0:
            log(f"VoxCPM {tag or 'main'} em {repo}")
            return repo
        shutil.rmtree(repo, ignore_errors=True)
    sys.exit("não consegui clonar o repositório do VoxCPM")


def absolute_manifest(data: Path, name: str) -> Path:
    """The training script runs from tools/VoxCPM: give it the manifest with absolute audio paths."""
    out = data / name.replace(".jsonl", ".abs.jsonl")
    if not (data / name).exists():
        return data / name
    lines = []
    for r in rows(data / name):
        for k in ("audio", "ref_audio"):
            if r.get(k) and not Path(r[k]).is_absolute():
                r[k] = str((data / r[k]).resolve())
        lines.append(json.dumps(r, ensure_ascii=False))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def write_config(work: Path, data: Path, pretrained: str, steps: int, rank: int, mem: float, dev: str = "cuda") -> Path:
    batch, accum = (2, 8) if dev == "mps" else (16, 1) if mem >= 22 else (4, 4) if mem >= 15 else (2, 8)
    cfg = {
        "pretrained_path": pretrained,
        "train_manifest": str(absolute_manifest(data, "train.jsonl")),
        "val_manifest": str(absolute_manifest(data, "val.jsonl")),
        "sample_rate": 16000,
        "out_sample_rate": 48000,
        "batch_size": batch,
        "grad_accum_steps": accum,
        "num_workers": 0 if sys.platform in ("win32", "darwin") else 2,  # spawn-based workers: keep it simple
        "preprocessing_num_workers": 4,
        "num_iters": steps,
        "max_steps": steps,
        "log_interval": 10,
        "valid_interval": SAVE_EVERY,
        "save_interval": SAVE_EVERY,
        "learning_rate": 1.0e-4,
        "weight_decay": 0.01,
        "warmup_steps": max(10, steps // 10),
        "max_batch_tokens": 8192,
        "save_path": str(work / "checkpoints"),
        "tensorboard": str(work / "logs"),
        "lambdas": {"loss/diff": 1.0, "loss/stop": 1.0},
        "lora": {"enable_lm": True, "enable_dit": True, "enable_proj": False, "r": rank, "alpha": rank, "dropout": 0.0},
    }
    p = work / "lora.yaml"
    p.write_text(to_yaml(cfg), encoding="utf-8")
    log(f"config: batch {batch} × acumulação {accum}, {steps} passos, LoRA r={rank} ({dev}, {mem:.0f} GB)")
    return p


def to_yaml(d: dict, indent: int = 0) -> str:
    out = []
    for k, v in d.items():
        pad = "  " * indent
        if isinstance(v, dict):
            out.append(f"{pad}{k}:")
            out.append(to_yaml(v, indent + 1))
        elif isinstance(v, bool):
            out.append(f"{pad}{k}: {'true' if v else 'false'}")
        elif isinstance(v, str):
            out.append(f"{pad}{k}: {json.dumps(v)}")
        else:
            out.append(f"{pad}{k}: {v}")
    return "\n".join(out)


def heavy(dev: str):
    """On the Mac training shares the GPU with the video pipeline: hold its heavy-model lock."""
    import contextlib

    if dev != "mps":
        return contextlib.nullcontext()
    from cvm import gpu

    log("esperando a GPU ficar livre (os vídeos em produção terminam o passo atual)")
    return gpu.section()


def run(py: str, args: list[str], cwd: Path | None = None) -> None:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTORCH_ENABLE_MPS_FALLBACK": "1"}
    r = subprocess.run([py, *args], cwd=cwd, env=env)
    if r.returncode != 0:
        sys.exit(f"falhou ({r.returncode}): {' '.join(args[:2])}")


def checkpoints(work: Path) -> list[Path]:
    return sorted(p for p in (work / "checkpoints").glob("step_*") if p.is_dir())


def rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def held_out(data: Path) -> list[dict]:
    return rows(data / "heldout.jsonl")[:EVAL_SENTENCES]


def best_reference(data: Path, language: str = "pt-BR") -> dict:
    """The longest clean training clip of a language: today's clone and the trained voice both use it."""
    rs = [r for r in rows(data / "train.jsonl") if f"/{language}/" in r["audio"]] or rows(data / "train.jsonl")
    return max((r for r in rs if r["duration"] <= 15), key=lambda r: r["duration"], default=rs[0])


def lang_of(row: dict) -> str:
    return "en-US" if "/en-US/" in row["audio"] else "pt-BR"


def generate(py: str, pretrained: str, work: Path, data: Path, ref: dict, cks: list[Path | None]) -> None:
    groups = [{"lora": str(ck) if ck else None,
               "items": [{"text": h["text"], "out": str(work / "eval" / (ck.name if ck else "base") / f"{i:02d}.wav"),
                          "ref_audio": str(data / ref["audio"]), "ref_text": ref["text"]} for i, h in enumerate(held_out(data))]}
              for ck in cks]
    spec = work / "eval" / "jobs.json"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(json.dumps({"pretrained": pretrained, "groups": groups}, ensure_ascii=False), encoding="utf-8")
    run(py, [str(SPEAK), str(spec)])


def score(work: Path, data: Path, name: str) -> float:
    from cvm.voice.qa import heard, word_error_rate

    wers = [word_error_rate(h["text"], heard(work / "eval" / name / f"{i:02d}.wav", lang_of(h)), lang_of(h))
            for i, h in enumerate(held_out(data))]
    return round(sum(wers) / max(1, len(wers)), 3)


def evaluate(py: str, pretrained: str, work: Path, data: Path, ref: dict, dev: str) -> tuple[Path | None, list[dict]]:
    cks = checkpoints(work)[-EVAL_CHECKPOINTS:]
    with heavy(dev):
        generate(py, pretrained, work, data, ref, [None, *cks])
    scores = [{"checkpoint": n, "wer": score(work, data, n)} for n in ["base", *(c.name for c in cks)]]
    for s in scores:
        log(f"{s['checkpoint']}: erro de palavras {s['wer']:.1%}")
    trained = [s for s in scores if s["checkpoint"] != "base"]
    if not trained:
        return None, scores
    win = min(trained, key=lambda s: (s["wer"], -int(s["checkpoint"].split("_")[-1])))
    return work / "checkpoints" / win["checkpoint"], scores


# ---------- Mac: merge the LoRA into the weights, convert to MLX ----------

def merge_lora(ckpt: Path, pretrained: str, out: Path, strength: float = 1.0) -> Path:
    """Full VoxCPM2 checkpoint with the LoRA folded in: W += strength · (alpha / r) · B @ A for every wrapped Linear
    (VoxCPM's LoRALinear keeps the Linear's own `weight` key next to `lora_A` / `lora_B`). strength < 1 keeps more
    of the base model's fluency, less of the owner's own voice."""
    import torch
    from safetensors.torch import load_file, save_file

    if (out / "model.safetensors").exists():
        return out
    cfg = json.loads((ckpt / "lora_config.json").read_text())
    lc = cfg.get("lora_config", cfg)
    scale = strength * float(lc.get("alpha", lc.get("r", 1))) / float(lc.get("r", 1))
    lora = load_file(str(ckpt / "lora_weights.safetensors"))
    base = load_file(str(Path(pretrained) / "model.safetensors"))
    merged, missing = 0, []
    for k in [k for k in lora if k.endswith(".lora_A")]:
        mod = k[: -len(".lora_A")]
        target = f"{mod.replace('._orig_mod.', '.')}.weight"  # same names as model.safetensors (state_dict keys)
        if target not in base:
            missing.append(mod)
            continue
        w = base[target]
        delta = (lora[f"{mod}.lora_B"].float() @ lora[k].float()) * scale
        base[target] = (w.float() + delta).to(w.dtype)
        merged += 1
    if missing:
        sys.exit(f"LoRA sem camada correspondente no modelo ({len(missing)}): {missing[:3]}")
    out.mkdir(parents=True, exist_ok=True)
    for f in Path(pretrained).iterdir():
        if f.is_file() and f.name != "model.safetensors":
            shutil.copy2(f, out / f.name)
    save_file(base, str(out / "model.safetensors"), metadata={"format": "pt"})
    log(f"LoRA incorporado em {merged} camadas (escala {scale:g})")
    return out


def to_mlx(merged: Path, out: Path) -> Path:
    from mlx_audio.convert import convert

    if (out / "config.json").exists():
        return out
    log("convertendo para o formato do Mac (MLX, 8 bits)")
    convert(hf_path=str(merged), mlx_path=str(out), quantize=True, q_bits=8)
    # the original repo's tokenizer is custom code (trust_remote_code); the Mac's working model ships a plain
    # LlamaTokenizer with the same vocabulary: use its tokenizer files
    from huggingface_hub import snapshot_download

    from cvm.voice.engine import VOXCPM2_MODEL

    ok = Path(snapshot_download(VOXCPM2_MODEL, local_files_only=True))
    (out / "tokenization_voxcpm2.py").unlink(missing_ok=True)
    for f in ("tokenizer_config.json", "tokenizer.json", "special_tokens_map.json"):
        if (ok / f).exists():
            shutil.copy2(ok / f, out / f)
    return out


def mac_samples(work: Path, data: Path, ref: dict, mlx_model: Path) -> None:
    """The held-out sentences with the converted model, through the same engine the videos use."""
    from cvm.models import VoiceProfile
    from cvm.voice.engine import _voxcpm

    d = work / "eval" / "mac"
    d.mkdir(parents=True, exist_ok=True)
    profile = VoiceProfile(avatar_id=0, language="pt-BR", ref_audio_path=str(data / ref["audio"]), ref_text=ref["text"],
                           params={"engine": "voxcpm2", "mlx_model": str(mlx_model)})
    with heavy("mps"):
        for i, h in enumerate(held_out(data)):
            if not (d / f"{i:02d}.wav").exists():
                _voxcpm(h["text"], lang_of(h), profile, d / f"{i:02d}.wav", seed=1234)


def blind_test(work: Path, data: Path, best: Path) -> Path:
    out = work / "blind_test"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rng = random.Random(42)
    key = []
    for i, h in enumerate(held_out(data)):
        options = {"real": data / h["audio"], "clone_hoje": work / "eval" / "base" / f"{i:02d}.wav",
                   "treinada": work / "eval" / best.name / f"{i:02d}.wav"}
        if (work / "eval" / "mac" / f"{i:02d}.wav").exists():
            options["treinada_mac"] = work / "eval" / "mac" / f"{i:02d}.wav"
        names = list(options)
        rng.shuffle(names)
        entry = {"frase": h["text"]}
        for letter, name in zip("ABCD", names):
            shutil.copy2(options[name], out / f"{i + 1:02d}_{letter}.wav")
            entry[letter] = name
        key.append(entry)
    (out / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "LEIA.txt").write_text(
        "Para cada frase há 3 ou 4 áudios (A, B, C, D): sua gravação real, a clonagem de hoje e a voz treinada\n"
        "(e a versão dela para o Mac), em ordem sorteada. Ouça sem abrir key.json, anote qual soa mais natural e\n"
        "mais parecida com você; depois confira.\n", encoding="utf-8")
    return out


def register(best: Path, data: Path, ref: dict, avatar_id: int, name: str, mlx_model: Path | None) -> None:
    from sqlmodel import Session, select

    from cvm.db import engine
    from cvm.models import VoiceProfile

    dest = settings.data_dir / "avatars" / str(avatar_id) / "voice" / "lora" / name
    shutil.copytree(best, dest, dirs_exist_ok=True)
    ref_wav = dest / "reference.wav"
    shutil.copy2(data / ref["audio"], ref_wav)
    params = {"engine": "voxcpm2", "variant": "real", "label": "Voz real (treinada)"}
    if mlx_model:
        params["mlx_model"] = str(mlx_model)
    else:
        params["lora"] = str(dest)
    # one profile per recorded language (pt-BR always; en-US when the English block was recorded), each with a
    # reference clip in its own language
    refs = {"pt-BR": (ref_wav, ref["text"])}
    if any("/en-US/" in r["audio"] for r in rows(data / "train.jsonl")):
        en = best_reference(data, "en-US")
        shutil.copy2(data / en["audio"], dest / "reference_en-US.wav")
        refs["en-US"] = (dest / "reference_en-US.wav", en["text"])
    with Session(engine) as s:
        for lang, (wav, text) in refs.items():
            profiles = s.exec(select(VoiceProfile).where(VoiceProfile.avatar_id == avatar_id,
                                                         VoiceProfile.language == lang)).all()
            p = next((x for x in profiles if (x.params or {}).get("variant") == "real"), None) or \
                VoiceProfile(avatar_id=avatar_id, language=lang)
            p.ref_audio_path, p.ref_text, p.params = str(wav), text, params
            s.add(p)
        s.commit()
    log(f"voz 'Voz real (treinada)' registrada para o avatar {avatar_id} em {', '.join(refs)}")


def default_name() -> str:
    """Work folder name (data/voice_ft/<name>): private/voice/finetune.json {"name"} keeps the owner's earlier runs."""
    return (private.json("voice/finetune.json", {}) or {}).get("name") or "voz-real"


def main() -> None:
    ap = argparse.ArgumentParser(description="Treina a voz do avatar (VoxCPM2 + LoRA) com as gravações do Estúdio de voz.")
    ap.add_argument("--dataset", help="URL da exportação (http://<mac>:8790/avatars/1/studio-export.zip), .zip ou pasta")
    ap.add_argument("--from-lora", help="usa um checkpoint treinado em outra máquina (pasta step_XXXXXXX ou a voz registrada lá)")
    ap.add_argument("--avatar", type=int, default=1)
    ap.add_argument("--name", default=default_name())
    ap.add_argument("--epochs", type=float, default=0, help="0 = automático (~6 épocas, de 300 a 1200 passos)")
    ap.add_argument("--max-steps", type=int, default=0, help="teste rápido: limita os passos (ex.: 20)")
    ap.add_argument("--rank", type=int, default=32)
    ap.add_argument("--register", action="store_true", help="registra a melhor versão como voz do avatar nesta máquina")
    ap.add_argument("--skip-train", action="store_true", help="só avalia os checkpoints que já existem")
    a = ap.parse_args()
    if not (a.dataset or a.from_lora):
        ap.error("informe --dataset (treinar) ou --from-lora (trazer um treino feito em outra máquina)")

    from huggingface_hub import snapshot_download

    dev, mem = device()
    pretrained = snapshot_download("openbmb/VoxCPM2", local_files_only=True)
    work = settings.data_dir / "voice_ft" / a.name

    if a.from_lora:  # a LoRA trained on another machine: Mac → merge + MLX; CUDA PC → VoxCPM2 + this LoRA as is
        ck = Path(a.from_lora)
        if not (ck / "lora_config.json").exists():
            sys.exit(f"{ck} não é um checkpoint de treino (falta lora_config.json)")
        mlx_model = to_mlx(merge_lora(ck, pretrained, work / "merged"), work / "mlx") if dev == "mps" else None
        if a.register:
            data = get_dataset(a.dataset, work / "dataset") if a.dataset else None
            ref = best_reference(data) if data else None
            if not ref:
                sys.exit("para registrar, informe também --dataset (a referência de voz sai das gravações)")
            register(ck, data, ref, a.avatar, a.name, mlx_model)
        return

    if dev == "cuda" and mem < 11.5:  # training only: registering a LoRA trained elsewhere just runs inference
        sys.exit(f"esta placa tem {mem:.0f} GB; o treino precisa de pelo menos 12 GB (ideal 20+).")
    data = get_dataset(a.dataset, work / "dataset")
    info = json.loads((data / "info.json").read_text(encoding="utf-8")) if (data / "info.json").exists() else {}
    log(f"{info.get('train', '?')} frases de treino, {info.get('minutes', '?')} min no total")
    repo = get_repo()
    py = train_python(dev, repo)
    if not a.skip_train and not checkpoints(work):
        n = len(rows(data / "train.jsonl"))
        steps = int(min(1200, max(300, math.ceil(n / 16) * (a.epochs or 6))))
        steps = a.max_steps or math.ceil(steps / SAVE_EVERY) * SAVE_EVERY
        cfg = write_config(work, data, pretrained, steps, a.rank, mem, dev)
        log("treinando (PC: 20 min a 1 h; Mac: algumas horas; Ctrl+C salva um checkpoint e para)")
        with heavy(dev):
            run(py, [str(repo / "scripts" / "train_voxcpm_finetune.py"), "--config_path", str(cfg)], cwd=repo)
    ref = best_reference(data)
    best, scores = evaluate(py, pretrained, work, data, ref, dev)
    (work / "scores.json").write_text(json.dumps(scores, indent=1), encoding="utf-8")
    if not best:
        sys.exit("nenhum checkpoint encontrado")
    mlx_model = None
    if dev == "mps":
        mlx_model = to_mlx(merge_lora(best, pretrained, work / "merged"), work / "mlx")
        mac_samples(work, data, ref, mlx_model)
    out = blind_test(work, data, best)
    log(f"melhor: {best.name}. Teste cego em {out}")
    if a.register:
        register(best, data, ref, a.avatar, a.name, mlx_model)


if __name__ == "__main__":
    main()
