"""Install wizard: first channel with publish defaults, brand upload and bumper preview."""
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from cvm.api import setup as setup_api
from cvm.config import settings
from cvm.db import get_session
from cvm.models import Avatar, Channel, Render, Setting
from cvm.setup import channel as setup_channel


@pytest.fixture
def s(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    monkeypatch.setattr(setup_channel, "engine", e)
    with Session(e) as session:
        yield session


@pytest.fixture
def client(s):
    app = FastAPI()
    app.include_router(setup_api.router)
    app.dependency_overrides[get_session] = lambda: s
    return TestClient(app)


def _png(w, h, colour=(200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "JPEG")
    return buf.getvalue()


def _channel(s, **prefs) -> Channel:
    return setup_channel.create(s, {"name": "Meu Canal", "language": "pt-BR", "brief": "tecnologia", **prefs})


# ---------- channel ----------

def test_create_channel_publish_defaults(s):
    c = _channel(s)
    assert c.id and c.language == "pt-BR" and c.status == "active" and c.platform == "youtube"
    assert c.made_for_kids is False
    assert c.prefs["publish_defaults"] == {"notify_subscribers": False, "paid_promotion": False,
                                           "auto_chapters": False, "auto_places": False, "synthetic": "auto"}
    assert c.prefs["bumper_mode"] == "short"
    off = _channel(s, bumper=False, publish={"notify_subscribers": True, "junk": 1}, made_for_kids=True)
    assert off.prefs["bumper_mode"] == "none" and off.made_for_kids is True
    assert off.prefs["publish_defaults"]["notify_subscribers"] is True and "junk" not in off.prefs["publish_defaults"]


def test_create_channel_uses_lucas_when_present(s):
    lucas = Avatar(name="Lucas", status="ready")
    s.add(lucas)
    s.commit()
    assert _channel(s).avatar_id == lucas.id
    assert _channel(s, avatar_id=None).avatar_id is None


def test_create_channel_rejects_arabic(s, client):
    with pytest.raises(ValueError):
        _channel(s, language="ar-AE")
    r = client.post("/setup/channel", json={"name": "X", "language": "ar-AE"})
    assert r.status_code == 422 and "pt-BR" in r.json()["detail"]
    r = client.post("/setup/channel", json={"name": "X", "language": "en-US"})
    assert r.status_code == 200 and r.json()["language"] == "en-US"


# ---------- brand ----------

def test_brand_upload_writes_pngs(s, client):
    c = _channel(s)
    r = client.post(f"/setup/channel/{c.id}/brand",
                    files={"logo": ("l.jpg", _png(800, 600), "image/jpeg"),
                           "banner": ("b.jpg", _png(1000, 1000), "image/jpeg")})
    assert r.status_code == 200, r.text
    brand = settings.data_dir / "channels" / str(c.id) / "brand"
    logo, banner = Image.open(brand / "logo.png"), Image.open(brand / "banner.png")
    assert logo.format == "PNG" and logo.size == (600, 600)
    assert banner.format == "PNG" and abs(banner.width / banner.height - 16 / 9) < 0.01


def _png_header(w, h) -> bytes:
    """A few bytes declaring a w × h PNG (no pixel data): what a decompression bomb looks like before decoding."""
    import struct
    import zlib

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def test_brand_upload_refuses_huge_pictures(s, client):
    c = _channel(s)
    for w, h in ((8000, 6000), (20000, 20000)):  # over our 40 MP cap; over Pillow's bomb limit
        r = client.post(f"/setup/channel/{c.id}/brand", files={"logo": ("l.png", _png_header(w, h), "image/png")})
        assert r.status_code == 422 and "megapixels" in r.json()["detail"], (w, h, r.text)


def test_brand_upload_refuses_small_logo_and_non_images(s, client):
    c = _channel(s)
    r = client.post(f"/setup/channel/{c.id}/brand", files={"logo": ("l.png", _png(300, 300), "image/png")})
    assert r.status_code == 422 and "400" in r.json()["detail"]
    r = client.post(f"/setup/channel/{c.id}/brand", files={"logo": ("l.png", b"not an image", "image/png")})
    assert r.status_code == 422
    assert not (settings.data_dir / "channels" / str(c.id) / "brand" / "logo.png").exists()
    assert client.post("/setup/channel/999/brand", files={"logo": ("l.png", _png(500, 500), "image/png")}).status_code == 404


# ---------- bumper ----------

def test_parse_params_validates():
    assert setup_channel.parse_params("oops") == setup_channel.DEFAULT_PARAMS
    p = setup_channel.parse_params('Here: {"variant": "glitch", "sound": "laser", "colours": {"ring": "#FF0000", '
                                   '"glow": "red", "x": "#000000"}, "extra": 1}')
    assert p == {"variant": "glitch", "sound": "mix", "colours": {"ring": "#FF0000"}}


def test_bumper_prompt_bad_json_uses_defaults(s, monkeypatch):
    c = _channel(s)
    (settings.data_dir / "channels" / str(c.id) / "brand").mkdir(parents=True)
    Image.new("RGB", (500, 500)).save(settings.data_dir / "channels" / str(c.id) / "brand" / "logo.png")
    from cvm.render import bumper_brand
    from cvm.scripting import agents

    calls = []
    monkeypatch.setattr(agents, "run_light", lambda prompt, step, **k: calls.append(k) or {"result": "oops"})
    rendered = {}

    def fake_render(logo, banner, W, H, fps, out, D, colours=None, music=None, variant="pop", sound="mix"):
        rendered.update(colours=colours, variant=variant, sound=sound)
        out.write_bytes(b"mp4")
        return out
    monkeypatch.setattr(bumper_brand, "render_short", fake_render)
    s.add(Setting(key=f"bumper_preview:{c.id}", value={"state": "running", "prompt": "algo bem tech"}))
    s.commit()
    setup_channel.make_bumper(c.id)
    s.expire_all()
    st = s.get(Setting, f"bumper_preview:{c.id}").value
    assert st["state"] == "ready" and st["params"] == setup_channel.DEFAULT_PARAMS, st
    assert calls and calls[0]["mcp"] == "empty" and "Bash" in calls[0]["disallowed_tools"]
    assert rendered == {"colours": {}, "variant": "pop", "sound": "mix"}


def test_bumper_start_is_detached_and_single(s, client, monkeypatch):
    c = _channel(s)
    assert client.post(f"/setup/channel/{c.id}/bumper", json={}).status_code == 422  # no logo yet
    setup_channel.brand_dir(c.id).mkdir(parents=True)
    Image.new("RGB", (500, 500)).save(setup_channel.brand_dir(c.id) / "logo.png")
    started = []
    monkeypatch.setattr(setup_channel.subprocess, "Popen",
                        lambda cmd, **k: started.append((cmd, k)) or SimpleNamespace(pid=4242))
    monkeypatch.setattr(setup_channel, "_created", lambda pid: 1.0)
    monkeypatch.setattr(setup_channel, "_process", lambda pid, created: object() if created == 1.0 else None)
    r = client.post(f"/setup/channel/{c.id}/bumper", json={"prompt": "neon"})
    assert r.status_code == 200 and r.json()["state"] == "running"
    assert started[0][1]["start_new_session"] is True and str(c.id) in started[0][0]
    r2 = client.post(f"/setup/channel/{c.id}/bumper", json={})
    assert r2.json()["state"] == "running" and len(started) == 1
    assert client.get(f"/setup/channel/{c.id}/bumper").json()["state"] == "running"
    monkeypatch.setattr(setup_channel, "_process", lambda pid, created: None)  # the process died without a word
    assert client.get(f"/setup/channel/{c.id}/bumper").json()["state"] == "failed"
    assert client.get(f"/setup/channel/{c.id}/bumper.mp4").status_code == 404


def _with_logo(c):
    setup_channel.brand_dir(c.id).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 500)).save(setup_channel.brand_dir(c.id) / "logo.png")


