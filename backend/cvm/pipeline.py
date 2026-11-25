"""Job state machine for the content pipeline.

States that wait on a human (approval, manual publishing) are listed in HUMAN_GATES;
the worker never advances them on its own.
"""

import re
from datetime import datetime
from statistics import median

from cvm.models import Job, now

STATES = [
    "trend_selected",
    "scripting",
    "awaiting_approval",
    "voicing",
    "sourcing_visuals",
    "lipsyncing",
    "composing",
    "rendering",
    "validating",
    "seo",
    "awaiting_publish_review",
    "scheduled",
    "ready_for_manual_publish",
    "published",
    "failed",
]

TRANSITIONS: dict[str, set[str]] = {
    "trend_selected": {"scripting"},
    "scripting": {"awaiting_approval"},
    "awaiting_approval": {"voicing", "scripting"},  # approve or send back for rewrite
    "voicing": {"sourcing_visuals"},
    "sourcing_visuals": {"lipsyncing", "voicing"},  # voicing: the narration is of an older script (editorial)
    "lipsyncing": {"composing"},
    "composing": {"rendering", "sourcing_visuals", "voicing"},  # the editorial video sends its scenes/voice back
    "rendering": {"validating", "seo"},  # seo: jobs rendered before the validation step existed
    # automatic validation (cvm/curation.py): on to SEO, or back by itself to the step that fixes a problem it found
    "validating": {"seo", "scripting", "voicing", "sourcing_visuals", "composing"},
    "seo": {"awaiting_publish_review"},
    # schedule, re-compose after swapping visuals, redo voice+video (e.g. new avatar/voice), or rewrite the script
    "awaiting_publish_review": {"scheduled", "composing", "voicing", "scripting"},
    # manual mode vs api/browser mode; until it is published, a video can still be redone (its kits are dropped)
    "scheduled": {"ready_for_manual_publish", "published", "composing", "voicing", "scripting"},
    "ready_for_manual_publish": {"published", "composing", "voicing", "scripting"},
    "published": {"ready_for_manual_publish"},  # the user undoes "Marcar como publicado"
    "failed": set(),
}

HUMAN_GATES = {"awaiting_approval", "awaiting_publish_review", "ready_for_manual_publish"}


class InvalidTransition(ValueError):
    pass


def can_transition(src: str, dst: str) -> bool:
    if dst == "failed":
        return src not in {"published", "failed"}
    return dst in TRANSITIONS.get(src, set())


def advance(job: Job, dst: str, note: str | None = None) -> Job:
    if not can_transition(job.state, dst):
        raise InvalidTransition(f"{job.state} -> {dst}")
    entry = {"from": job.state, "to": dst, "at": now().isoformat(), "note": note}
    if started := (job.progress or {}).get("started"):  # time actually spent working (not waiting) in the step
        entry["worked_s"] = round((now() - datetime.fromisoformat(started)).total_seconds(), 1)
    job.history = [*job.history, entry]
    job.state = dst
    job.progress = {}
    job.error = note if dst == "failed" else None
    job.updated_at = now()
    return job


def send_to_revision(job: Job, note: str) -> Job:
    """Back to "scripting" from any step before the render (the user asked to apply new script rules to videos
    already in the queue). The scriptwriter gets `note` and revises the latest version instead of starting over."""
    if job.state not in {"awaiting_approval", "voicing", "sourcing_visuals", "lipsyncing", "composing", "scripting",
                         "validating",  # validating: the automatic check found a script problem (cvm/curation.py)
                         "awaiting_publish_review"}:  # a finished video the user wants remade (editorial format)
        raise InvalidTransition(f"{job.state} -> scripting (revision)")
    job.history = [*job.history, {"from": job.state, "to": "scripting", "at": now().isoformat(), "note": note, "revise": True}]
    job.state, job.progress, job.error, job.updated_at = "scripting", {}, None, now()
    return job


def retry(job: Job) -> Job:
    """Return a failed job to the state it failed in (a render that failed QA is composed again, not re-checked)."""
    if job.state != "failed" or not job.history:
        raise InvalidTransition("only failed jobs with history can be retried")
    prev = job.history[-1]["from"]
    if prev == "rendering":
        prev = "composing"
    job.history = [*job.history, {"from": "failed", "to": prev, "at": now().isoformat(), "note": "retry"}]
    job.state, job.error, job.updated_at = prev, None, now()
    return job


