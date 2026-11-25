"""Voice studio: the avatar owner reads ~30 min, one sentence at a time, to fine-tune a voice model on his voice.

Each sentence of reading/estudio_<lang>.txt becomes one take: data/avatars/<id>/voice/dataset/<lang>/<idx>.wav
(48 kHz mono, edges trimmed) with its exact text, which is what VoxCPM2 LoRA training needs (scripts/voice_finetune.py,
run on a CUDA PC). Every take is checked when it arrives: level (clipping / too quiet), length and a whisper round
trip against the text; a bad take is marked "redo". `export_zip` packs the good takes with train/val/held-out
manifests (held-out sentences never train; they are the blind test after training).
"""

import io
import json
import random
import re
import threading
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from cvm import private
from cvm.media import run_ffmpeg
from cvm.paths import avatar_dir
from cvm.voice.text import prepare

READING = Path(__file__).parent / "reading"
_BUNDLED = READING  # the private override replaces only the bundled script (tests point READING elsewhere)
LANGS = ("pt-BR", "en-US")
SR = 48000
MAX_WER = 0.25
# foreign names (Shibuya, Montmartre, Park Güell…): whisper spells them its own way even when read right
MAX_WER_NAMES = 0.5
NAME_SECTIONS = ("Nomes estrangeiros", "Termos de tecnologia")  # English words mid-Portuguese: same


def max_wer(section: str, text: str = "") -> float:
    """Lenient for the names/English-terms blocks and for any sentence that is mostly proper names (capitalized words
    after the first: "o Louvre, a Torre Eiffel, Montmartre…"), whatever the block."""
    words = re.findall(r"[^\W\d_]+", text)
    names = sum(1 for w in words[1:] if w[0].isupper())
    if section.startswith(NAME_SECTIONS) or (len(words) > 3 and names / len(words) >= 0.2):
        return MAX_WER_NAMES
    return MAX_WER
CLIP_DB = -0.3  # peak above this = clipped
QUIET_DB = -38.0  # speech RMS below this = too far from the mic
HELD_OUT = 20  # sentences kept out of training for the blind test
_lock = threading.Lock()  # whisper + state writes, one take at a time


def sentences(language: str) -> list[dict]:
    """[{idx, text, section}] from the reading script (lines starting with '# ' are section instructions).
    private/voice/reading/estudio_<lang>.txt wins when present: the owner's takes were recorded from that text, and a
    take is matched to its sentence by index."""
    own = private.PRIVATE_DIR / "voice" / "reading" / f"estudio_{language}.txt"
    f = own if own.exists() and READING == _BUNDLED else READING / f"estudio_{language}.txt"
    if not f.exists():
        return []
    out, section = [], ""
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("# "):
            section = line[2:].strip()
            continue
        out.append({"idx": len(out), "text": line, "section": section})
    return out


def folder(avatar_id: int, language: str) -> Path:
    d = avatar_dir(avatar_id) / "voice" / "dataset" / language
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path(avatar_id: int, language: str) -> Path:
    return folder(avatar_id, language) / "state.json"


def state(avatar_id: int, language: str) -> dict:
    p = _state_path(avatar_id, language)
    return json.loads(p.read_text()) if p.exists() else {"takes": {}, "noise_db": None}


def _save_state(avatar_id: int, language: str, st: dict) -> None:
    p = _state_path(avatar_id, language)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    tmp.replace(p)


def _load(wav: Path) -> np.ndarray:
    import soundfile as sf

    a, _ = sf.read(str(wav), dtype="float32", always_2d=False)
    return a if a.ndim == 1 else a.mean(axis=1)


def _db(x: float) -> float:
    return round(20 * float(np.log10(max(x, 1e-9))), 1)


