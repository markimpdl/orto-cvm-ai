"""Visible steps of the phase before the cut editor (owner, 2026-11-25: "só mostra em andamento"):
download → light copy (480p proxy) → whole-video transcript → signals → AI reading the transcript block by block →
ready for review.

The worker code reports where it is with `report(step, label, pct=…, done=…, total=…)`; nothing is passed down the
call chain: `use(sink)` sets the current reporter for this thread (a ContextVar), so yt-dlp/ffmpeg/whisper/the
detection only call `report()` and do nothing when no one listens (tests, the automatic path, other callers).
`use()` nests: inside a lives run (sink = the run item) the analysis adds the CutReview row, and every report goes to
both, so the Crescer list and the queue show the same step.

State: {step, label, pct (0–100 or None), done, total, since (ISO, when this step began), at, waiting (bool)}.
Writes are throttled (MIN_INTERVAL_S apart) except when the step changes or `force=True`; each sink opens its own
short session and writes one row, so a report never commits the caller's pending work:
- `review_sink(engine, review_id)` → CutReview.progress;
- `setting_sink(engine, key)` → Setting "progress:<key>" (the download before a review exists: lives run items use
  "lives:<channel_id>:<video_id>", /fabrica/novo link requests "fabrica:<request key>"). A separate row on purpose:
  rewriting the shared item lists every 2 s would race the API adding a new link to the same list.
"""

import logging
import re
import subprocess
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

log = logging.getLogger(__name__)
MIN_INTERVAL_S = 2.0
PREFIX = "progress:"

# pt-BR labels (the frontend translates them through its i18n table like any other API text)
DOWNLOAD, PROXY, TRANSCRIPT, SIGNALS, AI, READY, QUEUED = ("download", "proxy", "transcript", "signals", "ai", "ready",
                                                          "queued")
GPU_WAIT = "esperando a vez da GPU"
SLOT_WAIT = "esperando outro vídeo terminar de baixar"

Sink = Callable[[dict], None]


class Reporter:
    def __init__(self, sinks: list[Sink], min_interval: float = MIN_INTERVAL_S, clock=time.monotonic):
        self.sinks = sinks
        self.min_interval = min_interval
        self.clock = clock
        self.state: dict | None = None
        self._last = float("-inf")
        self._pending = False

    def update(self, step: str, label: str, pct: float | None = None, done: float | None = None,
               total: float | None = None, waiting: bool = False, force: bool = False) -> bool:
        """True when this update was written."""
        prev = self.state or {}
        new_step = prev.get("step") != step
        since = _iso() if new_step or not prev.get("since") else prev["since"]
        self.state = {"step": step, "label": label, "pct": None if pct is None else round(max(0.0, min(100.0, pct)), 1),
                      "done": done, "total": total, "since": since, "waiting": bool(waiting)}
        now = self.clock()
        if not (force or new_step or prev.get("waiting") != bool(waiting) or now - self._last >= self.min_interval):
            self._pending = True
            return False
        self._write(now)
        return True

    def flush(self) -> None:
        if self._pending and self.state:
            self._write(self.clock())

    def _write(self, now: float) -> None:
        self._last, self._pending = now, False
        state = {**self.state, "at": _iso()}
        for sink in self.sinks:
            try:
                sink(state)
            except Exception as e:  # noqa: BLE001 — progress must never break the analysis
                log.warning("progress write failed: %s", e)


_current: ContextVar[Reporter | None] = ContextVar("cvm_prep_progress", default=None)


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def current() -> Reporter | None:
    return _current.get()


@contextmanager
def use(sink: Sink, min_interval: float | None = None):
    """Report to `sink` (and to the sinks of an outer `use`) inside this block."""
    outer = _current.get()
    gap = outer.min_interval if outer else MIN_INTERVAL_S if min_interval is None else min_interval
    rep = Reporter([*(outer.sinks if outer else []), sink], gap,
                   outer.clock if outer else time.monotonic)
    if outer and outer.state:
        rep.state, rep._last = dict(outer.state), outer._last
    token = _current.set(rep)
    try:
        yield rep
    finally:
        rep.flush()
        _current.reset(token)
        if outer and rep.state:  # the outer reporter goes on from where the inner one stopped
            outer.state, outer._last = dict(rep.state), rep._last


def report(step: str, label: str, **kw) -> None:
    rep = _current.get()
    if rep:
        rep.update(step, label, **kw)


# --- sinks -------------------------------------------------------------------------------------------------------

def review_sink(engine, review_id: int) -> Sink:
    def write(state: dict) -> None:
        from sqlmodel import Session

        from cvm.models import CutReview

        with Session(engine) as s:
            r = s.get(CutReview, review_id)
            if r:
                r.progress = state
                s.add(r)
                s.commit()
    return write


def setting_sink(engine, key: str) -> Sink:
    def write(state: dict) -> None:
        from sqlmodel import Session

        from cvm.models import Setting

        with Session(engine) as s:
            row = s.get(Setting, PREFIX + key) or Setting(key=PREFIX + key, value={})
            row.value = dict(state)
            s.add(row)
            s.commit()
    return write


