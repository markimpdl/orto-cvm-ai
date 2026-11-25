from cvm.visuals.sources import relevance


def test_phrase_match_scores_high():
    assert relevance("GTA 6", {"text": "GTA 6 Fan Cover"}) == 1.0


def test_file_numbering_and_stray_numbers_do_not_count():
    assert relevance("GTA 6", {"text": "Alfa Romeo 147 GTA (6)"}) < 0.5
    assert relevance("GTA 6", {"text": "brigade training jump gta 2014 6 soldiers"}) < 0.5


def test_accents_and_stopwords_are_ignored():
    assert relevance("praia de Copacabana", {"text": "Praia Copacabana ao pôr do sol"}) == 1.0
    assert relevance("São Paulo", {"text": "sao paulo skyline"}) == 1.0
    assert relevance("video game controller", {"text": "kitchen table"}) == 0.0


def test_tag_only_matches_count_half():
    cand = {"text": "warren's lobster hut", "tags": "car garage neon street racing night"}
    assert relevance("neon street racing at night", cand) == 0.5
    assert relevance("neon street racing", {"text": "Neon street racing", "tags": ""}) == 1.0


def test_wan_frames_are_4k_plus_1_within_model_limits():
    from cvm.visuals.sources import wan_frames

    for s in (0.5, 2.0, 3.3, 5.0, 9.0):
        n = wan_frames(s)
        assert (n - 1) % 4 == 0 and 49 <= n <= 121
    assert wan_frames(5.0) == 121 and wan_frames(3.0) == 73
