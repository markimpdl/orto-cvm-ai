import json

import httpx
import numpy as np
import soundfile as sf

from cvm.render import music


def test_openverse_filters_licences_and_short_tracks():
    results = [
        {"url": "https://x/a.mp3", "license": "by", "license_version": "4.0", "duration": 120000, "title": "A", "creator": "Ann"},
        {"url": "https://x/b.mp3", "license": "by-nc", "duration": 120000, "title": "B", "creator": "Bob"},
        {"url": "https://x/c.mp3", "license": "cc0", "duration": 20000, "title": "C", "creator": "Cy"},
        {"url": "https://x/d.mp3", "license": "by-nd", "duration": 120000, "title": "D", "creator": "Di"},
    ]
    seen = {}

    def handler(req: httpx.Request):
        seen.update(req.url.params)
        return httpx.Response(200, json={"results": results})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = music.openverse_tracks("dark ambient", c)
    assert [t["title"] for t in out] == ["A"]
    assert out[0]["license"] == "CC BY 4.0"
    assert seen["license_type"] == "commercial,modification"


def test_credit_sidecar(tmp_path):
    track = tmp_path / "t.mp3"
    track.write_bytes(b"")
    track.with_suffix(".json").write_text(json.dumps({"title": "Night", "creator": "Ann", "license": "CC BY 4.0"}))
    assert music.credit_for(track) == "Música: “Night” — Ann (CC BY 4.0)"


def test_energetic_start_skips_quiet_intro(tmp_path):
    sr = 16000
    t = np.arange(sr * 90) / sr
    tone = np.sin(2 * np.pi * 220 * t).astype("float32")
    tone[: sr * 20] *= 0.05  # 20 s quiet intro
    wav = tmp_path / "m.wav"
    sf.write(wav, tone * 0.5, sr)
    start = music.energetic_start(wav, need_s=30)
    assert 12 <= start <= 21