def mark(engine, key: str, step: str, label: str, waiting: bool = False) -> None:
    """One write outside any reporter (e.g. the worker re-scheduling a download that waits for a free slot): keeps
    `since` while the step stays the same, so the page can say how long it has waited."""
    from sqlmodel import Session

    from cvm.models import Setting

    try:
        with Session(engine) as s:
            row = s.get(Setting, PREFIX + key)
            old = dict(row.value) if row and row.value else {}
            since = old.get("since") if old.get("step") == step and old.get("since") else _iso()
            setting_sink(engine, key)({"step": step, "label": label, "pct": None, "done": None, "total": None,
                                       "since": since, "at": _iso(), "waiting": waiting})
    except Exception as e:  # noqa: BLE001
        log.warning("progress mark failed: %s", e)


def engine_of(session):
    return session.get_bind()


def get(session, key: str) -> dict | None:
    from cvm.models import Setting

    row = session.get(Setting, PREFIX + key)
    return dict(row.value) if row and row.value else None


def clear(session, key: str) -> None:
    """Drop a download's progress row once its item/request is no longer working (commits only that delete)."""
    from sqlmodel import Session

    from cvm.models import Setting

    try:
        with Session(engine_of(session)) as s:
            row = s.get(Setting, PREFIX + key)
            if row:
                s.delete(row)
                s.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("progress clear failed: %s", e)


def lives_key(channel_id: int, video_id: str) -> str:
    return f"lives:{channel_id}:{video_id}"


def fabrica_key(request_key: str) -> str:
    return f"fabrica:{request_key}"


# --- parsers -----------------------------------------------------------------------------------------------------

YTDLP_PCT = re.compile(r"\[download\]\s+(\d{1,3}(?:\.\d+)?)%")
FFMPEG_TIME = re.compile(r"(?:out_time_us|out_time_ms)=(\d+)")
FFMPEG_CLOCK = re.compile(r"(?:out_time|time)=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def ytdlp_pct(line: str) -> float | None:
    """`[download]  42.3% of ~1.20GiB at 5.1MiB/s ETA 02:11` → 42.3 (None for any other line)."""
    m = YTDLP_PCT.search(line or "")
    return float(m.group(1)) if m else None


def ffmpeg_seconds(line: str) -> float | None:
    """ffmpeg `-progress` lines (out_time_us=…, out_time=00:01:02.5) or its stats line (time=00:01:02.50) → seconds."""
    line = line or ""
    m = FFMPEG_TIME.search(line)
    if m:
        return int(m.group(1)) / 1e6  # both keys are microseconds (ffmpeg's out_time_ms is misnamed)
    m = FFMPEG_CLOCK.search(line)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return None


def hms(t: float) -> str:
    """Seconds → "1h12" / "12 min" / "45 s", the way the labels show audio positions."""
    t = max(0, int(t))
    if t >= 3600:
        return f"{t // 3600}h{t // 60 % 60:02d}"
    if t >= 60:
        return f"{t // 60} min"
    return f"{t} s"


class Ytdlp:
    """Feeds yt-dlp's output lines: "Baixando 42%". A download in several parts (video, then audio) restarts at
    0%; the label keeps the part number so the bar going back down is not a surprise."""

    def __init__(self):
        self.part, self.last = 1, 0.0

    def __call__(self, line: str) -> None:
        if "[download] Destination:" in line and self.last > 0:
            self.part, self.last = self.part + 1, 0.0
        pct = ytdlp_pct(line)
        if pct is None:
            return
        self.last = pct
        label = f"Baixando {pct:.0f}%" + (f" (parte {self.part})" if self.part > 1 else "")
        report(DOWNLOAD, label, pct=pct)


def run_lines(cmd: list[str], on_line: Callable[[str], None], timeout: float | None = None,
              popen=None) -> subprocess.CompletedProcess:
    """subprocess.run(capture_output=True, text=True) that also hands every output line (stdout and stderr merged,
    \\r-progress split into lines) to `on_line` as it comes. stdout of the result carries everything (stderr is
    the same text, so callers reading either still see the messages)."""
    p = (popen or subprocess.Popen)(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    t0 = time.monotonic()
    out: list[str] = []
    try:
        for raw in p.stdout:
            for line in raw.replace("\r", "\n").splitlines():
                out.append(line)
                try:
                    on_line(line)
                except Exception:  # noqa: BLE001 — a progress parse error never breaks the download
                    pass
            if timeout and time.monotonic() - t0 > timeout:
                p.kill()
                raise subprocess.TimeoutExpired(cmd, timeout)
        code = p.wait()
    finally:
        if p.poll() is None:
            p.kill()
    text = "\n".join(out[-400:])
    return subprocess.CompletedProcess(cmd, code, stdout=text, stderr=text)


def ytdlp_run(cmd: list[str], *, timeout: float | None = None, **_ignored) -> subprocess.CompletedProcess:
    """Drop-in for subprocess.run(cmd, capture_output=True, text=True, timeout=…) on a yt-dlp download: adds
    `--newline` and reports "Baixando N%" through the current reporter."""
    cmd = [cmd[0], "--newline", *cmd[1:]]
    return run_lines(cmd, Ytdlp(), timeout)
