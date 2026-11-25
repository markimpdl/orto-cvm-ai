"""Lucas: packed in the repo, installed by the wizard."""
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from cvm.models import Avatar, VoiceProfile
from cvm.setup import lucas

PACK = Path(__file__).resolve().parents[2] / "scripts" / "pack_lucas.py"


def _pack():
    spec = importlib.util.spec_from_file_location("pack_lucas", PACK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def env(tmp_path, monkeypatch):
    from cvm.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    zp = tmp_path / "lucas.zip"
    man = {"avatar": {"name": "Lucas", "status": "ready", "notes": None},
           "voices": [{"language": "pt-BR", "engine": "voxcpm2", "ref_file": "voice/ref_pt-BR.wav",
                       "ref_text": "oi", "params": {}},
                      {"language": "en-US", "engine": "voxcpm2", "ref_file": "voice/ref_en-US.wav",
                       "ref_text": "hi", "params": {"made_from": "pt-BR"}}]}
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("avatar.json", json.dumps(man))
        for n in ("base/base.mp4", "base/still.jpg", "podcast/base.mp4", "voice/ref_pt-BR.wav",
                  "voice/ref_en-US.wav", "thumb_cutout.png"):
            z.writestr(n, b"x")
    monkeypatch.setattr(lucas, "ZIP", zp)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_install_lucas_creates_avatar_and_two_voices(env):
    av = lucas.install(env)
    assert av.status == "ready" and av.name == "Lucas"
    voices = env.exec(select(VoiceProfile)).all()
    assert {v.language for v in voices} == {"pt-BR", "en-US"}
    for v in voices:
        assert Path(v.ref_audio_path).exists() and f"/avatars/{av.id}/" in v.ref_audio_path
        assert v.engine == "voxcpm2"
    assert (Path(av.base_video_dir) / "base.mp4").exists()
    assert Path(av.podcast_video_dir).is_dir()


def test_install_lucas_idempotent(env):
    a = lucas.install(env)
    b = lucas.install(env)
    assert a.id == b.id
    assert len(env.exec(select(Avatar)).all()) == 1
    assert len(env.exec(select(VoiceProfile)).all()) == 2


def test_zip_has_no_arabic_or_owner_paths(tmp_path):
    src = tmp_path / "avatars" / "2"
    rows = [{"language": l, "ref_audio_path": f"/Users/someone/data/avatars/2/voice/ref_{l}.wav",
             "ref_text": "t", "params": {}} for l in ("pt-BR", "en-US", "ar-AE")]
    man = _pack().manifest(src, {"name": "Lucas (IA)", "status": "ready", "notes": None}, rows)
    text = json.dumps(man)
    assert "ar-AE" not in text and "/Users/" not in text
    assert man["avatar"]["name"] == "Lucas" and len(man["voices"]) == 2


def _bad_zip(tmp_path, kind):
    zp = tmp_path / f"bad_{kind}.zip"
    man = {"avatar": {"name": "Lucas", "status": "ready"},
           "voices": [{"language": "pt-BR", "ref_file": "voice/ref_pt-BR.wav"}]}
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("avatar.json", json.dumps(man))
        z.writestr("base/base.mp4", b"x")
        if kind == "slip":
            z.writestr("../evil.txt", b"x")
        # "missing": the voice file the manifest names is not in the zip
    return zp


@pytest.mark.parametrize("kind", ["slip", "missing"])
def test_failed_install_leaves_nothing_and_retry_works(env, tmp_path, kind):
    from cvm.config import settings
    with pytest.raises(lucas.LucasError):
        lucas.install(env, _bad_zip(tmp_path, kind))
    assert env.exec(select(Avatar)).all() == [] and env.exec(select(VoiceProfile)).all() == []
    avs = settings.data_dir / "avatars"
    assert not avs.exists() or list(avs.iterdir()) == []
    assert not (tmp_path / "evil.txt").exists() and not (settings.data_dir / "evil.txt").exists()
    assert lucas.install(env).status == "ready"


def test_corrupt_member_leaves_nothing(env, tmp_path):
    from cvm.config import settings
    zp = tmp_path / "corrupt.zip"
    shutil_src = zipfile.ZipFile(lucas.ZIP).filename
    data = bytearray(Path(shutil_src).read_bytes())
    i = data.find(b"base/base.mp4")
    data[i + 40:i + 44] = b"\xff\xff\xff\xff"  # damage the stored member bytes
    zp.write_bytes(bytes(data))
    with pytest.raises(lucas.LucasError):
        lucas.install(env, zp)
    assert env.exec(select(Avatar)).all() == []
    avs = settings.data_dir / "avatars"
    assert not avs.exists() or list(avs.iterdir()) == []


def test_broken_install_is_repaired_without_duplicate(env):
    import shutil
    a = lucas.install(env)
    shutil.rmtree(Path(a.base_video_dir).parent)
    b = lucas.install(env)
    assert b.id == a.id and len(env.exec(select(Avatar)).all()) == 1
    voices = env.exec(select(VoiceProfile)).all()
    assert len(voices) == 2 and all(Path(v.ref_audio_path).exists() for v in voices)
    # voices missing too
    for v in voices:
        env.delete(v)
    env.commit()
    lucas.install(env)
    assert len(env.exec(select(VoiceProfile)).all()) == 2
