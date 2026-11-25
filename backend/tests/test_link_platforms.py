"""Phase 6: links from Twitch, Kick, TikTok and Instagram (cvm/lives/platforms.py, ingest.info_from). No network."""
import json
import subprocess

import pytest

from cvm import media_links
from cvm.lives import ingest, platforms, runs


@pytest.mark.parametrize("url,platform,vid,canonical", [
    ("https://youtu.be/abcdefghijk?t=3", "youtube", "abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk"),
    ("https://www.twitch.tv/videos/2045678912?t=1h2m", "twitch", "v2045678912", "https://www.twitch.tv/videos/2045678912"),
    ("https://clips.twitch.tv/FunnyCleverClipName-AbC12", "twitch", "FunnyCleverClipName-AbC12",
     "https://clips.twitch.tv/FunnyCleverClipName-AbC12"),
    ("https://www.twitch.tv/gaules/clip/BraveTiny-Xy_9?filter=clips", "twitch", "BraveTiny-Xy_9",
     "https://clips.twitch.tv/BraveTiny-Xy_9"),
    ("https://kick.com/xqc/videos/0a1b2c3d-1111-2222-3333-444455556666", "kick", "0a1b2c3d-1111-2222-3333-444455556666",
     "https://kick.com/xqc/videos/0a1b2c3d-1111-2222-3333-444455556666"),
    ("https://kick.com/xqc/clips/clip_01HABCDEF", "kick", "clip_01HABCDEF", "https://kick.com/xqc/clips/clip_01HABCDEF"),
    ("https://www.tiktok.com/@fulano.br/video/7301234567890123456?is_from_webapp=1", "tiktok", "7301234567890123456",
     "https://www.tiktok.com/@fulano.br/video/7301234567890123456"),
    ("https://vm.tiktok.com/ZMabc123/", "tiktok", None, "https://vm.tiktok.com/ZMabc123/"),
    ("https://www.instagram.com/reel/C1a2B3c4D5e/?igsh=xyz", "instagram", "C1a2B3c4D5e",
     "https://www.instagram.com/reel/C1a2B3c4D5e/"),
    ("https://instagram.com/fulano/p/C9zz_yy-XX/", "instagram", "C9zz_yy-XX", "https://www.instagram.com/p/C9zz_yy-XX/"),
])
def test_detect_per_platform(url, platform, vid, canonical):
    d = platforms.detect(url)
    assert (d["platform"], d["id"], d["url"], d["original_url"]) == (platform, vid, canonical, url)


@pytest.mark.parametrize("url,words", [
    ("https://www.twitch.tv/gaules", "canal da Twitch"),
    ("https://kick.com/xqc", "canal do Kick"),
    ("https://www.tiktok.com/@fulano", "perfil do TikTok"),
    ("https://www.tiktok.com/@fulano/photo/7301234567890123456", "fotos"),
    ("https://www.instagram.com/stories/fulano/123/", "login"),
    ("https://www.instagram.com/fulano/", "perfil do Instagram"),
    ("https://vimeo.com/1", "não reconhecido"),
])
def test_detect_refuses_what_is_not_one_public_video(url, words):
    with pytest.raises(ValueError, match=words):
        platforms.detect(url)


def test_normalize_keeps_original_url_and_uid():
    out = media_links.normalize([{"url": "https://www.twitch.tv/videos/123?t=5", "cut": "highlights"},
                                 "https://vt.tiktok.com/ZSabc/"])
    assert out[0] == {"url": "https://www.twitch.tv/videos/123", "original_url": "https://www.twitch.tv/videos/123?t=5",
                      "platform": "twitch", "video_id": "twitch:v123", "cut": "highlights", "keep_audio": False}
    assert out[1]["platform"] == "tiktok" and out[1]["video_id"] is None
    with pytest.raises(ValueError, match="Twitch"):
        media_links.normalize(["https://twitch.tv/gaules"])


# trimmed yt-dlp --dump-json output per site
INFOS = {
    "twitch_vod": ("https://www.twitch.tv/videos/2045678912", {
        "id": "v2045678912", "title": "GTA RP dia 40", "duration": 10800, "uploader": "Gaules", "uploader_id": "gaules",
        "extractor_key": "TwitchVod", "webpage_url": "https://www.twitch.tv/videos/2045678912", "live_status": "was_live"}),
    "twitch_clip": ("https://clips.twitch.tv/BraveTiny", {
        "id": "987654", "display_id": "BraveTiny", "title": "que isso", "duration": 28.5, "creator": "x",
        "uploader": "Gaules", "uploader_id": "gaules", "extractor_key": "TwitchClips",
        "webpage_url": "https://clips.twitch.tv/BraveTiny"}),
    "kick_vod": ("https://kick.com/xqc/videos/0a1b2c3d-1111-2222-3333-444455556666", {
        "id": "0a1b2c3d-1111-2222-3333-444455556666", "title": "stream", "duration": 7200, "uploader": "xQc",
        "uploader_id": "xqc", "extractor_key": "KickVOD"}),
    "tiktok": ("https://vm.tiktok.com/ZMabc123/", {
        "id": "7301234567890123456", "title": "", "description": "o melhor drift\ndo ano #gta", "duration": 31,
        "uploader": "fulano.br", "channel_id": "MS4wLjABAAAA", "extractor_key": "TikTok",
        "webpage_url": "https://www.tiktok.com/@fulano.br/video/7301234567890123456"}),
    "instagram": ("https://www.instagram.com/reel/C1a2B3c4D5e/?igsh=xyz", {
        "id": "C1a2B3c4D5e", "title": "Video by fulano", "duration": 15.2, "uploader": "Fulano", "channel": "fulano",
        "uploader_id": "123456", "extractor_key": "Instagram", "webpage_url": "https://www.instagram.com/reel/C1a2B3c4D5e/"}),
}


