"""The presenter looks into the lens: chunks whose face crops look away are redone once, kept only if better."""
from pathlib import Path

from cvm.avatar import talking
from cvm.visuals import judge


def test_chunks_looking_away_are_redone(tmp_path, monkeypatch):
    work = tmp_path / "w"
    work.mkdir()
    for i in range(3):
        (work / f"raw_{i:02d}.mp4").write_bytes(b"x")
        (work / f"seg_{i:02d}.wav").write_bytes(b"x")
    monkeypatch.setattr(talking.settings, "visual_judge", True)
    monkeypatch.setattr(judge, "ready", lambda: True)
    monkeypatch.setattr(talking, "face_box", lambda still: (0.3, 0.0, 0.4, 0.5))
    monkeypatch.setattr(talking, "_away_frames", lambda raw, s, box, tmp: [Path(f"{raw.stem}_{k}") for k in range(3)])
    looks = {"raw_00": "at the camera", "raw_01": "to the side", "raw_02": "at the camera", "redo_01": "at the camera"}
    monkeypatch.setattr(judge, "gaze", lambda faces: [looks[str(f).rsplit("_", 1)[0]] for f in faces])
    redone, encoded = [], []

    def fake_chunk(bin_, seg, anchor, still, prompt, seconds, raw, seeds):
        redone.append(raw.name)
        raw.write_bytes(b"new")
        return 0.5

    monkeypatch.setattr(talking, "_chunk", fake_chunk)
    monkeypatch.setattr(talking, "_encode_part", lambda raw, s, part: encoded.append(part.name))
    talking._fix_gaze(work, [0.0, 5.0, 10.0, 15.0], tmp_path / "still.png", "p", Path("ltx"))
    assert redone == ["redo_01.mp4"]  # only the chunk looking to the side
    assert (work / "raw_01.mp4").read_bytes() == b"new" and encoded == ["part_01.mp4"]
    assert all((work / f"gaze_ok_{i:02d}").exists() for i in range(3))
    talking._fix_gaze(work, [0.0, 5.0, 10.0, 15.0], tmp_path / "still.png", "p", Path("ltx"))
    assert redone == ["redo_01.mp4"]  # checked chunks are not judged again
