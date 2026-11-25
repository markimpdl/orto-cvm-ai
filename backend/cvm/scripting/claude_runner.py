"""Runs Claude Code (user's subscription login) headlessly for agent steps.

Never uses an API key: we strip ANTHROPIC_API_KEY from the environment so the CLI
falls back to the logged-in subscription. The agents read/write app data through
the project's `cvm` MCP server, configured in the repo's .mcp.json.
"""

import json
from pathlib import Path
import os
import subprocess
import sys

from cvm.config import REPO_ROOT, settings
from cvm.models import Job

SCRIPTING = (
    "Use the scriptwriter subagent to write the script for project {project_id} following the project's "
    "style and channel skills. It must read the project and its trend with the cvm MCP tools and save one "
    "script per project language with save_script.{feedback} "
    "Then use the qa-reviewer subagent on project {project_id}; if it finds problems, have the scriptwriter "
    "fix them and save new versions. End with one line: a summary of the script and the QA verdict (the system "
    "hands the job to the user for approval once every language has a script). If no script can be written, call "
    "set_job_state(job_id={job_id}, state='failed', note=<why>)."
)

SEO = (
    "Use the seo-optimizer subagent: for every render of project {project_id} that has no SEO yet "
    "(see get_project(project_id, detail='text') -> renders), write platform metadata in the render's language and save it with save_seo. "
    "Then call set_job_state(job_id={job_id}, state='awaiting_publish_review', note='SEO pronto')."
)

TREND_ANALYSIS = (
    "Use the trend-analyst subagent to analyse the collected trends{region_clause}. It should read them with "
    "list_trends, check performance_summary and list_channels, research the strongest candidates, and save up "
    "to 10 video ideas, best first, with save_trend_analysis(analysis_id={analysis_id}, summary=..., items=...)."
)

STEPS: dict[str, str] = {"scripting": SCRIPTING, "seo": SEO}


def _feedback(job: Job) -> str:
    """The user's latest rewrite request, if the script was sent back."""
    for h in reversed(job.history):
        if h.get("revise") and h.get("to") == "scripting":
            return (" REVISION, not a new script: the project already has scripts (get_project -> scripts, latest "
                    f"version per language). Keep their content, facts, sources and visuals; {h.get('note')}")
        if h.get("from") == "awaiting_approval" and h.get("to") == "scripting":
            return f" The user rejected the previous version with this feedback, address it: {h.get('note') or '(no note)'}."
        if h.get("to") == "awaiting_approval":
            break
    return ""


def build_prompt(job: Job, step: str | None = None) -> str | None:
    template = STEPS.get(step or job.state)
    if not template:
        return None
    return project_private_prompt(template.format(project_id=job.project_id, job_id=job.id, feedback=_feedback(job)),
                                  step or job.state, job.project_id)


STEP_AGENTS = {"scripting": ["scriptwriter", "qa-reviewer"], "seo": ["seo-optimizer"], "ideas": ["trend-analyst"],
               "trends": ["trend-analyst"]}


def with_private(prompt: str, step: str | None = None, agents: list[str] | None = None,
                 style: str | None = None, editorial: bool = False) -> str:
    """The prompt plus the owner's private addenda (none without `private/`): for the agents of `step` (or `agents`;
    fallback: the subagents the prompt names), and for the scriptwriter the style skill's (and the editorial one's)
    addendum. `claude -p` reads .claude/agents itself, so the addenda travel in the prompt. Idempotent."""
    import re

    from cvm import private

    names = agents or STEP_AGENTS.get(step or "") or [
        m.lower() for m in re.findall(r"(?:the|a)\s+([a-z-]+)\s+subagent", prompt, re.I)]
    names = list(dict.fromkeys(names))
    pieces = [(f"## Private rules for the {n} subagent (owner-only, follow them)", private.agent_rules(n)) for n in names]
    if "scriptwriter" in names:
        for sk in dict.fromkeys([*([style] if style else []), *(["editorial"] if editorial else [])]):
            pieces.append((f"## Private rules for the script-style-{sk} skill (apply on top of the skill)",
                           private.skill_rules(f"script-style-{sk}")))
    extra = [f"{h}\n{t}" for h, t in pieces if t and t not in prompt]
    return "\n\n".join([prompt, *extra]) if extra else prompt


