"""Text-to-speech with the avatar's cloned voice.

Engines:
- "voxcpm2": VoxCPM2 (Apache-2.0, 48 kHz) on MLX, or PyTorch on CUDA — the only local voice (Chatterbox removed,
  user 2026-11-24): cloned from the reference recording (avatars, kids characters, narrators), plus a free-text
  style instruction ("calm, low, suspenseful") outside continuation mode; the owner's trained LoRA voice too.
- "elevenlabs": VoiceProfile.params {"engine": "elevenlabs"} (paid by the user, cvm/voice/eleven.py).
- "macos": built-in `say` voices; zero setup, used only while the avatar has no voice reference.
"""

import logging
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from cvm.media import to_wav
from cvm.models import VoiceProfile
from cvm.models_registry import cuda_loader

log = logging.getLogger(__name__)

VOXCPM2_MODEL = "mlx-community/VoxCPM2-8bit"
LANG_CODE = {"pt-BR": "pt", "ar-AE": "ar", "en-US": "en"}
MACOS_VOICE = {"pt-BR": "Luciana", "ar-AE": "Majed", "en-US": "Samantha"}


@lru_cache(maxsize=1)
def _voxcpm2(model_path: str = VOXCPM2_MODEL):
    """The MLX VoxCPM2; `model_path` = a fine-tuned voice converted for the Mac (cvm/voice/finetune.py,
    VoiceProfile.params["mlx_model"]). One model in memory at a time."""
    from mlx_audio.tts.utils import load_model

    return load_model(model_path=model_path)


@cuda_loader("voice")
def _voxcpm2_cuda():
    from huggingface_hub import snapshot_download
    from voxcpm import VoxCPM

    from cvm.models_registry import repo

    path = snapshot_download(repo("voxcpm2"), local_files_only=True)
    # no denoiser (another download) and no torch.compile (needs Triton, which Windows lacks)
    return VoxCPM.from_pretrained(path, load_denoiser=False, local_files_only=True, optimize=False, device="cuda")


_LORA_MODELS: dict[str, object] = {}  # one VoxCPM2 built with LoRA layers, when hot-swapping isn't possible


from cvm.voice.finetune_speak import lora_config_of  # noqa: E402


def voxcpm2_with_lora(lora: str | None):
    """The CUDA VoxCPM2 with a fine-tuned LoRA (cvm/voice/finetune.py) applied, or the plain model. Tries the
    documented hot-swap (load_lora / unload_lora) on the cached model first; a model loaded without LoRA layers
    can't take one, so then a second instance is built with `lora_weights_path` (one slot, replaced on change)."""
    model = _voxcpm2_cuda()
    current = getattr(model, "_cvm_lora", None)
    if lora == current:
        return model
    try:
        if current:
            model.unload_lora()
        if lora:
            model.load_lora(lora)
        model._cvm_lora = lora
        return model
    except Exception:  # noqa: BLE001 — no LoRA layers in the base load
        if not lora:
            return model
    if lora not in _LORA_MODELS:
        from huggingface_hub import snapshot_download
        from voxcpm import VoxCPM

        from cvm.models_registry import repo

        _LORA_MODELS.clear()
        path = snapshot_download(repo("voxcpm2"), local_files_only=True)
        _LORA_MODELS[lora] = VoxCPM.from_pretrained(path, lora_weights_path=lora, lora_config=lora_config_of(lora), load_denoiser=False,
                                                    local_files_only=True, optimize=False, device="cuda")
    return _LORA_MODELS[lora]


def _voxcpm_cuda(text: str, profile: VoiceProfile, out: Path, same_lang: bool, seed: int | None,
                 instruct: str | None) -> Path:
    import soundfile as sf
    import torch

    if seed is not None:
        torch.manual_seed(seed % 2**32)
    params = {k: v for k, v in (profile.params or {}).items() if k in ("inference_timesteps", "cfg_value")}
    # same language: continuation of the reference (accent kept) + the reference as the voice; else voice only
    extra = {"prompt_wav_path": profile.ref_audio_path, "prompt_text": profile.ref_text} if same_lang else {}
    if instruct and not extra:  # voice design works only outside continuation (else the text is read aloud)
        text = f"({instruct}){text}"
    lora = (profile.params or {}).get("lora")
    model = voxcpm2_with_lora(lora if lora and Path(lora).exists() else None)
    wav = model.generate(text=text, reference_wav_path=profile.ref_audio_path, **extra, **params)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "voxcpm.wav"
        sf.write(raw, wav, model.tts_model.sample_rate)
        return to_wav(raw, out)


