"""Assisted publishing in a real browser window (publish_mode "browser", also offered in every manual Kit).

Opens the platform's upload page in a new tab of the channel's own Google Chrome (persistent per-channel profile,
started once with a local DevTools port and reused by every click — see ensure_chrome), attaches the video and
fills title/description/caption. It never clicks the final "Publish": that stays a human decision, and the user
can fix anything the page layout broke. First run per channel: `login()` opens a plain Chrome (not automated —
Google refuses sign-in in automation-controlled browsers) on the channel's profile, data/browser-profiles/
publish-<channel id>; the user signs in there and closes it, and the assisted run reuses the session.
CVM never types passwords.

Each step is best effort — platforms change their pages often — and is reported in data/publish-browser/<pub>.json,
which the Kit shows. Run detached by the API:  python -m cvm.publish.browser_upload <publication id>
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

from sqlmodel import Session

from cvm.config import settings
from cvm.db import engine
from cvm.models import Channel, Project, Publication, Render
from cvm.publish.platforms import CATEGORIES, UPLOAD_PAGES, YOUTUBE_OFF_BOXES, YOUTUBE_PAID_PROMO_NO, caption_text, category_for, youtube_rules, youtube_tags

log = logging.getLogger(__name__)
LOGIN_WAIT_S = 600  # time for a first-time manual login
WATCH_MAX_S = 3 * 3600  # stop watching the tab after this (a forgotten tab kept 6 watchers at 100% CPU for 19 h)
STATUS_DIR = settings.data_dir / "publish-browser"


class Report:
    def __init__(self, pub_id: int):
        self.path = STATUS_DIR / f"{pub_id}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = {"state": "starting", "steps": [], "detected_url": None, "pid": os.getpid()}
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False))

    def state(self, s: str):
        self.data["state"] = s
        self.save()

    def step(self, name: str, fn, hint: str | None = None) -> bool:
        """Run one step; a failure is recorded with `hint` = what to do by hand (shown on /publicar)."""
        try:
            fn()
            self.data["steps"].append({"step": name, "ok": True})
            return True
        except Exception as e:  # noqa: BLE001 — a failed step is left for the user to do by hand
            log.warning("%s failed: %s", name, e)
            self.data["steps"].append({"step": name, "ok": False, "error": (str(e).splitlines() or [""])[0][:200],
                                       **({"hint": hint} if hint else {})})
            return False
        finally:
            self.save()


BUSY = ("starting", "waiting_login", "filling")


def status(pub_id: int) -> dict | None:
    """The run's report. A run whose process died mid-way (Chrome closed, crash) would stay "filling" forever and
    keep the UI's "Prepare again" disabled: it reads as "closed"."""
    import psutil

    p = STATUS_DIR / f"{pub_id}.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    if data.get("state") in BUSY and data.get("pid") and not psutil.pid_exists(data["pid"]):
        data["state"] = "closed"
    elif data.get("state") in BUSY and not data.get("pid") and time.time() - p.stat().st_mtime > LOGIN_WAIT_S + 60:
        data["state"] = "closed"  # older report without a pid, untouched for longer than any run can take
    return data


_DEEP_FIND = """(function f(root) {
  const el = root.querySelector('[data-cvm-file="1"]');
  if (el) return el;
  for (const n of root.querySelectorAll('*')) if (n.shadowRoot) { const r = f(n.shadowRoot); if (r) return r; }
  return null;
})(document)"""


def _set_files(page, selector: str, path: Path, timeout: int = 20000):
    """Attach a local file to an <input type=file> by PATH through the DevTools protocol. Playwright's
    set_input_files refuses files over 50 MB on a browser it attached to (connect_over_cdp), but this Chrome runs on
    the same machine, so Chrome can read the file itself."""
    box = page.locator(selector).first
    box.wait_for(state="attached", timeout=timeout)
    box.evaluate("el => el.setAttribute('data-cvm-file', '1')")
    cdp = page.context.new_cdp_session(page)
    try:
        obj = cdp.send("Runtime.evaluate", {"expression": _DEEP_FIND})["result"]
        if not obj.get("objectId"):
            raise RuntimeError("campo de arquivo não encontrado")
        cdp.send("DOM.setFileInputFiles", {"files": [str(Path(path).resolve())], "objectId": obj["objectId"]})
    finally:
        box.evaluate("el => el.removeAttribute('data-cvm-file')")
        cdp.detach()