# typical seconds per step before any job has measured it (M5 Pro, ~60 s video, 2 languages)
DEFAULT_SECONDS = {"scripting": 300, "voicing": 150, "sourcing_visuals": 240, "lipsyncing": 240,
                   "composing": 120, "rendering": 30, "validating": 60, "seo": 150}


def typical_durations(jobs: list[Job]) -> dict[str, float]:
    """Median seconds each timed step took in past jobs (successful passes only), else the defaults."""
    samples: dict[str, list[float]] = {}
    for job in jobs:
        prev_at = None
        for h in job.history:
            if not isinstance(h.get("at"), str):  # a hand-made entry without a time must not break /projects
                prev_at = None
                continue
            at = datetime.fromisoformat(h["at"])
            took = h.get("worked_s")
            src = h.get("from")  # a hand-made entry may lack from/to (project 724, 2026-11-24): skip, never 500
            if took is None and src in DEFAULT_SECONDS and src not in ("scripting", "seo") and prev_at:
                took = (at - prev_at).total_seconds()  # older history: automatic steps start as soon as entered
            if took is not None and h.get("to") != "failed" and src in DEFAULT_SECONDS:
                samples.setdefault(src, []).append(took)
            prev_at = at
    return {s: round(median(samples[s]) if s in samples else d, 1) for s, d in DEFAULT_SECONDS.items()}


def seconds_since(iso: str) -> float:
    return (now() - datetime.fromisoformat(iso)).total_seconds()


EPISODE_PLACEHOLDER = re.compile(r"^(?P<base>.+ – Ep\. \d+)\s*$")
_EP = r"(?:Ep\.?|Episódio|Episode|الحلقة|حلقة)\s*[\d٠-٩]+"
_EP_REF = re.compile(r"(Ep\.?|Episódio|Episode|الحلقة|حلقة)\s*[\d٠-٩]+", re.I)
_SERIES_PREFIX = re.compile(rf"^.*?{_EP}\s*[:–—-]\s*", re.I)  # "Série – Ep. 3: título"
_SERIES_SUFFIX = re.compile(rf"\s*(?:\||[–—-])\s*[^|–—-]*{_EP}\s*$", re.I)  # "título | Série Ep. 1"


def episode_label(title: str, series: str = "") -> str:
    """The episode's own title, without the series name / episode number the scriptwriter sometimes adds."""
    t = _SERIES_SUFFIX.sub("", _SERIES_PREFIX.sub("", title.strip())).strip()
    if series and (s := series.strip()):
        t = re.sub(rf"\s*\|\s*{re.escape(s)}\s*$", "", t).strip()
    return t or title.strip()


def fix_episode_number(text: str, number: int | None, series: str = "") -> str:
    """The episode number in a kids title is the project's own, never one the scriptwriter worked out (it used to
    write the series' next number: "Ep. 27" on episode 13). An unnumbered episode gets no number at all."""
    if not text or not _EP_REF.search(text):
        return text
    if number:
        return _EP_REF.sub(lambda m: f"{m[1]} {number}", text)
    return episode_label(text, series)


def episode_titles(session, project, content: dict) -> None:
    """Put the project's episode number in the packaging titles (the YouTube title and its Test & Compare
    alternatives) of a kids episode, in place."""
    from cvm.channels import episode_number

    pk = (content or {}).get("packaging")
    if not pk or not project or project.style != "kids":
        return
    n = episode_number(project.title)
    series = (project.title or "").split(" – ")[0] if n else ""
    pk["title"] = fix_episode_number(pk.get("title", ""), n, series)
    for alt in pk.get("alternatives") or []:
        if isinstance(alt, dict) and alt.get("title"):
            alt["title"] = fix_episode_number(alt["title"], n, series)


def name_episode(session, project_id: int, content: dict) -> bool:
    """A series episode queued without a premise is titled "<series> – Ep. N"; once its script brings a packaging
    title, the project becomes "<series> – Ep. N: <title>" so the UI shows it. Titles already named are kept.
    The packaging titles get the project's episode number first (episode_titles)."""
    from cvm.models import Project

    project = session.get(Project, project_id)
    episode_titles(session, project, content)
    title = ((content or {}).get("packaging") or {}).get("title", "").strip()
    if not (title and project and (m := EPISODE_PLACEHOLDER.match(project.title or ""))):
        return False
    base = m["base"]
    if title.startswith(base):  # the scriptwriter already wrote "<series> – Ep. N: …"
        title = title[len(base):].lstrip(" :–-")
    title = episode_label(title, base.rsplit(" – Ep.", 1)[0]) if title else title
    project.title = f"{base}: {title}" if title else base
    session.add(project)
    return True
