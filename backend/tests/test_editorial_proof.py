import functools
import http.server
import json
import threading

import pytest
from PIL import Image

from cvm.visuals import proof

PAGE = """<html><body style="font:20px sans-serif;margin:40px">
<p>Intro paragraph about nothing.</p>
<div style="height:600px"></div>
<p id="a">The game does not boot yet, says the team.</p>
<p>Plain <b>split</b> across <i>inline</i> tags here.</p>
<div style="height:900px"></div></body></html>"""


EXTRA = {
    "inline.html": "<html><body><p>Say <b>a</b> <i>b</i> now</p><div>Heading</div><div>Next block</div></body></html>",
    "quotes.html": "<html><body><p>It's a \"fine\" day - really</p><p>non&nbsp;breaking&nbsp;space here</p></body></html>",
    "short.html": "<html><body style='margin:0'><p>tiny page phrase</p></body></html>",
    "dup.html": "<html><body><p style='display:none'>hidden twin</p><p>hidden twin</p></body></html>",
}


@pytest.fixture
def server(tmp_path):
    (tmp_path / "page.html").write_text(PAGE)
    for k, v in EXTRA.items():
        (tmp_path / k).write_text(v)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/page.html"
    srv.shutdown()


def test_capture_finds_phrase_and_boxes(server, tmp_path):
    out = tmp_path / "p.png"
    r = proof.capture(server, "The game does not boot yet", out)
    assert r and r["img"] == str(out) and out.exists()
    w, h = Image.open(out).size
    assert abs(r["size"][0] - w) <= 2 and abs(r["size"][1] - h) <= 2
    rects = r["boxes"][0]["rects"]
    assert rects and all(0 <= x and 0 <= y and x + rw <= w + 2 and y + rh <= h + 2 for x, y, rw, rh in rects)
    json.dumps(r["boxes"])


def test_phrase_absent_returns_none(server, tmp_path):
    assert proof.capture(server, "nothing like this", tmp_path / "p.png") is None


def test_piracy_host_refused(tmp_path):
    for u in ("https://gta6recomp.example.com/x", "https://cdn.site.com/free-download/a",
              "https://torrent-hub.org/a", "https://ftp.crackz.net/a", "ftp://x.com/a"):
        assert proof.capture(u, "x", tmp_path / "p.png") is None


def test_phrase_across_inline_tags(server, tmp_path):
    r = proof.capture(server, "Plain split across inline tags", tmp_path / "p.png")
    assert r and r["boxes"][0]["rects"]


def _site(url, name):
    return url.rsplit("/", 1)[0] + "/" + name


def test_allowed_whole_tokens():
    assert proof.allowed("https://news.com/eu-crackdown-on-apps")
    assert proof.allowed("https://news.com/app-downloads-surge")
    assert proof.allowed("https://firecracker.dev/a")
    assert proof.allowed("https://github.com/x/gta6recomp")
    assert not proof.allowed("https://gta6recomp.com/a")
    assert not proof.allowed("https://x.com/get/download/now")


def test_redirect_to_banned_host_refused(tmp_path):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://localhost:%d/x" % self.server.server_address[1])
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        orig = proof.PIRACY_HOSTS
        proof.PIRACY_HOSTS = ("localhost",)
        assert proof.capture(f"http://127.0.0.1:{srv.server_address[1]}/", "x", tmp_path / "p.png") is None
    finally:
        proof.PIRACY_HOSTS = orig
        srv.shutdown()


def test_whitespace_nodes_and_blocks(server, tmp_path):
    u = _site(server, "inline.html")
    assert proof.capture(u, "a b now", tmp_path / "1.png")
    assert proof.capture(u, "Heading Next block", tmp_path / "2.png")


def test_curly_quotes_dashes_nbsp(server, tmp_path):
    u = _site(server, "quotes.html")
    assert proof.capture(u, "It\u2019s a \u201cfine\u201d day \u2013 really", tmp_path / "1.png")
    assert proof.capture(u, "non breaking space", tmp_path / "2.png")


def test_size_from_real_png_on_short_page(server, tmp_path):
    out = tmp_path / "p.png"
    r = proof.capture(_site(server, "short.html"), "tiny page phrase", out)
    w, h = Image.open(out).size
    assert r["size"] == [w, h]
    assert all(y + rh <= h + 2 for _, y, _, rh in r["boxes"][0]["rects"])


def test_skips_invisible_first_occurrence(server, tmp_path):
    assert proof.capture(_site(server, "dup.html"), "hidden twin", tmp_path / "p.png")
