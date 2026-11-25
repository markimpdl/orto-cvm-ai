"""Task 6 of the editorial format: the card/segment renderer (cards.html in headless Chromium + ffmpeg segments)."""
import json
import subprocess
from pathlib import Path

import pytest

from cvm.config import settings
from cvm.render.editorial import cards
from cvm.render.editorial.timeline import EditorialError

BRAND_NO_LOGO = {"logo": None, "name": "Canal Teste", "colours": {}}


def test_card_spec_without_logo():
    spec = cards.page_spec({"tipo": "numero", "valor": "42", "rotulo": "algo", "secao": ["01", "O *teste*"],
                            "secao_nova": True, "start": 0, "end": 2}, BRAND_NO_LOGO, "pt-BR")
    assert spec["logo"] is None
    assert spec["name"] == "Canal Teste"
    assert "Canal Teste" in json.dumps(spec)  # the brand comes from the channel, never a built-in name
    assert spec["body"] == "card" and spec["valor"] == "42" and spec["secao_nova"] is True
    vin = cards.page_spec({"tipo": "vinheta", "start": 0, "end": 2}, BRAND_NO_LOGO, "pt-BR")
    assert vin["body"] == "vinheta" and vin["secao"] is None and vin["logo"] is None
    # the page itself carries no channel defaults (channel names: test_owner_rules.test_no_personal_terms_in_code)
    html = cards.TEMPLATE.read_text()
    assert "SEM HYPE" not in html.upper() and "Rockstar" not in html