@pytest.mark.parametrize("name,uid,url", [
    ("twitch_vod", "twitch:v2045678912", "https://www.twitch.tv/videos/2045678912"),
    ("twitch_clip", "twitch:987654", "https://clips.twitch.tv/BraveTiny"),
    ("kick_vod", "kick:0a1b2c3d-1111-2222-3333-444455556666",
     "https://kick.com/xqc/videos/0a1b2c3d-1111-2222-3333-444455556666"),
    ("tiktok", "tiktok:7301234567890123456", "https://www.tiktok.com/@fulano.br/video/7301234567890123456"),
    ("instagram", "instagram:C1a2B3c4D5e", "https://www.instagram.com/reel/C1a2B3c4D5e/"),
])
def test_info_from_per_platform(name, uid, url):
    pasted, info = INFOS[name]
    out = ingest.info_from(info, pasted)
    assert out["video_id"] == uid and out["url"] == url and out["original_url"] == pasted
    assert out["platform"] == name.split("_")[0] and out["uploader"] and out["title"]
    assert out["duration"] == float(info["duration"])
    if name == "tiktok":
        assert out["title"] == "o melhor drift"  # empty title: the first line of the caption


def test_probe_refuses_lives_on_any_site():
    for url, info in (("https://www.twitch.tv/videos/1", {"id": "v1", "is_live": True, "extractor_key": "TwitchVod"}),
                      ("https://kick.com/a/videos/0a1b2c3d-1111-2222-3333-444455556666",
                       {"id": "x", "live_status": "is_live", "extractor_key": "KickVOD"})):
        run = lambda cmd, info=info, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps(info), "")  # noqa: E731
        with pytest.raises(ingest.NotReady, match="no ar"):
            ingest.probe(url, run=run)


@pytest.mark.parametrize("url,stderr,words", [
    ("https://www.instagram.com/p/C1/", "ERROR: [Instagram] C1: Requested content is not available, rate-limit reached "
     "or login required. Use --cookies", "Instagram: o site pede login"),
    ("https://www.tiktok.com/@a/video/1", "ERROR: [TikTok] 1: This video is private", "TikTok: o vídeo é privado"),
    ("https://www.twitch.tv/videos/9", "ERROR: [twitch:vod] 9: Video 9 does not exist", "Twitch: não achei o vídeo"),
    ("https://www.twitch.tv/videos/9", "ERROR: [twitch:vod] This video is only available to subscribers",
     "só para inscritos"),
    ("https://kick.com/a/clips/clip_1", "ERROR: [kick:clips] HTTP Error 404: Not Found", "experimental"),
    ("https://youtu.be/abcdefghijk", "ERROR: Sign in to confirm your age", "restrição de idade"),
])
def test_site_errors_read_plainly(url, stderr, words):
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", stderr)  # noqa: E731
    with pytest.raises(RuntimeError, match=words):
        ingest.probe(url, run=run)


def test_youtube_bot_check_still_waits():
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "Sign in to confirm you’re not a bot")  # noqa: E731
    with pytest.raises(ingest.NotReady):
        ingest.probe("https://youtu.be/abcdefghijk", run=run)


def test_download_names_files_safely(tmp_path):
    live = ingest.info_from(INFOS["twitch_vod"][1], INFOS["twitch_vod"][0])

    def run(cmd, **kw):
        (tmp_path / "live_twitch_v2045678912.mp4").write_bytes(b"v")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    assert ingest.download(live, tmp_path, run=run).name == "live_twitch_v2045678912.mp4"


def test_runs_key_other_sites():
    assert runs.video_id_of("https://youtu.be/abcdefghijk") == "abcdefghijk"
    assert runs.video_id_of("https://www.twitch.tv/videos/55") == "twitch:v55"
    assert runs.video_id_of("https://vm.tiktok.com/ZMabc123/") == "tiktok:ZMabc123"
    assert runs.video_id_of("https://www.twitch.tv/gaules") is None


def test_own_account_on_other_sites():
    from cvm.models import Channel

    ch = Channel(name="c", language="pt-BR", youtube_channel_id="UCme", prefs={"accounts": {"twitch": "@Studio"}})
    assert media_links.is_own(ch, {"platform": "youtube", "channel_id": "UCme"})
    assert media_links.is_own(ch, {"platform": "twitch", "channel_id": "studio", "uploader": "Studio"})
    assert not media_links.is_own(ch, {"platform": "twitch", "channel_id": "gaules", "uploader": "Gaules"})
    assert not media_links.is_own(ch, {"platform": "kick", "channel_id": "studio"})