def _replace_text(page, selector: str, text: str, timeout: int = 20000):
    box = page.locator(selector).first
    box.wait_for(state="visible", timeout=timeout)
    box.click()
    page.keyboard.press(SELECT_ALL)
    page.keyboard.press("Backspace")
    page.keyboard.insert_text(text)


SELECT_ALL = "Meta+A" if sys.platform == "darwin" else "Control+A"


def _untick(page, label_rx: str, timeout: int = 8000):
    """Leave a Studio checkbox unticked, found by its label (Studio's language varies: en/pt/es patterns). Studio
    checkboxes are <ytcp-checkbox-lit> with an inner role=checkbox carrying aria-checked."""
    rx = re.compile(label_rx, re.I)
    box = page.locator("ytcp-checkbox-lit").filter(has_text=rx)
    if box.count():
        target = box.first.locator("[role=checkbox]").first if box.first.locator("[role=checkbox]").count() else box.first
    else:
        target = page.get_by_role("checkbox", name=rx).first
    target.wait_for(state="attached", timeout=timeout)
    target.scroll_into_view_if_needed(timeout=timeout)
    if (target.get_attribute("aria-checked") or "").lower() == "true":
        _click_through(target, timeout)
        if (target.get_attribute("aria-checked") or "").lower() == "true":
            raise RuntimeError("a caixa continuou marcada")


def _click_through(target, timeout: int = 8000):
    """Studio's #checkbox-container sits over the role=checkbox div and "intercepts pointer events" (2026-11-24: the
    notify-subscribers and automatic-places boxes stayed ticked). Try a normal click briefly, then click through."""
    try:
        target.click(timeout=min(timeout, 3000))
    except Exception:  # noqa: BLE001
        target.click(timeout=timeout, force=True)


def _paid_promotion_no(page, timeout: int = 8000):
    """Answer "No, my video does not include paid promotion" (a radio pair, not a box)."""
    by_name = page.locator("[name=VIDEO_PAID_PRODUCT_PLACEMENT_NO]")
    target = (by_name if by_name.count() else page.get_by_role("radio", name=re.compile(YOUTUBE_PAID_PROMO_NO, re.I))).first
    target.wait_for(state="attached", timeout=timeout)
    target.scroll_into_view_if_needed(timeout=timeout)
    if (target.get_attribute("aria-checked") or "").lower() != "true":
        _click_through(target, timeout)
        if (target.get_attribute("aria-checked") or "").lower() != "true":
            raise RuntimeError("a opção “Não” não ficou marcada")


def _wait_file_input(page, rep: Report):
    """Upload page ready (logged in). Waits for a manual login if needed."""
    rep.state("waiting_login")
    page.locator("input[type=file]").first.wait_for(state="attached", timeout=LOGIN_WAIT_S * 1000)
    rep.state("filling")


