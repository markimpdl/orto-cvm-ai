from cvm.setup.requirements import Hardware, check, detect, gate

MAC32 = Hardware("mac", "Apple M4", 32, 0, 500)
MAC48 = Hardware("mac", "Apple M5 Pro", 48, 0, 500)


def test_gate_stops_small_or_other():
    assert gate(Hardware("mac", "Apple M1", 16, 0, 500)).feature == "base"
    assert gate(Hardware("other", "Intel", 64, 0, 500)) is not None
    assert gate(MAC32) is None


def test_avatar_blocked_on_32gb_mac_with_reason():
    b = check(["base", "avatar"], MAC32)
    assert [x.feature for x in b] == ["avatar"] and "48 GB" in b[0].reason and "32 GB" in b[0].reason


def test_all_fit_on_48gb_mac():
    assert check(["base", "ai_images", "avatar", "animation", "music"], MAC48) == []


def test_disk_total_blocks():
    b = check(["base", "avatar"], Hardware("mac", "M5", 48, 0, 60))
    assert any(x.feature == "disk" for x in b)


def test_windows_avatar_pending_until_1b():
    win = Hardware("windows", "RTX 5090", 64, 32, 900)
    assert [x.feature for x in check(["base", "avatar"], win)] == ["avatar"]


def test_windows_video_features_say_they_need_24gb_and_are_not_there_yet():
    win = Hardware("windows", "RTX 5060", 32, 8, 900)
    got = {b.feature: b.reason for b in check(["avatar", "animation", "kids"], win)}
    assert set(got) == {"avatar", "animation", "kids"}
    for reason in got.values():
        assert "24 GB" in reason and "ainda não está disponível no Windows" in reason


def test_windows_gate_needs_vram():
    assert gate(Hardware("windows", "GTX 1660", 32, 6, 500)).feature == "base"
    assert gate(Hardware("windows", "RTX 5060", 32, 8, 500)) is None  # the owner's 8 GB card
    assert gate(Hardware("windows", "RTX 4070", 32, 12, 500)) is None


def test_windows_base_row_numbers():
    from cvm.setup.requirements import FEATURES
    win, mac = FEATURES["base"].windows, FEATURES["base"].mac
    assert (win.ram_gb, win.vram_gb, win.disk_gb) == (32, 8, 25)
    assert (mac.ram_gb, mac.vram_gb, mac.disk_gb) == (32, 0, 15)  # Mac row unchanged


def test_windows_ram_rounded_to_whole_gb(monkeypatch):
    # a 32 GB PC reports ~31.8 GB to Windows (hardware-reserved memory); it must pass the 32 GB gate
    import sys
    import types

    import cvm.setup.requirements as r
    monkeypatch.setattr(r.sys, "platform", "win32")
    monkeypatch.setattr(r.shutil, "which", lambda _n: "nvidia-smi")
    monkeypatch.setattr(r, "_run", lambda _c: "NVIDIA GeForce RTX 5060, 8151")
    fake = types.SimpleNamespace(virtual_memory=lambda: types.SimpleNamespace(total=int(31.8 * 1024**3)))
    monkeypatch.setitem(sys.modules, "psutil", fake)
    hw = detect()
    assert (hw.chip, hw.ram_gb, hw.vram_gb) == ("NVIDIA GeForce RTX 5060", 32, 8.0)
    assert gate(hw) is None


def test_detect_never_crashes(monkeypatch):
    import cvm.setup.requirements as r
    monkeypatch.setattr(r.sys, "platform", "linux")
    assert detect().platform == "other"
    monkeypatch.setattr(r.sys, "platform", "win32")
    monkeypatch.setattr(r.shutil, "which", lambda _n: None)
    hw = detect()
    assert hw.platform == "windows" and hw.vram_gb == 0


def _disk(feats, free, ram=48):
    return any(b.feature == "disk" for b in check(feats, Hardware("mac", "M5", ram, 0, free)))


def test_animation_alone_needs_ltx_pack():
    # base 15 + pack 75 + tool 5 + gemma 9 + refine LoRA 1 = 105, plus 20 reserve = 125
    assert _disk(["base", "animation"], 124)
    assert not _disk(["base", "animation"], 126)


def test_shared_pack_counted_once():
    # pack 75 + tool 5 + gemma 9 + refine LoRA 1 = 90, once
    assert not _disk(["base", "avatar", "animation"], 15 + 1 + 90 + 20 + 1)
    assert _disk(["base", "avatar", "animation"], 15 + 1 + 90 + 20 - 1)
    # kids own 10 on top of the same pack
    assert not _disk(["base", "avatar", "kids"], 15 + 1 + 90 + 10 + 20 + 1)
    assert _disk(["base", "avatar", "kids"], 15 + 1 + 90 + 10 + 20 - 1)


def test_pack_follows_settings(monkeypatch):
    from cvm.config import settings
    monkeypatch.setattr(settings, "ltx_pack", "2.3")
    assert not _disk(["base", "animation"], 15 + 30 + 5 + 9 + 20 + 1)  # no refine LoRA on 2.3
    assert _disk(["base", "animation"], 15 + 30 + 5 + 9 + 20 - 1)


def test_requires_expanded():
    small = Hardware("mac", "M4", 32, 0, 40)
    assert any(b.feature == "disk" for b in check(["ai_images"], small))  # base + ai_images = 45
    assert [b.feature for b in check(["avatar"], MAC32)] == ["avatar", "disk"] or \
        "avatar" in [b.feature for b in check(["avatar"], MAC32)]
    assert [b.feature for b in check(["base"], Hardware("mac", "M1", 16, 0, 500))] == ["base"]


def test_unknown_feature_raises():
    import pytest
    with pytest.raises(ValueError, match="nope"):
        check(["nope"], MAC32)


def test_disk_uses_what_is_missing_when_known():
    # static estimate for base+avatar+animation is ~126 GB: with 60 GB free it blocks, unless little is missing
    hw = Hardware("mac", "M5", 48, 0, 60)
    feats = ["base", "avatar", "animation"]
    assert _disk(feats, 60)  # unknown → static estimate
    assert not any(b.feature == "disk" for b in check(feats, hw, missing_gb=0))  # everything ready
    assert not any(b.feature == "disk" for b in check(feats, hw, missing_gb=39))  # 39 + 20 reserve ≤ 60
    blk = [b for b in check(feats, hw, missing_gb=41) if b.feature == "disk"]  # 41 + 20 > 60
    assert blk and "41 GB" in blk[0].reason