def project_private_prompt(prompt: str, step: str | None, project_id: int | None) -> str:
    """with_private for a project's step: resolves the project's style and editorial-long status."""
    style, editorial = None, False
    if project_id is not None:
        try:
            from sqlmodel import Session

            from cvm import editorial as _ed
            from cvm.db import engine
            from cvm.models import Project

            with Session(engine) as s:
                if p := s.get(Project, project_id):
                    style = str(getattr(p.style, "value", p.style))
                    editorial = bool(_ed.long_languages(s, p))
        except Exception:  # private addenda must never break a step
            pass
    return with_private(prompt, step=step, style=style, editorial=editorial)


CHANNEL_IDEAS = (
    "Use the trend-analyst subagent to propose video ideas for ONE channel, from its niche rather than from what is "
    "trending: channel_id={channel_id} \"{name}\" ({platform}, {language}). Read its brief with list_channels (the "
    "\"Temas\" line is the niche; respect audience, tone and taboos). Research the niche with WebSearch for recent "
    "news, releases, curiosities and strong evergreen angles; list_trends may add timely hooks when they fit the niche "
    "(then put their ids in trend_ids, else trend_ids=[]). Check performance_summary for what worked. Do not repeat "
    "these videos the channel already has: {recent}. Save up to 10 ideas, best first, each with channel_id="
    "{channel_id} and hooks in {language}, with save_trend_analysis(analysis_id={analysis_id}, summary=..., items=...)."
)


def channel_ideas_prompt(analysis_id: int, channel, recent_titles: list[str]) -> str:
    return CHANNEL_IDEAS.format(analysis_id=analysis_id, channel_id=channel.id, name=channel.name,
                                platform=channel.platform.value, language=channel.language,
                                recent="; ".join(recent_titles[:30]) or "(none yet)")


def trend_analysis_prompt(analysis_id: int, region: str | None, shown: list[str] | None = None) -> str:
    prompt = TREND_ANALYSIS.format(analysis_id=analysis_id, region_clause=f" for region {region}" if region else " for all regions (BR, AE, US)")
    if shown:  # "new suggestions" must be new: the user already saw these
        prompt += (" The user already saw these ideas: do not suggest them again or the same subject from another "
                   "angle; bring different subjects: " + "; ".join(shown[:40]) + ".")
    return prompt


RUN_DIR = settings.data_dir / "run"  # one <pid>.claude file per running `claude -p`


LIGHT_STEPS = {"seo", "ideas", "viral", "research", "curation", "editorial"}


def model_for(step: str | None) -> str | None:
    return settings.claude_model_light if step in LIGHT_STEPS and settings.claude_model_light else settings.claude_model


def isolation_args(step: str | None = None, mcp_config: Path | None = None) -> list[str]:
    """Pipeline runs use only the project's configuration: no user plugins or hooks (claude-mem, superpowers), no
    user skills or MCP servers (claude.ai connectors), no ~/.claude/CLAUDE.md. Interactive sessions are unaffected.
    The subscription login is still used (never an API key). Measured 2026-11-15: 43.9k → 30.0k context tokens."""
    if not settings.claude_isolated:
        return []
    args = ["--setting-sources", "project", "--strict-mcp-config", "--mcp-config", str(mcp_config or REPO_ROOT / ".mcp.json"),
            "--settings", json.dumps({"claudeMdExcludes": [str(Path.home() / ".claude" / "CLAUDE.md")]})]
    if model := model_for(step):
        args += ["--model", model]
    return args


