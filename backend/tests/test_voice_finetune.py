import json

from cvm.voice import finetune


def _data(tmp_path):
    d = tmp_path / "dataset"
    (d / "audio" / "pt-BR").mkdir(parents=True)
    train, held = [], []
    for i in range(6):
        p = d / "audio" / "pt-BR" / f"{i:04d}.wav"
        p.write_bytes(b"RIFF" + bytes([i]))
        (train if i < 4 else held).append({"audio": f"audio/pt-BR/{i:04d}.wav", "text": f"frase {i}", "duration": 3 + i * 4})
    (d / "train.jsonl").write_text("\n".join(json.dumps(r) for r in train))
    (d / "heldout.jsonl").write_text("\n".join(json.dumps(r) for r in held))
    return d


def test_config_follows_the_docs_and_fits_the_card(tmp_path):
    d = _data(tmp_path)
    for vram, batch in ((24, "batch_size: 16"), (16, "batch_size: 4"), (12, "batch_size: 2")):
        cfg = finetune.write_config(tmp_path, d, "/models/VoxCPM2", 400, 32, vram).read_text()
        assert batch in cfg and "sample_rate: 16000" in cfg and "max_steps: 400" in cfg
        assert "  enable_dit: true" in cfg and "  r: 32" in cfg and "learning_rate: 0.0001" in cfg
        assert f'train_manifest: {json.dumps(str(d / "train.abs.jsonl"))}' in cfg


def test_reference_is_the_longest_clip_under_15s(tmp_path):
    d = _data(tmp_path)
    assert finetune.best_reference(d)["audio"] == "audio/pt-BR/0003.wav"  # 15 s; nothing longer qualifies


def test_blind_test_hides_which_is_which(tmp_path):
    d = _data(tmp_path)
    work = tmp_path / "work"
    best = work / "checkpoints" / "step_0000300"
    best.mkdir(parents=True)
    for sub in ("base", best.name):
        (work / "eval" / sub).mkdir(parents=True)
        for i in range(2):
            (work / "eval" / sub / f"{i:02d}.wav").write_bytes(sub.encode())
    out = finetune.blind_test(work, d, best)
    files = sorted(p.name for p in out.glob("*.wav"))
    assert files == ["01_A.wav", "01_B.wav", "01_C.wav", "02_A.wav", "02_B.wav", "02_C.wav"]
    key = json.loads((out / "key.json").read_text())
    assert sorted(key[0][k] for k in "ABC") == ["clone_hoje", "real", "treinada"]
    assert (out / f"01_{next(k for k in 'ABC' if key[0][k] == 'treinada')}.wav").read_bytes() == best.name.encode()


def test_merge_folds_the_lora_into_the_linear_weights(tmp_path):
    import torch
    from safetensors.torch import load_file, save_file

    pre, ck, out = tmp_path / "pre", tmp_path / "step_0000300", tmp_path / "merged"
    pre.mkdir(); ck.mkdir()
    torch.manual_seed(0)
    w = torch.randn(8, 6, dtype=torch.bfloat16)
    save_file({"base_lm.layers.0.self_attn.q_proj.weight": w, "stop_head.weight": torch.ones(2, 2)}, str(pre / "model.safetensors"))
    (pre / "config.json").write_text("{}")
    (pre / "audiovae.pth").write_bytes(b"vae")
    a, b = torch.randn(4, 6), torch.randn(8, 4)
    save_file({"base_lm.layers.0.self_attn.q_proj.lora_A": a, "base_lm.layers.0.self_attn.q_proj.lora_B": b}, str(ck / "lora_weights.safetensors"))
    (ck / "lora_config.json").write_text(json.dumps({"base_model": "x", "lora_config": {"r": 4, "alpha": 8}}))
    finetune.merge_lora(ck, str(pre), out)
    m = load_file(str(out / "model.safetensors"))
    x = torch.randn(3, 6)
    with_lora = x @ w.float().T + (x @ a.T @ b.T) * 2.0  # what LoRALinear computes (scaling = alpha / r)
    assert torch.allclose(x @ m["base_lm.layers.0.self_attn.q_proj.weight"].float().T, with_lora, atol=0.15)
    assert m["base_lm.layers.0.self_attn.q_proj.weight"].dtype == torch.bfloat16
    assert torch.equal(m["stop_head.weight"], torch.ones(2, 2))
    assert (out / "config.json").exists() and (out / "audiovae.pth").read_bytes() == b"vae"
