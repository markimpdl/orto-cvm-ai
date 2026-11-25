"""YouTube upload defaults (user's rule, 2026-11-24): AI disclosure only with the avatar on screen; paid promotion,
automatic chapters, automatic places and "notify subscribers" always off."""

import re
from types import SimpleNamespace

from cvm.publish import browser_upload, platforms


def test_api_upload_never_notifies_and_declares_no_paid_promotion(monkeypatch, tmp_path):
    seen = {}

    class Req:
        def next_chunk(self):
            return None, {"id": "VID"}

    class Videos:
        def insert(self, **kw):
            seen.update(kw)
            return Req()

    yt = SimpleNamespace(videos=lambda: Videos(), thumbnails=lambda: None)
    import googleapiclient.discovery
    import googleapiclient.http

    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: yt)
    monkeypatch.setattr(googleapiclient.http, "MediaFileUpload", lambda *a, **k: None)
    monkeypatch.setattr(platforms, "youtube_credentials", lambda ch: None)
    monkeypatch.setattr(platforms, "caption_text", lambda *a, **k: "desc")
    monkeypatch.setattr(platforms, "category_for", lambda *a, **k: "20")
    render = SimpleNamespace(seo={"title": "T", "tags": []}, path=str(tmp_path / "v.mp4"), profile="youtube",
                             language="pt-BR", meta={"synthetic_disclosure": False})
    channel = SimpleNamespace(made_for_kids=False)
    assert platforms.upload_youtube(render, channel).endswith("VID")
    assert seen["notifySubscribers"] is False
    assert seen["body"]["status"]["containsSyntheticMedia"] is False
    assert seen["body"]["paidProductPlacementDetails"] == {"hasPaidProductPlacement": False}
    assert "paidProductPlacementDetails" in seen["part"]


class FakeBox:
    def __init__(self, checked):
        self.checked, self.clicks = checked, 0

    first = property(lambda self: self)

    def count(self):
        return 1

    def locator(self, _):
        return self

    def filter(self, has_text):
        return self

    def wait_for(self, **_):
        pass

    def scroll_into_view_if_needed(self, **_):
        pass

    def get_attribute(self, _):
        return "true" if self.checked else "false"

    def click(self, **_):
        self.clicks += 1
        self.checked = not self.checked


def test_untick_clicks_only_a_ticked_box():
    ticked, unticked = FakeBox(True), FakeBox(False)
    browser_upload._untick(SimpleNamespace(locator=lambda _: ticked), "x")
    browser_upload._untick(SimpleNamespace(locator=lambda _: unticked), "x")
    assert (ticked.checked, ticked.clicks) == (False, 1) and unticked.clicks == 0


def test_off_box_labels_match_studio_in_portuguese_and_english():
    labels = {
        "Capítulos automáticos": ["Permitir capítulos automáticos e momentos importantes", "Allow automatic chapters"],
        "Permitir a localização automática": ["Permitir a localização automática", "Allow automatic places"],
        "Publicar no feed de inscrições e enviar notificações aos inscritos": [
            "Publicar no feed de inscrições e enviar notificações aos inscritos",
            "Publish to subscriptions feed and notify subscribers"],
    }
    for name, rx in platforms.YOUTUBE_OFF_BOXES:
        assert all(re.search(rx, t, re.I) for t in labels[name]), name


def test_paid_promotion_no_label_matches_studio():
    for label in ("Não, meu vídeo não inclui promoção paga", "No, my video does not include paid promotion"):
        assert re.search(platforms.YOUTUBE_PAID_PROMO_NO, label, re.I)


class BlockedBox(FakeBox):
    """Studio's container intercepts the plain click; only a forced click gets through."""

    def click(self, force=False, **_):
        if not force:
            raise TimeoutError("checkbox-container intercepts pointer events")
        super().click()


def test_untick_clicks_through_an_intercepting_container():
    box = BlockedBox(True)
    browser_upload._untick(SimpleNamespace(locator=lambda _: box), "x")
    assert box.checked is False and box.clicks == 1


def test_paid_promotion_answers_no():
    no = FakeBox(False)
    page = SimpleNamespace(locator=lambda _: no)
    browser_upload._paid_promotion_no(page)
    assert no.checked is True
