import io
import json
import zipfile

import numpy as np
import pytest
import soundfile as sf

from cvm.voice import studio


@pytest.fixture
def env(tmp_path, monkeypatch):
    reading = tmp_path / "reading"
    reading.mkdir()
    lines = ["# Narração calma"] + [f"Frase de teste número {w} para o estúdio de voz do avatar." for w in
                                      ("um dois três quatro cinco seis sete oito nove dez onze doze treze catorze quinze "
                                       "dezesseis dezessete dezoito dezenove vinte vinte_e_um vinte_e_dois vinte_e_três "
                                       "vinte_e_quatro vinte_e_cinco vinte_e_seis vinte_e_sete vinte_e_oito vinte_e_nove "
                                       "trinta trinta_e_um trinta_e_dois trinta_e_três trinta_e_quatro").split()]
    (reading / "estudio_pt-BR.txt").write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setattr(studio, "READING", reading)
    monkeypatch.setattr(studio, "avatar_dir", lambda aid: tmp_path / "avatars" / str(aid))
    heard = {"text": None}
    monkeypatch.setattr("cvm.voice.qa.heard", lambda wav, lang: heard["text"])
    return tmp_path, heard


def _voice(tmp_path, amp=0.3, seconds=2.5, name="take.wav"):
    sr = 48000
    t = np.arange(int(sr * seconds)) / sr
    a = amp * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    a = np.concatenate([np.zeros(sr // 2), a, np.zeros(sr // 2)]).astype("float32")
    p = tmp_path / name
    sf.write(p, a, sr)
    return p


def test_sentences_keep_their_section(env):
    s = studio.sentences("pt-BR")
    assert len(s) == 34 and s[0]["section"] == "Narração calma" and s[3]["idx"] == 3


def test_good_take_is_trimmed_and_ok(env):
    tmp, heard = env
    heard["text"] = studio.sentences("pt-BR")[0]["text"]
    t = studio.save_take(1, "pt-BR", 0, _voice(tmp))
    assert t["status"] == "ok" and t["wer"] == 0
    out, sr = sf.read(studio.folder(1, "pt-BR") / "0000.wav")
    assert sr == 48000 and len(out) / sr < 3.0  # 0.5 s silences trimmed to ~0.12 s
    assert studio.summary(1, "pt-BR")["ok"] == 1


def test_clipped_quiet_and_misread_takes_ask_for_redo(env):
    tmp, heard = env
    heard["text"] = studio.sentences("pt-BR")[1]["text"]
    assert "estourado" in " ".join(studio.save_take(1, "pt-BR", 1, _voice(tmp, amp=1.4, name="c.wav"))["problems"])
    assert "baixo" in " ".join(studio.save_take(1, "pt-BR", 1, _voice(tmp, amp=0.004, name="q.wav"))["problems"])
    heard["text"] = "outra coisa completamente diferente do texto"
    t = studio.save_take(1, "pt-BR", 1, _voice(tmp, name="m.wav"))
    assert t["status"] == "redo" and "bateu" in " ".join(t["problems"])
    studio.delete_take(1, "pt-BR", 1)
    assert "1" not in studio.state(1, "pt-BR")["takes"]


def test_export_needs_enough_takes_then_splits(env):
    tmp, heard = env
    sents = studio.sentences("pt-BR")
    with pytest.raises(ValueError):
        studio.export_zip(1, ("pt-BR",))
    for s in sents:
        heard["text"] = s["text"]
        studio.save_take(1, "pt-BR", s["idx"], _voice(tmp, name=f"{s['idx']}.wav"))
    z = zipfile.ZipFile(io.BytesIO(studio.export_zip(1, ("pt-BR",))))
    rows = {n: [json.loads(x) for x in z.read(f"{n}.jsonl").decode().splitlines() if x] for n in ("train", "val", "heldout")}
    texts = [r["text"] for g in rows.values() for r in g]
    assert len(texts) == len(set(texts)) == 34  # no sentence in two splits
    assert len(rows["heldout"]) == 3 and len(rows["val"]) >= 3
    for r in rows["train"]:
        assert r["audio"] in z.namelist() and r.get("ref_audio", r["audio"]) in z.namelist()
        assert r.get("ref_audio") != r["audio"]


def test_noise_floor(env):
    tmp, _ = env
    p = tmp / "n.wav"
    sf.write(p, (np.random.default_rng(0).standard_normal(48000) * 0.0005).astype("float32"), 48000)
    r = studio.save_noise(1, "pt-BR", p)
    assert r["noise_db"] < -60 and r["verdict"] == "ótimo"
