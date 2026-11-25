from cvm.avatar.capture import pick_reference_window
from cvm.avatar.reading import FILES, sections, spoken_text


def test_reference_window_ends_on_a_pause():
    pauses = [(0.0, 0.8), (4.0, 4.3), (9.5, 9.9), (12.6, 13.0), (17.0, 17.4)]
    start, end = pick_reference_window(pauses, 60)
    assert start == 0.65 and abs(end - 12.7) < 1e-6


def test_reference_window_without_pauses_uses_target_length():
    assert pick_reference_window([], 60) == (0.0, 12.0)
    assert pick_reference_window([], 7) == (0.0, 7)


def test_reading_scripts_ship_inside_the_package():
    # bakeoff/ is left out of the public copy; the avatar recording guide must not depend on it
    from pathlib import Path

    import cvm
    from cvm.avatar import reading

    assert reading.TEXTS.is_relative_to(Path(cvm.__file__).parent)
    assert all((reading.TEXTS / f"{name}.txt").is_file() for name in FILES.values())


def test_reading_scripts_have_sections_and_skip_cues():
    for kind in FILES:
        secs = sections(kind)
        assert len(secs) >= 3 and all(s["title"] for s in secs)
    assert "[" not in spoken_text("video") and "Olhe para a lente" not in spoken_text("video")
    assert spoken_text("pt-BR", first_only=True).startswith("Em dois mil")


def test_leading_black_frames_are_detected(tmp_path):
    from cvm.avatar.capture import picture_start
    from cvm.media import run_ffmpeg

    clip = tmp_path / "rec.mp4"
    run_ffmpeg("-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.2:r=25", "-f", "lavfi", "-i", "color=c=gray:s=320x240:d=1:r=25",
               "-filter_complex", "[0][1]concat=n=2:v=1", "-pix_fmt", "yuv420p", str(clip))
    assert 0.15 <= picture_start(clip) <= 0.3
    plain = tmp_path / "plain.mp4"
    run_ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=320x240:d=1:r=25", "-pix_fmt", "yuv420p", str(plain))
    assert picture_start(plain) == 0.0
