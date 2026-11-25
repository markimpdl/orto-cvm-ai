from cvm import models_registry


def test_load_waits_until_ram_frees(monkeypatch):
    free = iter([2.0, 3.0, 30.0])
    slept = []
    monkeypatch.setattr(models_registry, "free_ram_gb", lambda: next(free))
    monkeypatch.setattr("time.sleep", slept.append)
    models_registry._wait_for_ram("image")
    assert len(slept) == 2


def test_load_goes_ahead_after_the_deadline(monkeypatch):
    slept = []
    monkeypatch.setattr(models_registry, "free_ram_gb", lambda: 1.0)
    monkeypatch.setattr(models_registry, "RAM_WAIT_S", 0)
    monkeypatch.setattr("time.sleep", slept.append)
    models_registry._wait_for_ram("image")
    assert slept == []