def youtube(page, rep: Report, render: Render, video: Path):
    seo = render.seo or {}
    page.goto(UPLOAD_PAGES["youtube"])
    _wait_file_input(page, rep)
    title_box = page.locator("#title-textarea #textbox").first

    def attach():
        """Attach the video and wait for the details form. After a first login Studio may land on the content list
        with the upload dialog closed (2026-11-24): a hidden file input takes the file and nothing happens. Then the
        upload page is opened again once before giving up."""
        for attempt in range(2):
            _set_files(page, "input[type=file]", video)
            try:
                title_box.wait_for(state="visible", timeout=45000)
                return
            except Exception:  # noqa: BLE001
                if attempt:
                    raise RuntimeError("a janela de envio do YouTube não abriu; clique em “Preparar de novo”") from None
                page.goto(UPLOAD_PAGES["youtube"])
                page.locator("input[type=file]").first.wait_for(state="attached", timeout=60000)
    if not rep.step("arquivo de vídeo", attach):
        raise RuntimeError(rep.data["steps"][-1].get("error") or "a janela de envio do YouTube não abriu")
    title = (seo.get("title") or "")[:100]
    rep.step("título", lambda: _replace_text(page, "#title-textarea #textbox", title, 60000))
    desc = caption_text(render, "youtube", render.meta.get("_channel"))  # + channel footer/cross links
    rep.step("descrição", lambda: _replace_text(page, "#description-textarea #textbox", desc))
    thumb = Path((render.meta or {}).get("thumbnail") or "/nonexistent")
    if thumb.exists() and render.profile == "youtube":
        rep.step("thumbnail", lambda: _set_files(page, "#file-loader input[type=file], input#file-loader", thumb))
    kids = bool(render.meta.get("made_for_kids"))  # set from the channel by run() below

    def audience():
        if kids:
            by_name = page.locator("[name^=VIDEO_MADE_FOR_KIDS_MFK]")
            label = r"^(yes, it's made for kids|sim, é conteúdo para crianças|sí, es contenido para niños)"
        else:
            by_name = page.locator("[name^=VIDEO_MADE_FOR_KIDS_NOT]")
            label = r"not made for kids|não é conteúdo para crianças|no es contenido para niños"
        target = by_name if by_name.count() else page.get_by_role("radio", name=re.compile(label, re.I))
        target.first.click(timeout=8000)
    rep.step("é conteúdo para crianças" if kids else "não é conteúdo para crianças", audience)
    tags_input = page.locator("#tags-container input#text-input, input[aria-label='Tags']")

    def show_more():  # opens "Show more" once; clicking it again would fold the section back
        if not tags_input.first.is_visible():
            page.locator("#toggle-button").first.click(timeout=5000)
            tags_input.first.wait_for(state="visible", timeout=8000)

    # "Altered or synthetic content" — under "Show more": Yes only when the AI avatar is on screen (cvm/guards.py)
    ai = bool(render.meta.get("_ai"))

    def altered():
        show_more()
        page.locator(f"[name=VIDEO_HAS_ALTERED_CONTENT_{'YES' if ai else 'NO'}]").first.click(timeout=8000)
    rep.step("conteúdo alterado/sintético: Sim (avatar de IA)" if ai else "conteúdo alterado/sintético: Não (sem avatar)",
             altered)

    rules = youtube_rules(render.meta.get("_channel"))  # the owner's hard rules unless the channel set its own
    if rules["paid_promotion"]:
        rep.step("promoção paga: Sim (confira antes de publicar)",
                 lambda: (show_more(), page.locator("[name=VIDEO_PAID_PRODUCT_PLACEMENT_NOTIFY]").first.click(timeout=8000)))
    else:
        rep.step("promoção paga: Não", lambda: (show_more(), _paid_promotion_no(page)))
    for name, rx in rules["off_boxes"]:  # automatic chapters/places, notify subscribers: off unless the channel wants them
        rep.step(f"desmarcar: {name}", lambda rx=rx: (show_more(), _untick(page, rx)))
    for name, rx in YOUTUBE_OFF_BOXES:  # boxes the channel chose to keep on: Studio's own setting stays
        if (name, rx) not in rules["off_boxes"]:
            rep.step(f"deixado como está no Studio: {name}", lambda: None)

    tags = youtube_tags(seo.get("tags") or [])

    def fill_tags():
        show_more()
        box = tags_input.first
        box.click()
        for tag in tags:  # Enter turns each one into a chip
            page.keyboard.insert_text(tag)
            page.keyboard.press("Enter")
    if tags:
        rep.step(f"tags ({len(tags)})", fill_tags)

    cat = category_for(render, render.meta.get("_channel"), render.meta.get("_style"))
    names = CATEGORIES[cat]

    def category():  # "Category" dropdown under "Show more"; the option text follows the Studio's language
        show_more()
        page.locator("#category ytcp-dropdown-trigger, ytcp-form-select#category, #category").first.click(timeout=8000)
        rx = re.compile("^\\s*(" + "|".join(re.escape(n) for n in names) + ")\\s*$", re.I)
        opt = page.locator("tp-yt-paper-item, [role=option]").filter(has_text=rx)
        opt.first.click(timeout=8000)
    rep.step(f"categoria ({names[0]})", category)



# TikTok Studio (www.tiktok.com/tiktokstudio/upload, 2026): a video <input type=file accept="video/*"> (older layouts
# kept the uploader in an iframe), a Draft.js caption editor prefilled with the file name, and under "Show more" the
# "AI-generated content" switch, which may ask to confirm with "Turn on". Labels in English and Portuguese.
TIKTOK_FILE = "input[type=file][accept*=video], input[type=file]"
TIKTOK_CAPTION = ("[data-e2e=caption_container] [contenteditable=true], div.public-DraftEditor-content[contenteditable=true], "
                  ".caption-editor [contenteditable=true], div[contenteditable=true]")
