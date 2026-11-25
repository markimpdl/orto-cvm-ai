"""A paragraph the trained voice reads in one take is cut back per scene with nothing added or trimmed inside it:
glued back, the scenes play the take exactly as it was read (user: "para ficar mais natural", 2026-11-22)."""
from types import SimpleNamespace

import numpy as np

from cvm.voice import narrate


def test_scenes_of_one_take_glue_back_into_the_take(tmp_path, monkeypatch):
    sr = narrate.SR
    take = (np.random.default_rng(0).standard_normal(sr * 3) * 0.1).astype("float32")
    take[int(0.95 * sr):int(1.05 * sr)] = 0  # natural pauses where the cuts fall
    take[int(1.95 * sr):int(2.05 * sr)] = 0
    monkeypatch.setattr(narrate, "trained_voice", lambda p: True)
    monkeypatch.setattr(narrate, "_best_take", lambda *a, **k: (take, "voxcpm2", {}))
    monkeypatch.setattr(narrate, "split_points", lambda parts, words, dur, lang: [1.0, 2.0])
    monkeypatch.setattr(narrate, "original_audio", lambda s, sc: None)
    monkeypatch.setattr(narrate.characters, "narrator_for", lambda series, lang: None)
    script = SimpleNamespace(content={"scenes": [{"index": i} for i in (1, 2, 3)]})
    sentences = {1: ["Uma frase."], 2: ["Outra frase."], 3: ["A última."]}
    joined, tails = narrate._cross_scene_takes(None, script, None, {}, SimpleNamespace(id=1), sentences, None, "pt-BR",
                                               tmp_path, 1, False, None, [], set())
    assert sum(len(joined[i]) for i in (1, 2, 3)) == len(take)
    assert np.array_equal(np.concatenate([joined[i] for i in (1, 2, 3)]), take)
    assert tails == {3}
