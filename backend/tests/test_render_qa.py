from cvm.render.qa import blocking_from, warnings_from


def test_render_analysis_rules():
    ok = {"black_s": 0.5, "frozen": [], "silences": [], "lufs": -14.4}
    assert warnings_from(ok) == [] and blocking_from(ok) == []
    bad = {"black_s": 3.0, "frozen": [5.2], "silences": [2.5], "lufs": -20.0}
    assert blocking_from(bad) == ["tela preta por 3.0s"]
    assert warnings_from(bad) == ["imagem parada por 5s", "silêncio de 2s", "volume -20 LUFS (alvo -14)"]


def test_camera_moves_cover_schema():
    from typing import get_args

    from cvm.render.compose import AUTO_MOVES, camera_expr
    from cvm.scripting.schema import CameraMove

    for m in get_args(CameraMove):
        if m != "static":
            z, x, y = camera_expr(m, 90)
            assert z and x and y
    assert set(AUTO_MOVES) <= set(get_args(CameraMove))
