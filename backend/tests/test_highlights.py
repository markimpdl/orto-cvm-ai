import numpy as np

from cvm.visuals.highlights import _ranges, segment_start


def test_ranges_bridge_short_gaps_and_drop_short_pieces():
    keep = np.array([1, 1, 1, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1, 1], bool)
    assert _ranges(keep, 17.5) == [{"start": 0.0, "end": 6.0}, {"start": 14.0, "end": 17.5}]


def test_segment_start_clamps_into_kept_range():
    meta = {"segments": [{"start": 2.0, "end": 10.0}, {"start": 20.0, "end": 28.0}]}
    assert segment_start(meta, 5.0) == 5.0
    assert segment_start(meta, 14.0) == 20.0
    assert segment_start(meta, 0.0) == 2.0
    assert segment_start({}, 7.0) == 7.0


def test_loudness_treats_nan_as_silence(monkeypatch):
    # ffmpeg prints "M=nan" over silent stretches; one NaN made every second of a recording "dull"
    from pathlib import Path

    import numpy as np

    from cvm.visuals import highlights

    err = "\n".join(f"frame:{i} pts:{i} pts_time:{i * 0.5}\nlavfi.r128.M={v}"
                    for i, v in enumerate(["nan", "nan", "-20.5", "-18.0"]))
    monkeypatch.setattr(highlights, "_run", lambda args: err)
    out = highlights._loudness(Path("x.mp4"), 2)
    assert np.isfinite(out).all()
    assert out[0] == -70.0 and out[1] == -19.25
