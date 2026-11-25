from cvm.scripting.schema import Visual
from cvm.visuals.footage import BLOCK, _acceptable_title, _clip_window


def test_leaks_and_reactions_are_refused():
    for t in ["GTA 6 LEAKED gameplay", "GTA VI vazou!", "Vazamento completo", "Reacting? no: REACTION to trailer"]:
        assert not _acceptable_title(t), t
    for t in ["Grand Theft Auto VI Trailer 2", "Enchente no RS: resgate", "Modo história explicado"]:
        assert _acceptable_title(t), t
    assert BLOCK.search("reach") is None


def test_clip_window_stays_inside_and_varies_by_scene():
    a, b = _clip_window("url2", 120, 8), _clip_window("url5", 120, 8)
    assert 24 <= a <= 111 and 24 <= b <= 111 and a != b
    assert _clip_window("x", 9, 8) == 0.0


def test_footage_visual_schema():
    v = Visual(type="footage", clip_query="GTA 6 trailer", official_channels=["Rockstar Games"])
    assert v.official_channels == ["Rockstar Games"]