def test_card_spec_with_logo_and_colours(tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    spec = cards.page_spec({"tipo": "pergunta", "texto": "Real?"},
                           {"logo": logo, "name": "X", "colours": {"accent": "#00AAFF"}}, "pt-BR")
    assert spec["logo"] == logo.resolve().as_uri()
    assert spec["accent"] == "#00AAFF"


@pytest.mark.parametrize("colours, expected", [
    ({"ring": "#1A1A1A"}, None),  # dark ring: unreadable on the card → page default
    ({"ring": "#00E5FF"}, "#00E5FF"),  # bright ring is used
    ({"accent": "#222", "ring": "#FFF"}, "#FFF"),  # dark accent skipped, 3-digit ring accepted
    ({"accent": "yellow"}, None),  # not a hex colour
    ({"accent": "#12345G"}, None),
    ({}, None),
])
def test_readable_accent(colours, expected):
    assert cards._readable_accent(colours) == expected
    assert cards.page_spec({"tipo": "pergunta", "texto": "x"}, {"name": "X", "colours": colours}, "pt-BR")["accent"] == expected


def test_slow_error_without_scene_start(tmp_path):
    s = {"tipo": "video", "start": None, "media": {"kind": "video", "file": "v.mp4", "start": 0, "window_end": 1.0}}
    with pytest.raises(EditorialError, match="0.0s"):
        cards.video_segment(s, 3.0, tmp_path / "o.ffconcat", tmp_path / "o.mp4")
    with pytest.raises(EditorialError):
        cards.video_segment({k: v for k, v in s.items() if k != "start"}, 3.0, tmp_path / "o.ffconcat", tmp_path / "o.mp4")


def test_footer_language(tmp_path):
    media = {"kind": "photo", "file": str(tmp_path / "a.jpg"), "start": 0, "window_end": None, "credit": "Foto: Fulano",
             "source": "wikimedia", "license": "CC BY", "own": False, "judged": True}
    s = {"tipo": "foto", "media": media, "start": 0, "end": 2}
    pt = cards.page_spec(s, BRAND_NO_LOGO, "pt-BR")
    en = cards.page_spec(s, BRAND_NO_LOGO, "en-US")
    assert pt["labels"] == {"fonte": "FONTE", "traducao": "TRADUÇÃO LIVRE"}
    assert en["labels"] == {"fonte": "SOURCE", "traducao": "FREE TRANSLATION"}
    assert pt["fonte"] == "Foto: Fulano" and pt["body"] == "overlay"
    own = cards.page_spec({**s, "media": {**media, "own": True, "credit": None}}, BRAND_NO_LOGO, "pt-BR")
    assert own["fonte"] is None


def test_proof_spec_from_plan(tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"x")
    s = {"tipo": "prova", "img": str(img), "size": [800, 400],
         "boxes": [{"rects": [[10, 20, 300, 40]]}, {"rects": [[10, 80, 200, 40]]}],
         "prova": {"url": "https://x.com/a", "frase": "f", "traducao": "tradução"}, "_hl": [0.5],
         "fonte": "x.com", "start": 0, "end": 3}
    spec = cards.page_spec(s, BRAND_NO_LOGO, "pt-BR")
    assert spec["img"] == img.resolve().as_uri() and spec["size"] == [800, 400]
    assert [b["t"] for b in spec["boxes"]] == [0.5, 0.85]
    assert spec["traducao"] == "tradução"


def test_slow_factor_capped(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cards, "_run", lambda cmd: calls.append([str(c) for c in cmd]))
    s = {"tipo": "video", "mov": "in", "media": {"kind": "video", "file": str(tmp_path / "v.mp4"), "start": 10.0,
                                                 "window_end": 12.0}}
    with pytest.raises(EditorialError):
        cards.video_segment(s, 3.5, tmp_path / "o.ffconcat", tmp_path / "o.mp4")  # 3.5 / 2.0 = 1.75 > 1.7
    assert not calls
    assert cards.video_segment(s, 3.0, tmp_path / "o.ffconcat", tmp_path / "o.mp4") == pytest.approx(1.5)
    assert cards.video_segment(s, 1.0, tmp_path / "o.ffconcat", tmp_path / "o.mp4") == 1.0
    cmd = " ".join(calls[0])
    assert calls[0][0] == settings.ffmpeg_bin
    assert "setpts=1.5*(PTS-STARTPTS)" in cmd and "-ss 10.0" in cmd
    assert "eq=saturation=0.92:contrast=1.04" in cmd


def _probe_duration(p: Path) -> float:
    r = subprocess.run([settings.ffprobe_bin, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def _mid_mean(p: Path, dur: float, out: Path) -> float:
    from PIL import Image, ImageStat

    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-ss", f"{dur / 2:.3f}", "-i", str(p), "-frames:v", "1", str(out)],
                   check=True)
    return sum(ImageStat.Stat(Image.open(out).convert("L")).mean)


@pytest.mark.skipif(not Path(settings.ffmpeg_bin).exists(), reason="ffmpeg-full not installed")
def test_render_one_card_and_one_photo(tmp_path):
    from PIL import Image
    from playwright.sync_api import sync_playwright

    photo = tmp_path / "photo.jpg"
    img = Image.new("RGB", (1600, 1000), (40, 120, 200))
    for x in range(0, 1600, 100):
        for y in range(0, 1000, 100):
            img.paste((230, 180, 40), (x, y, x + 50, y + 50))
    img.save(photo)
    dur = 1.5
    card = {"tipo": "numero", "valor": "42", "rotulo": "pessoas na fila", "secao": ["01", "O *teste*"], "secao_nova": True,
            "start": 0, "end": dur}
    foto = {"tipo": "foto", "mov": "pan-d", "secao": ["01", "O *teste*"], "start": 0, "end": dur,
            "media": {"kind": "photo", "file": str(photo), "start": 0, "window_end": None, "credit": "Foto: Teste",
                      "own": False}}
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = cards.open_page(b)
        lst, n = cards.render_frames(page, cards.page_spec(card, BRAND_NO_LOGO, "pt-BR"), dur, tmp_path / "f1", False)
        assert n == round(dur * cards.FPS)
        cards.card_segment(lst, dur, tmp_path / "card.mp4")
        lst2, _ = cards.render_frames(page, cards.page_spec(foto, BRAND_NO_LOGO, "pt-BR"), dur, tmp_path / "f2", True)
        cards.photo_segment(foto, dur, lst2, tmp_path / "photo.mp4")
        b.close()
    for name in ("card.mp4", "photo.mp4"):
        out = tmp_path / name
        assert abs(_probe_duration(out) - dur) <= 0.05, name
        assert _mid_mean(out, dur, tmp_path / f"{name}.png") > 15, name


def _chromium():
    try:
        from playwright.sync_api import sync_playwright

        p = sync_playwright().start()
        try:
            return p, p.chromium.launch()
        except Exception:  # noqa: BLE001
            p.stop()
            return None
    except Exception:  # noqa: BLE001
        return None


def test_list_cards_lay_out_one_to_five_boxes(tmp_path):
    """Review finding 6: the "fluxo" list lays out N boxes (1–5) inside the frame with N−1 arrows; 3 boxes keep
    the original 500 px boxes at 110/710/1310."""
    got = _chromium()
    if not got:
        pytest.skip("Playwright Chromium not installed")
    pw, b = got
    try:
        page = cards.open_page(b)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for n in range(1, 6):
            items = [{"t": 0.1 + k * 0.3, "text": f"Item {k + 1}"} for k in range(n)]
            scene = {"tipo": "lista", "itens": [[f"w{k}", f"Item {k + 1}"] for k in range(n)], "_items": items,
                     "start": 0, "end": 2.0}
            spec = cards.page_spec(scene, BRAND_NO_LOGO, "pt-BR")
            page.evaluate("([s, d]) => window.setup(s, d)", [spec, 2.0])
            for t in (0.0, 0.5, 1.9):
                page.evaluate("t => window.frame(t)", t)
            boxes = page.evaluate("[...document.querySelectorAll('.box')].map(e => [e.offsetLeft, e.offsetLeft + e.offsetWidth,"
                                  " e.scrollWidth <= e.clientWidth + 1])")  # layout box, not the pop-in scale
            arrows = page.evaluate("document.querySelectorAll('.arrow').length")
            assert len(boxes) == n and arrows == n - 1, n
            assert all(0 <= l and r <= 1920 for l, r, _ in boxes), (n, boxes)
            assert all(fits for *_, fits in boxes), (n, boxes)
            assert all(boxes[k][1] < boxes[k + 1][0] for k in range(n - 1)), (n, boxes)
            if n == 3:
                assert [round(l) for l, _, _ in boxes] == [110, 710, 1310] and round(boxes[0][1] - boxes[0][0]) == 500
        assert errors == []
    finally:
        b.close()
        pw.stop()
