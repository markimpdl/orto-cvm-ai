"""Configurações > Modelos: every model the pipeline uses, what it does, and the Mac / Windows (NVIDIA) version of each.

Only engines the code actually runs on a platform are listed for it (models_registry.CUDA, the cuda_loader paths in
voice/engine.py, render/captions.py and visuals/sources.py). A job the Windows code has no engine for says so
("windows": none) instead of naming a model that would not run. Nothing here downloads anything.
"""

import shutil
import sys
import time
from pathlib import Path

from cvm.config import settings
from cvm.models_registry import CUDA, HF_CACHE, MODELS, MLX

PLATFORM = "mac" if sys.platform == "darwin" else "windows"


def _hf(repo: str) -> Path:
    return HF_CACHE / ("models--" + repo.replace("/", "--"))


def _downloaded(repo: str) -> bool:
    return (_hf(repo) / "snapshots").exists()


def _folder(name: str) -> Path:
    return settings.models_dir / name


# ---------- readiness: the same checks the pipeline makes, imported lazily ----------

def _reg(name: str):
    def ready() -> bool:
        from cvm.models_registry import is_ready

        return is_ready(name)
    return ready


def _ltx(version: str):
    def ready() -> bool:
        from cvm.visuals import sources

        return bool(sources._ltx_pack(version)) and (_folder("LTX2MLX") / ".venv" / "bin" / "ltx-2-mlx").exists()
    return ready


def _call(module: str, fn: str):
    def ready() -> bool:
        import importlib

        return bool(getattr(importlib.import_module(module), fn)())
    return ready


def _wan() -> bool:
    from cvm.engines import video_ready

    return video_ready("wan")


# ---------- the models ----------
# platform: "mac" (MLX / Apple Silicon only), "windows" (PyTorch + NVIDIA CUDA), "both" (same weights on both).
# repos/folders: what it takes on disk. ready: the pipeline's own check (only asked on a compatible machine).

M = {
    "voxcpm2-mac": {"name": "VoxCPM2 8-bit (MLX)", "platform": "mac", "size": MODELS["voxcpm2"]["size"],
                    "repos": [MODELS["voxcpm2"]["repo"]], "ready": _reg("voxcpm2")},
    "voxcpm2-win": {"name": "VoxCPM2 (PyTorch)", "platform": "windows", "size": CUDA["voxcpm2"]["size"],
                    "repos": [CUDA["voxcpm2"]["repo"]], "ready": _reg("voxcpm2"),
                    "note": "No Mac, estes arquivos servem só para treinar a voz real."},
    "whisper-mac": {"name": "Whisper large-v3-turbo (MLX)", "platform": "mac", "size": MODELS["whisper"]["size"],
                    "repos": [MODELS["whisper"]["repo"]], "ready": _reg("whisper")},
    "whisper-win": {"name": "Whisper large-v3-turbo (PyTorch)", "platform": "windows", "size": CUDA["whisper"]["size"],
                    "repos": [CUDA["whisper"]["repo"]], "ready": _reg("whisper")},
    "clip": {"name": "CLIP ViT-B/32", "platform": "both", "size": MODELS["clip"]["size"],
             "repos": [MODELS["clip"]["repo"]], "ready": _reg("clip")},
    "z-image": {"name": "Z-Image Turbo 4-bit (mflux)", "platform": "mac", "size": MODELS["z-image-turbo"]["size"],
                "repos": [MODELS["z-image-turbo"]["repo"]], "ready": _reg("z-image-turbo")},
    "flux-mac": {"name": "FLUX.1 schnell 4-bit (mflux)", "platform": "mac", "size": MODELS["flux-schnell"]["size"],
                 "repos": [MODELS["flux-schnell"]["repo"]], "ready": _reg("flux-schnell")},
    "flux-win": {"name": "FLUX.1 schnell NF4 (diffusers)", "platform": "windows", "size": CUDA["flux-schnell"]["size"],
                 "repos": [CUDA["flux-schnell"]["repo"]], "ready": _reg("flux-schnell")},
    "klein": {"name": "FLUX.2 Klein 4B (mflux)", "platform": "mac", "size": MODELS["flux2-klein"]["size"],
              "repos": [MODELS["flux2-klein"]["repo"]], "ready": _reg("flux2-klein")},
    "ltx-2.5": {"name": "LTX-2.5 distilled q8 (ltx-2-mlx)", "platform": "mac", "size": "~75 GB",
                "repos": ["dgrauet/ltx-2.5-mlx-q8"], "folders": ["LTX2MLX"], "ready": _ltx("2.5")},
    "ltx-2.3": {"name": "LTX-2.3 distilled q8 (ltx-2-mlx)", "platform": "mac", "size": "~30 GB",
                "repos": ["dgrauet/ltx-2.3-mlx-q8"], "folders": ["LTX2MLX"], "ready": _ltx("2.3")},
    "ltx-refine": {"name": "LTX-2.5 Refine-Details IC-LoRA", "platform": "mac", "size": "~1 GB",
                   "repos": ["Lightricks/LTX-2.5-22b-IC-LoRA-Refine-Details"],
                   "ready": lambda: bool(__import__("cvm.visuals.sources", fromlist=["x"]).ltx_refine_lora())},
    "wan": {"name": "Wan 2.2 TI2V-5B 8-bit (mlx-gen)", "platform": "mac", "size": MODELS["wan-ti2v-5b"]["size"],
            "repos": [MODELS["wan-ti2v-5b"]["repo"]], "folders": ["MLXGen"], "ready": _wan},
    "gemma": {"name": "Gemma 3 12B 4-bit (mlx-vlm)", "platform": "mac", "size": "~9 GB",
              "repos": ["mlx-community/gemma-3-12b-it-4bit"], "folders": ["VLMJudge"],
              "ready": _call("cvm.visuals.judge", "ready")},
    "seedvr2": {"name": "SeedVR2 3B (mflux)", "platform": "mac", "size": "~7 GB",
                "repos": ["numz/SeedVR2_comfyUI"], "ready": _call("cvm.visuals.upscale", "ready")},
    "rvm": {"name": "Robust Video Matting", "platform": "both", "size": "~1 GB", "folders": ["RobustVideoMatting"],
            "ready": _call("cvm.avatar.matting", "matting_ready")},
    "ace-step": {"name": "ACE-Step 1.5", "platform": "mac", "size": "~11 GB", "repos": ["ACE-Step/Ace-Step1.5"],
                 "folders": ["ACE-Step-1.5"], "ready": _call("cvm.render.music_gen", "ready")},
}