def _path_with_uv(path: str) -> str:
    """.mcp.json starts the cvm MCP with `uv`. On Windows a process started before uv was installed (or from a
    shell with a stale PATH) can't find it, and every agent run then fails without its tools: add the PATH saved
    in the registry, which has it."""
    import shutil

    if sys.platform != "win32" or shutil.which("uv", path=path):
        return path
    import winreg

    saved = []
    for root, key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(root, key) as k:
                saved.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
        except OSError:
            pass
    return ";".join([path, *saved])


def _limit_check(text: str) -> None:
    """The subscription limit was hit: remember until when (cvm/claude_limit.py) and raise ClaudeLimit."""
    from sqlmodel import Session

    from cvm import claude_limit
    from cvm.db import engine

    until = claude_limit.reset_time(text)
    with Session(engine) as session:
        claude_limit.set_limit(session, until, text)
    raise claude_limit.ClaudeLimit(until, text)


def run_claude(prompt: str, timeout: int = 1800, step: str | None = None, mcp_config: Path | None = None,
               allowed_tools: list[str] | None = None, disallowed_tools: list[str] | None = None) -> dict:
    """mcp_config: another MCP config for this run only (e.g. project + vidIQ for research, cvm/grow); allowed_tools:
    tools this run may call without asking (headless runs cannot ask)."""
    from sqlmodel import Session

    from cvm import claude_limit
    from cvm.db import engine

    with Session(engine) as session:  # still limited: don't even call it
        if until := claude_limit.blocked_until(session):
            raise claude_limit.ClaudeLimit(until, claude_limit.state(session).get("message", ""))
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["PATH"] = _path_with_uv(env.get("PATH", ""))
    # a kids episode's latest script alone is ~100 KB: above Claude Code's default MCP output cap, the result goes to
    # a file the subagents (no Read/Bash) cannot open
    env.setdefault("MAX_MCP_OUTPUT_TOKENS", "80000")
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [settings.claude_bin, "-p", prompt, "--output-format", "json", *isolation_args(step, mcp_config),
         *(["--allowedTools", ",".join(allowed_tools)] if allowed_tools else []),
         *(["--disallowedTools", ",".join(disallowed_tools)] if disallowed_tools else [])],
        cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8",  # not the Windows default (cp1252): Arabic output broke the reader and `out` came back None
    )
    marker = RUN_DIR / f"{proc.pid}.claude"
    marker.touch()
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    finally:
        marker.unlink(missing_ok=True)
    if proc.returncode != 0:
        if claude_limit.is_limit(err + out):
            try:  # the JSON result carries the readable message ("…limit · resets 10pm (Europe/Lisbon)")
                msg = str(json.loads(out).get("result") or "")
            except ValueError:
                msg = ""
            _limit_check(msg or (err.strip() or out.strip())[-500:])
        raise RuntimeError(f"claude exited {proc.returncode}: {err[-2000:] or out[-2000:]}")
    result = json.loads(out)
    if result.get("is_error") and claude_limit.is_limit(str(result.get("result", ""))):
        _limit_check(str(result.get("result", "")))
    return result


def stop_leftover_runs() -> list[int]:
    """Stop `claude -p` runs left by a worker that was killed (they keep running and would duplicate work)."""
    import psutil  # not `ps`: also works on Windows

    stopped = []
    markers = [*RUN_DIR.glob("*.claude"), *RUN_DIR.glob("*.codex")] if RUN_DIR.exists() else []
    for marker in markers:
        pid = int(marker.stem)
        try:
            cmd = psutil.Process(pid).cmdline()
            if (any("claude" in c for c in cmd[:2]) and "-p" in cmd) or (any("codex" in c for c in cmd[:2]) and "exec" in cmd):
                psutil.Process(pid).terminate()
                stopped.append(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        marker.unlink(missing_ok=True)
    return stopped