def _voxcpm(text: str, language: str, profile: VoiceProfile, out: Path, seed: int | None = None,
            instruct: str | None = None) -> Path:
    from cvm.models_registry import MLX

    if not MLX:
        return _voxcpm_cuda(text, profile, out, bool(profile.language == language and profile.ref_text), seed, instruct)
    import mlx.core as mx
    import numpy as np
    import soundfile as sf

    if seed is not None:
        mx.random.seed(seed % 2**32)  # character voices have negative ids; mlx wants an unsigned seed
    params = {k: v for k, v in (profile.params or {}).items() if k in ("inference_timesteps", "cfg_value")}
    same_lang = profile.language == language and profile.ref_text
    # same language: the reference also serves as the spoken prefix (closest timbre and accent);
    # another language: timbre only, so the reference's accent doesn't leak into the new language
    extra = {"prompt_audio": profile.ref_audio_path, "prompt_text": profile.ref_text} if same_lang else {}
    if extra:  # in continuation mode mlx-audio prepends "(instruct)" to the text and the model reads it aloud
        instruct = None
    mlx_model = (profile.params or {}).get("mlx_model")
    model = _voxcpm2(mlx_model) if mlx_model and Path(mlx_model).exists() else _voxcpm2()
    chunks = [np.array(r.audio, dtype="float32") for r in model.generate(
        text=text, ref_audio=profile.ref_audio_path, instruct=instruct, **extra, **params)]
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "voxcpm.wav"
        sf.write(raw, np.concatenate(chunks), model.sample_rate)
        return to_wav(raw, out)


def _say(text: str, language: str, out: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / "out.aiff"
        subprocess.run(["say", "-v", MACOS_VOICE[language], "-o", str(aiff), text], check=True)
        return to_wav(aiff, out)


LIVELY = "energetic, excited, upbeat, smiling"


def tone_instruct(tone: dict | None) -> str | None:
    """An old Chatterbox tone dict ({"exaggeration": 0.8, "cfg_weight": 0.35}) as a VoxCPM2 style instruction."""
    return LIVELY if tone and float(tone.get("exaggeration") or 0) >= 0.7 else None


def wanted_engine(profile: VoiceProfile | None) -> str:
    import os

    return ((profile.params or {}).get("engine") if profile else None) or os.environ.get("CVM_VOICE_ENGINE", "voxcpm2")


def synthesize(text: str, language: str, profile: VoiceProfile | None, out: Path, seed: int | None = None,
               instruct: str | None = None, tone: dict | None = None,
               context: tuple[str | None, str | None] | None = None) -> tuple[Path, str]:
    """Returns (wav path, engine used). `instruct` (speaking style) is used by engines that support it;
    `tone` = an old Chatterbox-style dict ({"exaggeration": 0.85}) kept in channel prefs; a lively one becomes an
    energetic style instruction when no `instruct` is given;
    `context` = (previous, next) text, used by ElevenLabs so the intonation carries across takes."""
    from cvm.models_registry import is_ready

    has_ref = bool(profile and profile.ref_audio_path and Path(profile.ref_audio_path).exists())
    params = (profile.params or {}) if profile else {}
    if params.get("engine") == "elevenlabs":
        # never swapped for a local clone mid-video: narrate.py picks the trained voice for the WHOLE video up front
        # when credits are short; a failure here fails the step
        from cvm.voice import eleven

        return eleven.synthesize(text, params, out, seed=seed, context=context), "elevenlabs"
    if params.get("lora") or params.get("mlx_model"):
        # the owner's trained voice: never swapped for the AI clone or macOS `say` (user's rule, 2026-11-22)
        if not (has_ref and wanted_engine(profile) == "voxcpm2" and is_ready("voxcpm2")):
            raise RuntimeError("voz treinada indisponível (VoxCPM2 ou referência ausente); a voz clonada não é usada")
        try:
            return _voxcpm(text, language, profile, out, seed, instruct), "voxcpm2"
        except Exception as e:
            raise RuntimeError(f"voz treinada falhou: {e}") from e
    if has_ref:  # a voice with a reference (avatar, character, narrator) is never read by macOS' voice instead
        if not is_ready("voxcpm2"):
            raise RuntimeError("VoxCPM2 não está instalado: a voz clonada precisa dele (Configurações > Modelos)")
        return _voxcpm(text, language, profile, out, seed, instruct or tone_instruct(tone)), "voxcpm2"
    if shutil.which("say") is None:
        raise RuntimeError("no TTS engine available")
    return _say(text, language, out), "macos"
