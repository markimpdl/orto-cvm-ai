"""Configurações > Modelos: every job lists its Mac and Windows models; incompatible hardware is never 'installed'."""

from cvm import model_catalog as mc


def test_every_role_points_at_known_models_and_selectors_match_engines():
    from cvm import engines

    for r in mc.ROLES:
        assert set(r["mac"]) | set(r["windows"]) <= set(mc.M), r["key"]
        assert r["mac"] or r["windows"], r["key"]
        if not r["windows"]:
            assert r.get("fallback"), f"{r['key']}: say what Windows does without a model"
        sel = r.get("select")
        if sel == "image":
            assert {mc.ENGINE_ID[k] for k in r["mac"] + r["windows"]} <= set(engines.IMAGE_ENGINES)
        elif sel:
            assert sel.split(":")[1] in engines.VIDEO_PURPOSES
            assert {mc.ENGINE_ID[k] for k in r["mac"]} <= set(engines.VIDEO_ENGINES)


def test_wrong_platform_reads_incompatible(monkeypatch):
    monkeypatch.setattr(mc, "MLX", True)
    calls = []
    monkeypatch.setitem(mc.M["whisper-win"], "ready", lambda: calls.append(1) or True)
    s = mc._status("whisper-win")
    assert s["state"] == "incompatible" and not s["ready"] and not calls


def test_view_shape(monkeypatch):
    monkeypatch.setattr(mc, "disk_bytes", lambda: {k: 0 for k in mc.M})
    for m in mc.M.values():
        monkeypatch.setitem(m, "ready", lambda: False)
    v = mc.view()
    assert v["platform"] in ("mac", "windows")
    assert {r["key"] for r in v["roles"]} == {r["key"] for r in mc.ROLES}
    assert all(i["state"] in ("partial", "missing", "incompatible") for i in v["inventory"])
    assert all(i["used_by"] for i in v["inventory"])
