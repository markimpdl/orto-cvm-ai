import numpy as np

from cvm.voice import narrate


def test_scene_groups_join_while_key_matches_and_fit():
    texts = [(1, "a" * 100, ("x", None)), (2, "b" * 100, ("x", None)), (3, "c" * 100, None),
             (4, "d" * 100, ("x", None)), (5, "e" * 300, ("x", None)), (6, "f" * 50, ("y", None))]
    assert narrate.scene_groups(texts, limit=420) == [[1, 2], [3], [4, 5], [6]]


def test_scene_groups_split_when_too_long():
    texts = [(i, "w" * 150, ("x", None)) for i in range(1, 5)]
    assert narrate.scene_groups(texts, limit=420) == [[1, 2], [3, 4]]


def test_split_points_cut_in_the_pause_between_scenes():
    texts = ["Olha essa caminhonete.", "Ela saiu voando da ponte!"]
    words = [{"w": "Olha", "s": 0.0, "e": 0.3}, {"w": "essa", "s": 0.3, "e": 0.6},
             {"w": "caminhonete.", "s": 0.6, "e": 1.4}, {"w": "Ela", "s": 1.9, "e": 2.1},
             {"w": "saiu", "s": 2.1, "e": 2.4}, {"w": "voando", "s": 2.4, "e": 2.8},
             {"w": "da", "s": 2.8, "e": 2.9}, {"w": "ponte!", "s": 2.9, "e": 3.4}]
    assert narrate.split_points(texts, words, 3.5, "pt-BR") == [1.65]


def test_split_points_without_words_use_character_share():
    pts = narrate.split_points(["aaaa", "bbbbbbbbbbbb"], [], 4.0, "pt-BR")
    assert pts == [1.0]


def test_split_points_tolerate_a_misheard_boundary_word():
    texts = ["Eu tava na ponte.", "E ele pulou."]
    words = [{"w": "Eu", "s": 0, "e": 0.2}, {"w": "tava", "s": 0.2, "e": 0.5}, {"w": "na", "s": 0.5, "e": 0.6},
             {"w": "fonte", "s": 0.6, "e": 1.0}, {"w": "E", "s": 1.4, "e": 1.5}, {"w": "ele", "s": 1.5, "e": 1.7},
             {"w": "pulou", "s": 1.7, "e": 2.1}]
    (p,) = narrate.split_points(texts, words, 2.2, "pt-BR")
    assert 0.6 <= p <= 1.4


def test_quietest_finds_the_gap():
    sr = narrate.SR
    audio = np.concatenate([np.ones(sr), np.zeros(int(0.1 * sr)), np.ones(sr)]).astype("float32") * 0.5
    assert abs(narrate.quietest(audio, 0.9, window=0.3) - 1.05) < 0.03


def test_avatar_scene_in_mine_mode_gets_the_users_file():
    from cvm.visuals.select import _apply_media_mode

    used = []
    sc = {"index": 1, "visual": {"type": "avatar_pip", "media_id": 7, "media_start": 9}}
    out = _apply_media_mode(sc, "mine", [7, 8], used)
    assert out["visual"]["type"] == "avatar_pip" and out["visual"]["media_id"] == 7
    sc2 = {"index": 2, "visual": {"type": "avatar_full"}}
    assert _apply_media_mode(sc2, "mine", [7, 8], used)["visual"]["media_id"] == 8
    assert "media_id" not in _apply_media_mode(sc2, "mix", [7, 8], [])["visual"]