TIKTOK_SHOW_MORE = r"^\s*(show more|exibir mais|mostrar mais|ver mais|mais opç[õo]es|more options)\s*$"
TIKTOK_AI = r"(ai-generated content|conte[úu]do gerado por (ia|intelig[êe]ncia artificial))"
TIKTOK_TURN_ON = r"^\s*(turn on|ativar|ligar)\s*$"
TIKTOK_HINTS = {
    "file": "Clique em “Abrir arquivo do vídeo” no kit e arraste o arquivo para a página do TikTok Studio.",
    "caption": "Copie a legenda no kit (botão Copiar) e cole no campo de descrição, apagando o nome do arquivo.",
    "ai_on": "Em “Mostrar mais”, ligue “Conteúdo gerado por IA” (o avatar de IA aparece no vídeo).",
    "ai_off": "Em “Mostrar mais”, confira se “Conteúdo gerado por IA” está desligado (não há avatar na tela).",
}


def _tiktok_file_frame(page, timeout_s: float):
    """The frame that holds the video input: the page itself, else an uploader iframe. Waits for a manual login."""
    deadline = time.time() + timeout_s
    while True:
        if page.locator(TIKTOK_FILE).count():
            return page.main_frame
        for fr in page.frames:
            try:
                if fr != page.main_frame and fr.locator(TIKTOK_FILE).count():
                    return fr
            except Exception:  # noqa: BLE001 — a frame detached while we looked
                continue
        if time.time() > deadline:
            raise RuntimeError("a página de upload do TikTok não abriu (falta fazer login?)")
        time.sleep(1)


def _checked(el) -> bool:
    state = el.get_attribute("aria-checked")
    if state is not None:
        return state.lower() == "true"
    try:
        return el.is_checked()
    except Exception:  # noqa: BLE001
        return False


def _tiktok_ai(page, want: bool):
    """Set the "AI-generated content" switch to `want` (it opens "Show more" first; confirms "Turn on")."""
    label = page.get_by_text(re.compile(TIKTOK_AI, re.I)).first
    if not label.count() or not label.is_visible():
        more = page.get_by_text(re.compile(TIKTOK_SHOW_MORE, re.I))
        if more.count():
            more.first.click(timeout=5000)
        label.wait_for(state="visible", timeout=8000)
    label.scroll_into_view_if_needed(timeout=5000)
    row = label.locator("xpath=ancestor::*[.//*[@role='switch'] or .//input[@type='checkbox']][1]")
    switch = row.locator("[role=switch], input[type=checkbox]").first
    switch.wait_for(state="attached", timeout=5000)
    if _checked(switch) == want:
        return
    _click_through(switch)
    confirm = page.get_by_role("button", name=re.compile(TIKTOK_TURN_ON, re.I))
    try:
        confirm.first.wait_for(state="visible", timeout=3000)
        confirm.first.click(timeout=3000)
    except Exception:  # noqa: BLE001 — no confirmation dialog this time
        pass
    time.sleep(0.5)
    if _checked(switch) != want:
        raise RuntimeError("o botão “Conteúdo gerado por IA” não mudou")


def tiktok(page, rep: Report, render: Render, video: Path):
    """Attach the video, write the caption, set the AI label by the same rule as YouTube's box. Never clicks Post."""
    page.goto(UPLOAD_PAGES["tiktok"])
    rep.state("waiting_login")
    frame = _tiktok_file_frame(page, LOGIN_WAIT_S)
    rep.state("filling")

    def attach():
        if frame == page.main_frame:
            _set_files(page, TIKTOK_FILE, video)
        else:  # uploader inside an iframe: Playwright attaches it (≤ 50 MB through this connection)
            frame.locator(TIKTOK_FILE).first.set_input_files(str(video))
    if not rep.step("arquivo de vídeo", attach, TIKTOK_HINTS["file"]):
        raise RuntimeError(TIKTOK_HINTS["file"])

    text = caption_text(render, "tiktok")

    def caption():
        box = page.locator(TIKTOK_CAPTION).first
        box.wait_for(state="visible", timeout=120000)  # shows up once the upload starts
        deadline = time.time() + 15  # TikTok prefills the file name: write after it, or it overwrites ours
        while time.time() < deadline and not (box.inner_text() or "").strip():
            time.sleep(0.5)
        # the hashtag suggestion list that opens after the last "#tag" can wipe the editor (owner, 2026-11-26:
        # "a descrição não autopreencheu"): close it, check the text stayed, write again up to 3 times
        head = text.strip().splitlines()[0][:15] if text.strip() else ""
        for _ in range(3):
            _replace_text(page, TIKTOK_CAPTION, text, 10000)
            time.sleep(1.5)
            page.keyboard.press("Escape")
            time.sleep(1.0)
            if head in (box.inner_text() or ""):
                return
        raise RuntimeError("o TikTok apagou a legenda; cole a legenda do kit")
    rep.step("legenda", caption, TIKTOK_HINTS["caption"])

    ai = bool((render.meta or {}).get("_ai"))
    rep.step("conteúdo gerado por IA: ligado (avatar de IA)" if ai else "conteúdo gerado por IA: desligado (sem avatar)",
             lambda: _tiktok_ai(page, ai), TIKTOK_HINTS["ai_on" if ai else "ai_off"])