def levels(audio: np.ndarray) -> dict:
    """Peak and speech loudness (RMS of the loudest half of 20 ms windows, so pauses don't drag it down)."""
    if not len(audio):
        return {"peak_db": -120.0, "speech_db": -120.0}
    win = int(0.02 * SR)
    n = max(1, len(audio) // win)
    rms = np.sqrt(np.mean(audio[: n * win].reshape(n, -1) ** 2, axis=1) + 1e-12) if len(audio) >= win else np.array([np.sqrt(np.mean(audio ** 2))])
    loud = np.sort(rms)[len(rms) // 2:]
    return {"peak_db": _db(float(np.max(np.abs(audio)))), "speech_db": _db(float(np.mean(loud)))}


def summary(avatar_id: int, language: str) -> dict:
    sents = sentences(language)
    st = state(avatar_id, language)
    takes = st["takes"]
    ok = [t for t in takes.values() if t.get("status") == "ok"]
    return {
        "language": language,
        "sentences": [{**s, "take": takes.get(str(s["idx"]))} for s in sents],
        "total": len(sents),
        "ok": len(ok),
        "redo": sum(1 for t in takes.values() if t.get("status") == "redo"),
        "minutes_ok": round(sum(t.get("duration", 0) for t in ok) / 60, 1),
        "noise_db": st.get("noise_db"),
    }


def save_take(avatar_id: int, language: str, idx: int, upload: Path) -> dict:
    """Convert, trim, measure and whisper-check one sentence. Returns the take record."""
    from cvm.voice.qa import heard, trim_edges, word_error_rate

    sents = sentences(language)
    if not 0 <= idx < len(sents):
        raise ValueError("sentence index out of range")
    text = sents[idx]["text"]
    d = folder(avatar_id, language)
    wav = d / f"{idx:04d}.wav"
    tmp = d / f"_in_{idx:04d}.wav"
    run_ffmpeg("-i", str(upload), "-ac", "1", "-ar", str(SR), "-af", "highpass=f=60", str(tmp))
    audio = _load(tmp)
    tmp.unlink(missing_ok=True)
    lv = levels(audio)  # before trimming: clipping anywhere counts
    import soundfile as sf

    trimmed = trim_edges(audio, SR)
    pad = np.zeros(int(0.12 * SR), dtype="float32")  # a little air, < 0.5 s of silence per edge as the docs ask
    sf.write(str(wav), np.concatenate([pad, trimmed, pad]), SR, subtype="PCM_16")
    dur = round(len(trimmed) / SR, 2)
    problems = []
    if lv["peak_db"] > CLIP_DB:
        problems.append("som estourado: afaste um pouco o microfone ou baixe o ganho")
    if lv["speech_db"] < QUIET_DB:
        problems.append("muito baixo: chegue mais perto do microfone")
    if dur < 1.0:
        problems.append("curta demais")
    with _lock:
        try:
            got = heard(wav, language)
            wer = round(word_error_rate(text, got, language), 2)
        except Exception:  # noqa: BLE001 — whisper missing: keep the take, mark it unchecked
            got, wer = None, None
        if wer is not None and wer > max_wer(sents[idx]["section"], text):
            problems.append("a leitura não bateu com o texto")
        take = {"status": "redo" if problems else "ok", "problems": problems, "heard": got, "wer": wer,
                "duration": dur, **lv, "at": datetime.now(UTC).isoformat(timespec="seconds")}
        st = state(avatar_id, language)
        st["takes"][str(idx)] = take
        _save_state(avatar_id, language, st)
    return take


def delete_take(avatar_id: int, language: str, idx: int) -> None:
    with _lock:
        (folder(avatar_id, language) / f"{idx:04d}.wav").unlink(missing_ok=True)
        st = state(avatar_id, language)
        st["takes"].pop(str(idx), None)
        _save_state(avatar_id, language, st)


def save_noise(avatar_id: int, language: str, upload: Path) -> dict:
    """10 s of room silence: its level tells whether the room is quiet enough (noise under -55 dB is good)."""
    d = folder(avatar_id, language)
    tmp = d / "_noise.wav"
    run_ffmpeg("-i", str(upload), "-ac", "1", "-ar", str(SR), str(tmp))
    a = _load(tmp)
    tmp.unlink(missing_ok=True)
    noise = _db(float(np.sqrt(np.mean(a ** 2)))) if len(a) else None
    with _lock:
        st = state(avatar_id, language)
        st["noise_db"] = noise
        _save_state(avatar_id, language, st)
    verdict = "ótimo" if noise is not None and noise < -60 else "ok" if noise is not None and noise < -50 else "barulhento"
    return {"noise_db": noise, "verdict": verdict}


def export_zip(avatar_id: int, languages: tuple[str, ...] = LANGS, seed: int = 7) -> bytes:
    """Good takes + manifests for VoxCPM fine-tuning (paths relative to the zip root):
    train.jsonl / val.jsonl ({audio, text, duration, ref_audio?, ref_duration?}), heldout.jsonl (blind test)."""
    rng = random.Random(seed)
    items = []
    for lang in languages:
        st = state(avatar_id, lang)
        sents = {s["idx"]: s for s in sentences(lang)}
        for k, t in st["takes"].items():
            wav = folder(avatar_id, lang) / f"{int(k):04d}.wav"
            if t.get("status") == "ok" and wav.exists() and int(k) in sents:
                # the text as narration will feed it (voice/text.prepare: acronyms, numbers, loanwords), so the
                # trained voice learns the same written form it reads later
                items.append({"lang": lang, "idx": int(k), "text": prepare(sents[int(k)]["text"], lang), "path": wav,
                              "duration": t["duration"], "section": sents[int(k)]["section"]})
    if len(items) < 30:
        raise ValueError(f"só {len(items)} frases boas; grave pelo menos 30 (o ideal é mais de 250)")
    rng.shuffle(items)
    main = [i for i in items if i["lang"] == "pt-BR"] or items
    held = main[:min(HELD_OUT, len(main) // 10)]
    rest = [i for i in items if i not in held]
    n_val = max(3, len(rest) // 20)
    val, train = rest[:n_val], rest[n_val:]

    def rel(i):
        return f"audio/{i['lang']}/{i['idx']:04d}.wav"

    def rows(group, with_ref):
        out = []
        for i in group:
            row = {"audio": rel(i), "text": i["text"], "duration": i["duration"]}
            if with_ref and rng.random() < 0.4:  # the docs: 30–50% of samples with a same-speaker reference
                ref = rng.choice([j for j in train if j is not i and j["lang"] == i["lang"]] or train)
                row.update(ref_audio=rel(ref), ref_duration=ref["duration"])
            out.append(row)
        return out

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in items:
            z.write(i["path"], rel(i))
        for name, group, ref in (("train", train, True), ("val", val, False), ("heldout", held, False)):
            z.writestr(f"{name}.jsonl", "\n".join(json.dumps(r, ensure_ascii=False) for r in rows(group, ref)) + "\n")
        z.writestr("info.json", json.dumps({"avatar_id": avatar_id, "train": len(train), "val": len(val),
                                            "heldout": len(held), "minutes": round(sum(i["duration"] for i in items) / 60, 1),
                                            "made_at": datetime.now(UTC).isoformat(timespec="seconds")}, indent=1))
    return buf.getvalue()