def test_process_probe_checks_start_time(monkeypatch):
    import os

    import psutil

    monkeypatch.setattr(os, "kill", lambda *a: pytest.fail("os.kill used as a probe"))
    me = os.getpid()
    created = psutil.Process(me).create_time()
    assert setup_channel._created(me) == created
    assert setup_channel._process(me, created) is not None
    assert setup_channel._process(me, created - 5) is None  # same pid, another process: reused pid
    assert setup_channel._process(me, None) is None and setup_channel._process(None, created) is None


def test_reused_pid_is_failed_and_never_killed(s, monkeypatch):
    import os
    import time

    import psutil

    c = _channel(s)
    killed = []
    monkeypatch.setattr(setup_channel, "_kill", lambda proc: killed.append(proc))
    me = os.getpid()  # alive, but started at another time than the one recorded: not our child
    s.add(Setting(key=f"bumper_preview:{c.id}",
                  value={"state": "running", "pid": me, "pid_created": psutil.Process(me).create_time() - 100,
                         "started": time.time() - 11 * 60}))
    s.commit()
    assert setup_channel.status(s, c.id)["state"] == "failed" and killed == []


def test_running_without_pid_expires(s):
    import time

    c = _channel(s)
    key = f"bumper_preview:{c.id}"
    s.add(Setting(key=key, value={"state": "running", "started": time.time()}))
    s.commit()
    assert setup_channel.status(s, c.id)["state"] == "running"  # the API is about to write the pid
    s.get(Setting, key).value = {"state": "running", "started": time.time() - 60}
    s.commit()
    assert setup_channel.status(s, c.id)["state"] == "failed"
    s.get(Setting, key).value = {"state": "running"}  # a row from before `started` existed
    s.commit()
    assert setup_channel.status(s, c.id)["state"] == "failed"