# ---------- the jobs ----------
# select: the Configurações choice it maps to (engines.py) — "image" or "video:<purpose>"; None = no choice here.
# cloud: a paid service option beside the local models (ElevenLabs, paid by the user).

ROLES = [
    {"key": "voice", "label": "Voz clonada", "use": "Narra os vídeos e dá voz aos personagens com a voz do avatar.",
     "mac": ["voxcpm2-mac"], "windows": ["voxcpm2-win"], "cloud": "ElevenLabs",
     "note": "A voz é escolhida por avatar e por canal, não aqui."},
    {"key": "whisper", "label": "Legendas e conferência da narração",
     "use": "Transcreve a narração para as legendas palavra a palavra e confere se a voz disse o texto certo.",
     "mac": ["whisper-mac"], "windows": ["whisper-win"]},
    {"key": "clip", "label": "Imagem certa para a cena", "use": "Compara cada imagem ou vídeo de banco com o texto da cena.",
     "mac": ["clip"], "windows": ["clip"]},
    {"key": "image", "label": "Imagens geradas por IA", "use": "Desenha as imagens das cenas quando não há material real.",
     "mac": ["z-image", "flux-mac"], "windows": ["flux-win"], "select": "image",
     "note": "Séries infantis mantêm o visual com que começaram."},
    {"key": "klein", "label": "Personagens das séries infantis novas",
     "use": "Desenha cada cena a partir da ficha dos personagens, para eles ficarem iguais em todo episódio.",
     "mac": ["klein"], "windows": [], "fallback": "No Windows as séries usam o FLUX.1 schnell."},
    {"key": "video:kids", "label": "Animação infantil", "use": "Anima as cenas dos episódios das séries.",
     "mac": ["ltx-2.5", "ltx-2.3", "wan"], "windows": [], "select": "video:kids",
     "fallback": "No Windows ainda não há motor de vídeo: a cena fica parada, com movimento de câmera."},
    {"key": "video:realistic", "label": "Imagem → vídeo realista", "use": "Dá movimento às fotos geradas nos vídeos comuns.",
     "mac": ["ltx-2.5", "ltx-2.3", "wan"], "windows": [], "select": "video:realistic",
     "fallback": "No Windows ainda não há motor de vídeo: a cena fica parada, com movimento de câmera."},
    {"key": "video:cartoon", "label": "Imagem → vídeo em desenho", "use": "Dá movimento às ilustrações fora das séries.",
     "mac": ["ltx-2.5", "ltx-2.3", "wan"], "windows": [], "select": "video:cartoon",
     "fallback": "No Windows ainda não há motor de vídeo: a cena fica parada, com movimento de câmera."},
    {"key": "presenter", "label": "Apresentador falando",
     "use": "Faz o avatar falar com a narração (corpo inteiro, boca sincronizada) e refina os detalhes do rosto.",
     "mac": ["ltx-2.5", "ltx-2.3", "ltx-refine"], "windows": [],
     "fallback": "No Windows o apresentador falando ainda não é gerado."},
    {"key": "judge", "label": "Juiz das imagens",
     "use": "Olha cada imagem gerada e manda refazer as que têm mão, rosto ou texto errado. O LTX também usa ele para ler o texto da cena.",
     "mac": ["gemma"], "windows": [], "fallback": "No Windows essa conferência é pulada."},
    {"key": "upscale", "label": "Nitidez das imagens", "use": "Aumenta as imagens geradas para o tamanho do vídeo sem inventar detalhe.",
     "mac": ["seedvr2"], "windows": [], "fallback": "No Windows essa etapa é pulada e a imagem só é redimensionada."},
    {"key": "matting", "label": "Recorte do avatar", "use": "Tira o fundo do apresentador para ele aparecer sobre as cenas.",
     "mac": ["rvm"], "windows": ["rvm"]},
    {"key": "music", "label": "Trilha de fundo", "use": "Compõe música instrumental própria para cada estilo de vídeo.",
     "mac": ["ace-step"], "windows": [], "cloud": "ElevenLabs Music",
     "fallback": "No Windows usa as pastas de música locais ou trilhas livres do Openverse."},
]