def instagram(page, rep: Report, render: Render, video: Path):
    page.goto(UPLOAD_PAGES["instagram"])
    rep.state("waiting_login")
    page.locator("svg[aria-label='New post'], svg[aria-label='Nova publicação']").first.wait_for(timeout=LOGIN_WAIT_S * 1000)
    rep.state("filling")

    def open_create():
        page.locator("svg[aria-label='New post'], svg[aria-label='Nova publicação']").first.click()
        post = page.get_by_text(re.compile(r"^(Post|Publicação)$"))
        if post.count():
            post.first.click(timeout=3000)
        page.locator("input[type=file]").first.wait_for(state="attached", timeout=15000)
    if not rep.step("abrir 'Criar'", open_create):
        return
    rep.step("arquivo de vídeo", lambda: _set_files(page, "input[type=file]", video))

    def next_twice():
        for _ in range(2):
            page.get_by_role("button", name=re.compile(r"^(Next|Avançar)$")).first.click(timeout=60000)
            time.sleep(1.5)
    rep.step("avançar até a legenda", next_twice)
    rep.step("legenda", lambda: _replace_text(page, "div[aria-label^='Write a caption'], div[aria-label^='Escreva uma legenda']", caption_text(render, "instagram")))


FLOWS = {"youtube": youtube, "youtube_shorts": youtube, "tiktok": tiktok, "instagram": instagram}


WIN_CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
LOGIN_PAGES = {"youtube": "https://studio.youtube.com", "youtube_shorts": "https://studio.youtube.com",
               "tiktok": "https://www.tiktok.com/login", "instagram": "https://www.instagram.com/accounts/login/"}


def profile_dir(channel_id: int) -> Path:
    d = settings.data_dir / "browser-profiles" / f"publish-{channel_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


DEBUG_PORT_BASE = 9300  # each channel's Chrome listens on 9300 + channel id (DevTools, localhost only)


def debug_port(channel_id: int) -> int:
    return DEBUG_PORT_BASE + channel_id


def _endpoint_up(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5):
            return True
    except Exception:  # noqa: BLE001
        return False


def ensure_chrome(channel_id: int, url: str | None = None, timeout: float = 30) -> int:
    """The channel's own Google Chrome (persistent profile, real Keychain, NOT automation-flagged, so Google sign-in
    works), started with a DevTools port so every "Preparar no navegador" click attaches to the SAME window and opens
    a new tab — a second Chrome on a profile in use would just hand over to the first and exit. Returns the port."""
    import subprocess

    profile, port = profile_dir(channel_id), debug_port(channel_id)
    if _endpoint_up(port):
        if url:
            import urllib.parse
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe=':/?=&')}", method="PUT")
            urllib.request.urlopen(req, timeout=5).close()
        return port
    close_login_window(profile)  # a window on this profile without the port (older versions): close it cleanly
    args = [f"--user-data-dir={profile}", f"--remote-debugging-port={port}", "--remote-allow-origins=http://127.0.0.1",
            "--no-first-run", "--no-default-browser-check", *([url] if url else [])]
    if sys.platform == "win32":
        subprocess.Popen([str(WIN_CHROME), *args])
    else:
        subprocess.Popen(["open", "-na", "Google Chrome", "--args", *args])
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _endpoint_up(port):
            return port
        time.sleep(0.5)
    raise RuntimeError("o Chrome do canal não abriu a porta de controle")


def login(channel: Channel) -> None:
    """The channel's Chrome on its login page, for a one-time manual sign-in (the same window later uploads reuse)."""
    ensure_chrome(channel.id, LOGIN_PAGES[channel.platform.value])


def _close_login_window_win(profile: Path, timeout: float) -> bool:
    """Windows: find the login Chrome by its --user-data-dir and close it gently (taskkill without /F asks the
    windows to close, so the session cookies get written)."""
    import subprocess

    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -like "
          f"'*--user-data-dir={profile}*' -and $_.CommandLine -notlike '*--type=*' }} | ForEach-Object {{ $_.ProcessId }}")

    def pids() -> list[int]:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
        return [int(x) for x in out.split() if x.isdigit()]

    found = running = pids()
    for pid in running:
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True)
    deadline = time.time() + timeout
    while running and time.time() < deadline:
        time.sleep(0.5)
        running = pids()
    if running:
        raise RuntimeError("o Chrome de login não fechou; feche a janela dele e tente de novo")
    return bool(found)