def test_stuck_run_is_killed_after_deadline(s, monkeypatch):
    import time

    c = _channel(s)
    killed = []
    ours = object()
    monkeypatch.setattr(setup_channel, "_process", lambda pid, created: ours if (pid, created) == (77, 5.0) else None)
    monkeypatch.setattr(setup_channel, "_kill", lambda proc: killed.append(proc))
    key = f"bumper_preview:{c.id}"
    s.add(Setting(key=key, value={"state": "running", "pid": 77, "pid_created": 5.0, "started": time.time() - 60}))
    s.commit()
    assert setup_channel.status(s, c.id)["state"] == "running" and not killed
    s.get(Setting, key).value = {"state": "running", "pid": 77, "pid_created": 5.0, "started": time.time() - 11 * 60}
    s.commit()
    st = setup_channel.status(s, c.id)
    assert st["state"] == "failed" and killed == [ours] and "pid" not in st and "pid_created" not in st


def test_fast_child_result_is_not_overwritten_by_pid_write(s, monkeypatch):
    """The child finishes before the parent records its pid: the parent must not put "running" back."""
    from cvm.render import bumper_brand

    c = _channel(s)
    _with_logo(c)

    def fake_render(logo, banner, W, H, fps, out, D, **kw):
        out.write_bytes(b"mp4")
        return out
    monkeypatch.setattr(bumper_brand, "render_short", fake_render)

    def popen(cmd, **k):
        setup_channel.make_bumper(c.id)  # runs to the end before Popen returns
        return SimpleNamespace(pid=99999999)
    monkeypatch.setattr(setup_channel.subprocess, "Popen", popen)
    st = setup_channel.start_bumper(s, c, None)
    assert st["state"] == "ready" and st["params"] == setup_channel.DEFAULT_PARAMS
    assert setup_channel.status(s, c.id)["state"] == "ready"


def test_child_does_not_overwrite_a_newer_run(s, monkeypatch):
    from cvm.render import bumper_brand

    c = _channel(s)
    _with_logo(c)
    key = f"bumper_preview:{c.id}"
    s.add(Setting(key=key, value={"state": "running", "run": "old", "pid": 1}))
    s.commit()

    def fake_render(logo, banner, W, H, fps, out, D, **kw):  # meanwhile a newer run took over
        with Session(setup_channel.engine) as other:
            other.get(Setting, key).value = {"state": "running", "run": "new", "pid": 2}
            other.commit()
        out.write_bytes(b"mp4")
        return out
    monkeypatch.setattr(bumper_brand, "render_short", fake_render)
    setup_channel.make_bumper(c.id)
    s.expire_all()
    assert s.get(Setting, key).value["run"] == "new" and s.get(Setting, key).value["state"] == "running"


def test_bumper_approve_saves_params(s, client):
    c = _channel(s)
    assert client.post(f"/setup/channel/{c.id}/bumper/approve").status_code == 409  # nothing ready yet
    params = {"variant": "zoom", "sound": "impact", "colours": {"glow": "#00FF00"}}
    s.add(Setting(key=f"bumper_preview:{c.id}", value={"state": "ready", "params": params}))
    s.commit()
    preview = setup_channel.preview_path(c.id)
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"mp4")
    r = client.get(f"/setup/channel/{c.id}/bumper")
    assert r.json()["url"].startswith(f"/setup/channel/{c.id}/bumper.mp4")
    assert client.get(f"/setup/channel/{c.id}/bumper.mp4").content == b"mp4"
    assert client.post(f"/setup/channel/{c.id}/bumper/approve").status_code == 200
    s.expire_all()
    assert s.get(Channel, c.id).prefs["bumper_params"] == params


