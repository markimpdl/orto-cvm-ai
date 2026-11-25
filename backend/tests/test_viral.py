from datetime import datetime, timedelta

from cvm.scoring.viral import score, velocity_component
from cvm.trends.sources import _traffic, normalize_key

T0 = datetime(2026, 10, 12, 12, 0)


def test_velocity_growth_beats_flat():
    flat = velocity_component([(T0, 1000), (T0 + timedelta(hours=1), 1000)])
    growing = velocity_component([(T0, 1000), (T0 + timedelta(hours=1), 3000)])
    assert growing > flat == 0.5


def test_cross_source_and_rank_raise_score():
    base = dict(snapshots=[], metric=None, engagement=None, first_seen=T0, now=T0)
    low, _ = score(rank=40, n_sources=1, **base)
    high, parts = score(rank=1, n_sources=3, **base)
    assert high > low
    assert parts["cross_source"] == 1.0


def test_helpers():
    assert _traffic("200K+") == 200_000
    assert _traffic("2M+") == 2_000_000
    assert normalize_key("São Paulo FC!") == "sao paulo fc"


def test_cluster_cross_source():
    from cvm.scoring.cluster import cluster

    keys = ["lulanapaulista", "lula na paulista", "corinthians", "taylor fritz", "fritz taylor wins final", "gta 6 leak"]
    ids = cluster(keys)
    assert ids[0] == ids[1]
    assert ids[2] not in (ids[0], ids[5])
    assert ids[3] == ids[4]
    a, b = cluster(["los angeles lakers", "rachel slams southern california los angeles times"])
    assert a != b


def test_caption_alignment_keeps_script_words():
    from cvm.render.captions import align_script

    script = ["Vazaram", "24", "minutos", "do", "Extended", "Leek"]
    heard = [{"w": "vazaram", "s": 0.0, "e": 0.4}, {"w": "24", "s": 0.4, "e": 0.6}, {"w": "minutos", "s": 0.6, "e": 1.0},
             {"w": "do", "s": 1.0, "e": 1.1}, {"w": "Extended", "s": 1.1, "e": 1.5}, {"w": "Lake", "s": 1.5, "e": 1.9}]
    est = [{"w": w, "s": i * 0.3, "e": (i + 1) * 0.3} for i, w in enumerate(script)]
    out = align_script(script, heard, est)
    assert [w["w"] for w in out] == script
    assert out[4]["s"] == 1.1 and out[5]["s"] >= 1.5 - 1e-9 or out[5]["s"] >= out[4]["s"]