def close_login_window(profile: Path, timeout: float = 20) -> bool:
    """Quit any Chrome running on this profile (SIGTERM = clean shutdown, so the session cookies get written).
    Returns True if one was running."""
    import os
    import signal
    import subprocess

    if sys.platform == "win32":
        found = _close_login_window_win(profile, timeout)
        (profile / "SingletonLock").unlink(missing_ok=True)
        return found
    out = subprocess.run(["pgrep", "-f", f"Google Chrome.app/Contents/MacOS/Google Chrome .*--user-data-dir={profile}"],
                         capture_output=True, text=True).stdout.split()
    out += subprocess.run(["pgrep", "-f", f"Google Chrome --user-data-dir={profile}"], capture_output=True, text=True).stdout.split()
    pids = sorted({int(x) for x in out if int(x) != os.getpid()})
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + timeout
    while pids and time.time() < deadline:
        pids = [p for p in pids if subprocess.run(["kill", "-0", str(p)], capture_output=True).returncode == 0]
        time.sleep(0.5)
    if pids:
        raise RuntimeError("o Chrome do canal não fechou; feche-o com ⌘Q e tente de novo")
    (profile / "SingletonLock").unlink(missing_ok=True)
    return bool(out)


def run(pub_id: int) -> None:
    from playwright.sync_api import sync_playwright

    rep = Report(pub_id)
    with Session(engine) as s:
        pub = s.get(Publication, pub_id)
        render, channel = s.get(Render, pub.render_id), s.get(Channel, pub.channel_id)
        project = s.get(Project, render.project_id)
        style = getattr(project.style, "value", project.style) if project else None
        from cvm.guards import avatar_on_screen

        synthetic = youtube_rules(channel)["synthetic"]  # "auto" (owner's rule) = only with the avatar on screen
        ai = avatar_on_screen(s, project, render) if synthetic == "auto" else synthetic == "yes"
        if channel.platform.value == "tiktok":  # same rule, read from the YouTube channel it is linked to
            from cvm.publish.tiktok import ai_label

            ai = ai_label(s, project, render, channel)
        s.expunge_all()
    try:
        port = ensure_chrome(channel.id)
    except RuntimeError as e:
        rep.data["error"] = str(e)
        rep.state("closed")
        return
    with sync_playwright() as p:
        try:  # attach to the channel's Chrome (already open or just started) and work in a tab of our own
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
        except Exception as e:  # noqa: BLE001
            rep.data["error"] = f"Chrome não abriu: {str(e)[:300]}"
            rep.state("closed")
            return
        try:
            render.meta = {**(render.meta or {}), "made_for_kids": bool(channel.made_for_kids), "_channel": channel,
                           "_style": style, "_ai": ai}  # in memory only
            FLOWS[channel.platform.value](page, rep, render, Path(render.path))
            rep.state("review")  # the user checks the form and clicks Publish
        except Exception as e:  # noqa: BLE001
            rep.data["error"] = str(e).splitlines()[0][:300]
            rep.state("manual")
        # keep watching our tab until the user closes it (or the whole window); remember the video link it shows
        started, checked = time.time(), 0.0
        while not page.is_closed():
            if time.time() - started > WATCH_MAX_S:
                log.info("upload %s: tab watched for %d h, letting it go", pub_id, WATCH_MAX_S // 3600)
                break
            if time.time() - checked > 30:  # marked published (here or in /publicar): nothing left to watch
                checked = time.time()
                with Session(engine) as s:
                    pub = s.get(Publication, pub_id)
                    if not pub or pub.status == "published":
                        break
            try:
                html = page.locator("body").inner_text()
                m = re.search(r"https://(?:youtu\.be|www\.youtube\.com/shorts|youtube\.com/shorts)/[\w-]+"
                              r"|https://www\.tiktok\.com/@[\w.]+/video/\d+", html)
                if m and m.group(0) != rep.data["detected_url"]:
                    rep.data["detected_url"] = m.group(0)
                    rep.save()
            except Exception:  # noqa: BLE001
                if not browser.is_connected():
                    break
            time.sleep(3)
        rep.state("closed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run(int(sys.argv[1]))