def test_short_bumper_uses_approved_params(s, monkeypatch):
    from cvm.render import bumper, bumper_brand

    c = _channel(s)
    brand = settings.data_dir / "channels" / str(c.id) / "brand"
    brand.mkdir(parents=True)
    Image.new("RGB", (500, 500)).save(brand / "logo.png")
    seen = []

    def fake_render(logo, banner, W, H, fps, out, D, colours=None, music=None, **kw):
        seen.append((colours, kw))
        out.write_bytes(b"x")
        return out
    monkeypatch.setattr(bumper_brand, "render_short", fake_render)
    bumper.short_bumper(c, 320, 180, 10)
    assert seen[-1] == ({}, {})  # no approved params: called exactly as before
    c.prefs = {**c.prefs, "bumper_params": {"variant": "slide", "sound": "none", "colours": {"ring": "#123456"}}}
    bumper.short_bumper(c, 320, 180, 10)
    assert seen[-1] == ({"ring": "#123456"}, {"variant": "slide", "sound": "none"})


# ---------- render_short ----------

# today's argv (captured before the variant/sound params existed), the encoder and the output path left out
OLD_ARGV = ['-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '160x90', '-r', '10', '-i', '-', '-f',
            'lavfi', '-i', 'anoisesrc=d=0.7:c=pink:a=0.6', '-f', 'lavfi', '-i',
            "aevalsrc='0.9*sin(2*PI*(45+60*exp(-8*t))*t)*exp(-3.2*t)':d=1.4", '-f', 'lavfi', '-i',
            "aevalsrc='0.25*(sin(2*PI*1318*t)+sin(2*PI*1760*t)+0.6*sin(2*PI*2637*t))*exp(-3*t)':d=1.6",
            '-filter_complex',
            ('[1:a]highpass=f=400,lowpass=f=5000,afade=t=in:d=0.2,afade=t=out:st=0.2:d=0.5,volume=0.3[w];'
            '[2:a]adelay=150|150,volume=0.9[b];[3:a]adelay=600|600,volume=0.5[s];[w][b][s]amix=inputs=3:'
            'duration=longest:normalize=0,apad,atrim=0:1.00,afade=t=out:st=0.70:d=0.3,'
            'aformat=sample_rates=48000:channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11[a]'),
            '-map', '0:v', '-map', '[a]', '-t', '1.00', '-c:v', 'H264', '-b:v', '12M', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', 'OUT']


def _capture(monkeypatch, tmp_path, **kw):
    import hashlib

    from cvm.render import bumper_brand

    logo = tmp_path / "logo.png"
    Image.new("RGB", (64, 64), (200, 30, 30)).save(logo)
    got = {}

    class P:
        def __init__(self, cmd, stdin=None):
            got["cmd"], self.h, self.stdin = cmd, hashlib.sha1(), self

        def write(self, b):
            self.h.update(b)

        def close(self):
            got["frames"] = self.h.hexdigest()

        def wait(self):
            return 0
    monkeypatch.setattr(bumper_brand.subprocess, "Popen", P)
    out = tmp_path / "o.mp4"
    bumper_brand.render_short(logo, None, 160, 90, 10, out, 1.0, **kw)
    argv = [("H264" if a == bumper_brand.H264 else "OUT" if a == str(out) else a) for a in got["cmd"][1:]]
    return argv, got["frames"]


def test_render_short_default_unchanged(monkeypatch, tmp_path):
    argv, frames = _capture(monkeypatch, tmp_path)
    assert argv == OLD_ARGV
    assert _capture(monkeypatch, tmp_path, variant="pop", sound="mix") == (argv, frames)
    for variant in ("slide", "glitch", "zoom"):
        v_argv, v_frames = _capture(monkeypatch, tmp_path, variant=variant)
        assert v_argv == argv and v_frames != frames, variant
    for sound in ("whoosh", "impact", "shimmer", "none"):
        assert _capture(monkeypatch, tmp_path, sound=sound)[0] != argv, sound


# ---------- publishing ----------

class _Rep:
    def __init__(self):
        self.data, self.names = {"steps": []}, []

    def state(self, st):
        pass

    def step(self, name, fn):
        self.names.append(name)
        fn()
        return True


def _browser_steps(monkeypatch, channel):
    from cvm.publish import browser_upload as bu

    unticked = []
    monkeypatch.setattr(bu, "_set_files", lambda *a, **k: None)
    monkeypatch.setattr(bu, "_replace_text", lambda *a, **k: None)
    monkeypatch.setattr(bu, "_untick", lambda page, rx, timeout=8000: unticked.append(rx))
    monkeypatch.setattr(bu, "_paid_promotion_no", lambda page, timeout=8000: unticked.append("paid-no"))
    render = Render(project_id=1, language="pt-BR", profile="youtube", path="/x.mp4", seo={"title": "t"},
                    meta={"made_for_kids": False, "_channel": channel, "_style": "explainer", "_ai": False})
    rep = _Rep()
    bu.youtube(MagicMock(), rep, render, __import__("pathlib").Path("/x.mp4"))
    return unticked, rep.names


def test_upload_without_publish_defaults_keeps_todays_rules(monkeypatch):
    from cvm.publish.platforms import YOUTUBE_OFF_BOXES

    owner = Channel(id=1, name="Studio", platform="youtube", prefs={})
    unticked, names = _browser_steps(monkeypatch, owner)
    assert unticked == ["paid-no"] + [rx for _, rx in YOUTUBE_OFF_BOXES]
    assert "promoção paga: Não" in names and not any("deixado como está" in n for n in names)
    body, notify = _data_api(monkeypatch, owner)
    assert notify is False and body["paidProductPlacementDetails"] == {"hasPaidProductPlacement": False}
    assert body["status"]["containsSyntheticMedia"] is True  # from render.meta.synthetic_disclosure, as today


def test_upload_uses_channel_publish_defaults(monkeypatch):
    from cvm.publish.platforms import YOUTUBE_OFF_BOXES

    pd = {"notify_subscribers": True, "paid_promotion": True, "auto_chapters": False, "auto_places": False,
          "synthetic": "no"}
    ch = Channel(id=2, name="Novo", platform="youtube", prefs={"publish_defaults": pd})
    unticked, names = _browser_steps(monkeypatch, ch)
    assert "deixado como está no Studio: " + YOUTUBE_OFF_BOXES[2][0] in names
    notify_rx = YOUTUBE_OFF_BOXES[2][1]
    assert notify_rx not in unticked and YOUTUBE_OFF_BOXES[0][1] in unticked and YOUTUBE_OFF_BOXES[1][1] in unticked
    assert "paid-no" not in unticked
    body, notify = _data_api(monkeypatch, ch)
    assert notify is True and body["paidProductPlacementDetails"] == {"hasPaidProductPlacement": True}
    assert body["status"]["containsSyntheticMedia"] is False


def test_manual_kit_checklist_follows_channel():
    from cvm.api.publish import YOUTUBE_OFF, youtube_off

    assert youtube_off(Channel(name="Studio", platform="youtube", prefs={})) == YOUTUBE_OFF
    own = youtube_off(Channel(name="n", platform="youtube", prefs={"publish_defaults": {"auto_chapters": True}}))
    assert len(own) == len(YOUTUBE_OFF) - 1 and not any("Capítulos" in x for x in own)


def test_synthetic_setting_overrides_avatar_check():
    from cvm.publish.platforms import youtube_rules

    assert youtube_rules(None)["synthetic"] == "auto"
    assert youtube_rules(Channel(name="a", platform="youtube", prefs={"publish_defaults": {"synthetic": "yes"}}))[
        "synthetic"] == "yes"
    assert youtube_rules(Channel(name="a", platform="youtube", prefs={"publish_defaults": {"synthetic": "?"}}))[
        "synthetic"] == "auto"


def _data_api(monkeypatch, channel):
    import googleapiclient.discovery
    import googleapiclient.http

    from cvm.publish import platforms

    got = {}

    class Req:
        def next_chunk(self):
            return None, {"id": "vid"}

    yt = MagicMock()

    def insert(**kw):
        got.update(kw)
        return Req()
    yt.videos.return_value.insert.side_effect = insert
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: yt)
    monkeypatch.setattr(googleapiclient.http, "MediaFileUpload", lambda *a, **k: None)
    monkeypatch.setattr(platforms, "youtube_credentials", lambda c: None)
    monkeypatch.setattr(platforms, "category_for", lambda r, c: "22")
    monkeypatch.setattr(platforms, "caption_text", lambda *a, **k: "desc")
    render = Render(project_id=1, language="pt-BR", profile="youtube", path="/x.mp4", seo={"title": "t"},
                    meta={"synthetic_disclosure": True})
    platforms.upload_youtube(render, channel)
    return got["body"], got["notifySubscribers"]