# engine id (engines.py) for each selectable model
ENGINE_ID = {"z-image": "z-image", "flux-mac": "flux", "flux-win": "flux", "ltx-2.5": "ltx", "ltx-2.3": "ltx", "wan": "wan"}


def _nvidia() -> bool:
    return shutil.which("nvidia-smi") is not None


def compatible(key: str) -> bool:
    """Runs on this machine's hardware: MLX needs Apple Silicon, the PyTorch builds need an NVIDIA card."""
    p = M[key]["platform"]
    if p == "both":
        return MLX or _nvidia()
    if p == "mac":
        return MLX
    return not MLX and _nvidia()


def _tree_bytes(path: Path) -> int:
    total = 0
    stack = [path]
    while stack:
        try:
            with __import__("os").scandir(stack.pop()) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
        except OSError:
            pass
    return total


def _repo_bytes(folder: Path) -> int:
    """A HF repo's files, following the snapshot links wherever the blobs live (some caches keep them outside the
    repo folder), each real file counted once."""
    files = {p.resolve() for p in (folder / "snapshots").rglob("*") if p.is_file()} if (folder / "snapshots").exists() else set()
    return sum(f.stat().st_size for f in files if f.exists())


_size_cache: dict = {"at": 0.0, "value": {}}
SIZE_TTL_S = 300


def disk_bytes() -> dict[str, int]:
    """Bytes each model takes on this disk (HF blobs + its folders under models/), cached SIZE_TTL_S."""
    if time.monotonic() - _size_cache["at"] < SIZE_TTL_S and _size_cache["value"]:
        return _size_cache["value"]
    out: dict[str, int] = {}
    seen: dict[Path, int] = {}
    for key, m in M.items():
        n = 0
        for repo in m.get("repos", []):
            p = _hf(repo)
            n += seen.setdefault(p, _repo_bytes(p)) if p.exists() else 0
        for f in m.get("folders", []):
            p = _folder(f)
            n += seen.setdefault(p, _tree_bytes(p)) if p.exists() else 0
        out[key] = n
    _size_cache.update(at=time.monotonic(), value=out)
    return out


def _status(key: str) -> dict:
    m = M[key]
    ok = compatible(key)
    try:
        ready = ok and bool(m["ready"]())
    except Exception:  # noqa: BLE001  (a broken optional install reads as missing)
        ready = False
    present = any(_downloaded(r) for r in m.get("repos", [])) or any(_folder(f).exists() for f in m.get("folders", []))
    state = "installed" if ready else "incompatible" if not ok else "partial" if present else "missing"
    return {"id": key, "name": m["name"], "platform": m["platform"], "size": m["size"], "compatible": ok, "note": m.get("note"),
            "ready": ready, "state": state, "repos": m.get("repos", []), "folders": m.get("folders", [])}


def view() -> dict:
    from cvm import engines

    st = {k: _status(k) for k in M}
    sizes = disk_bytes()
    for k, s in st.items():
        s["disk_bytes"] = sizes.get(k, 0)
    cfg = engines.get()
    roles = []
    for r in ROLES:
        sel = r.get("select")
        current = None
        if sel == "image":
            current = cfg["image_engine"]
        elif sel and sel.startswith("video:"):
            current = cfg["video"][sel.split(":", 1)[1]]
        used = [k for k in r[PLATFORM] if st[k]["ready"] and (current is None or ENGINE_ID.get(k) == current)]
        roles.append({
            "key": r["key"], "label": r["label"], "use": r["use"], "note": r.get("note"), "fallback": r.get("fallback"),
            "cloud": r.get("cloud"), "select": sel, "current": current,
            "in_use": used[0] if used else None,
            "mac": [{**st[k], "engine": ENGINE_ID.get(k)} for k in r["mac"]],
            "windows": [{**st[k], "engine": ENGINE_ID.get(k)} for k in r["windows"]],
        })
    used_by = {k: [r["label"] for r in ROLES if k in r["mac"] or k in r["windows"]] for k in M}
    inventory = sorted(({**s, "used_by": used_by[k]} for k, s in st.items()),
                       key=lambda s: (["installed", "partial", "missing", "incompatible"].index(s["state"]), s["name"]))
    return {"platform": PLATFORM, "roles": roles, "inventory": inventory}
