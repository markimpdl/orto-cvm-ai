"""Runs the Codex CLI (`codex exec`, the user's ChatGPT subscription login) headlessly — the fallback writer when
Claude is at its usage limit (cvm/scripting/agents.py decides when).

Never uses an API key: OPENAI_API_KEY is stripped so the CLI uses the logged-in subscription. Codex has no subagents
or Claude skills, so the prompt carries the agent's instructions (.claude/agents/<name>.md) and the style skill
(.claude/skills/script-style-<style>/SKILL.md) inline; it reads and writes app data through the same `cvm` MCP
server (configured on the command line, same as the repo's .mcp.json).

Light runs (cvm/scripting/agents.run_light: curation, viral scoring, vidIQ research) read third-party text, so they get
none of that: `mcp="empty"` = no MCP server at all, `mcp="vidiq"` = only the vidIQ server (its tools limited to the
caller's allowed list); the user's own Codex servers, plugins, apps, hooks, shell and web search are switched off for
the run (the Codex side of viral_search.DENY). Nothing is written to ~/.codex/config.toml: vidIQ is added per run
with `-c`; its OAuth login is the user's (`codex mcp login vidiq`), and vidiq_ready() says whether it is there.
"""

import json
import os
import re
import shutil
import subprocess

from cvm import private
from cvm.config import REPO_ROOT, settings

AGENTS = REPO_ROOT / ".claude" / "agents"
SKILLS = REPO_ROOT / ".claude" / "skills"
_SUBAGENT = re.compile(r"(?:the|a)\s+([a-z-]+)\s+subagent", re.I)


_seen: dict = {"at": 0.0, "ok": False}
VIDIQ_URL = "https://mcp.vidiq.com/mcp"
VIDIQ = f'mcp_servers.vidiq.url="{VIDIQ_URL}"'
LIGHT_MCP = ("empty", "vidiq")
# features that give a light run tools it must not have (plugin/app MCP servers, the user's hooks, the shell = Bash in
# DENY, browsers/computer use = web outside web_search, sub-agents, image generation, skills); switched off when this
# Codex knows them (codex 0.169 `codex features list`)
LIGHT_DISABLED = (
    "plugins", "apps", "remote_plugin", "plugin_sharing", "hooks", "shell_tool", "unified_exec", "unified_exec_tty",
    "browser_use", "browser_use_external", "browser_use_full_cdp_access", "browser_annotation_api", "computer_use",
    "in_app_browser", "in_app_local_automation", "image_generation", "view_image", "multi_agent",
    "skill_mcp_dependency_install", "skill_search", "code_mode_host", "code_mode_host_grpc", "tool_suggest", "goals",
    "workspace_dependencies",
)
# features known to add no tool; any other feature left on refuses the light run (a newer Codex may add tools)
LIGHT_SAFE = frozenset({
    "api_key_cyber_access_programs", "api_key_model_discovery", "auth_elicitation", "bedrock_setup_wizard",
    "cli_daybreak", "code_mode_host_output_truncation", "code_mode_interrupt", "compaction_image_budget",
    "content_item_kinds", "daemon_auto_start", "deferred_tool_world_state", "enable_request_compression", "fast_mode",
    "guardian_approval", "guardian_include_additional_context", "guardian_reuse_parent_compaction", "in_app_chat",
    "in_app_dictation", "in_app_updates", "in_app_voice", "incremental_tools", "incremental_tools_legacy_gcp",
    "instant_interrupt", "mcp_oauth_refresh_coordination", "mentions_v2", "prediction_preserve_parent_tools",
    "reasoning_effort_override", "retain_client_tool_outputs", "shell_snapshot", "sleep_tool", "system_proxy_fallback",
    "tool_call_mcp_elicitation", "ultrafast_mode", "unbounded_connection_retries", "unified_image_budget",
    "untrusted_as_tool_output", "write_stdin_approval",
})
# the only vidIQ tools a light run may enable: the read-only ones the grow/* callers list (research, viral_search,
# vidiq_stats, vidiq_review TOOLS). Anything else (writes, posts, replies, uploads, credit-spending generators, studio
# jobs, a newer tool) is refused, even when a caller lists it. Adding a caller tool means adding it here on purpose.
VIDIQ_READ_ONLY = frozenset({
    "vidiq_keyword_research", "vidiq_outliers", "vidiq_list_competitors",  # grow/research
    "vidiq_trending_videos",  # grow/viral_search (+ outliers, keyword_research)
    "vidiq_channel_analytics", "vidiq_user_channels",  # grow/vidiq_stats
    "vidiq_score_title", "vidiq_generate_titles", "vidiq_score_thumbnail",  # grow/vidiq_review
})
_features_seen: dict = {}
_SAFE_NAME = re.compile(r"[A-Za-z0-9_-]+")
LOGGED_IN = ("o_auth", "oauth", "bearer_token", "logged_in")


def available() -> bool:
    """Switched on (Configurações: Codex as the reserve, or Codex as the writer) and installed and logged in."""
    from cvm import engines

    return (engines.codex_fallback() or engines.writer() == "codex") and installed()


def installed() -> bool:
    """Installed and logged in with ChatGPT (re-checked every 5 min: a PC may install or log in later)."""
    import time

    if not (exe := shutil.which(settings.codex_bin)):
        return False
    if time.monotonic() - _seen["at"] < 300:
        return _seen["ok"]
    _seen.update(at=time.monotonic(), ok=_logged_in(exe))
    return _seen["ok"]


def _logged_in(exe: str) -> bool:
    try:
        out = subprocess.run([exe, "login", "status"], capture_output=True, text=True, timeout=30,
                             env=_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and "logged in" in (out.stdout + out.stderr).lower()


def _mcp_list(exe: str, *args: str) -> list[dict]:
    """`codex mcp list --json` (read-only: lists the configured servers, starts none of them)."""
    out = subprocess.run([exe, "mcp", "list", "--json", *args], capture_output=True, text=True, timeout=30,
                         env=_env(), stdin=subprocess.DEVNULL, cwd=REPO_ROOT)
    if out.returncode != 0:
        raise RuntimeError(f"codex mcp list exited {out.returncode}: {(out.stderr or out.stdout)[-300:]}")
    data = json.loads(out.stdout or "[]")
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def vidiq_ready() -> bool:
    """Codex is logged in and has the vidIQ server logged in (OAuth by the user: `codex mcp login vidiq`).
    The server is passed with -c here too, so this works whether or not the user added it to his config."""
    if not installed():
        return False
    exe = shutil.which(settings.codex_bin) or settings.codex_bin
    try:
        servers = _mcp_list(exe, "-c", VIDIQ)
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError):
        return False
    return any(s.get("name") == "vidiq" and str(s.get("auth_status") or "").lower() in LOGGED_IN for s in servers)


def _user_servers(exe: str) -> list[str]:
    """MCP servers the user's own Codex config would start (plugins and apps are switched off separately)."""
    return [str(s.get("name")) for s in _mcp_list(exe, "--disable", "plugins", "--disable", "apps") if s.get("name")]


def _features(exe: str, *args: str) -> dict[str, tuple[str, bool]]:
    """{feature: (stage, effective state)} from `codex features list [--disable …]` (read-only), cached 5 min."""
    import time

    key = (exe, args)
    if (hit := _features_seen.get(key)) and time.monotonic() - hit[0] < 300:
        return hit[1]
    out = subprocess.run([exe, "features", "list", *args], capture_output=True, text=True, timeout=30, env=_env(),
                         stdin=subprocess.DEVNULL, cwd=REPO_ROOT)
    if out.returncode != 0:
        raise RuntimeError(f"codex features list exited {out.returncode}: {(out.stderr or out.stdout)[-300:]}")
    value = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] in ("true", "false"):
            value[parts[0]] = (" ".join(parts[1:-1]), parts[-1] == "true")
    if not value:
        raise RuntimeError("codex features list: saída ilegível")
    _features_seen[key] = (time.monotonic(), value)
    return value


def _vidiq_tools(allowed_tools: list[str] | None) -> list[str]:
    """The vidIQ tools a light run may call: the caller's allow list, only names in VIDIQ_READ_ONLY. Fails closed."""
    tools = [t.removeprefix("mcp__vidiq__") for t in allowed_tools or [] if t.startswith("mcp__vidiq__")]
    if bad := [t for t in tools if t not in VIDIQ_READ_ONLY]:
        raise ValueError(f"ferramentas do vidIQ fora da lista de leitura não são permitidas aqui: {bad}")
    if not tools:
        raise ValueError("execução com vidIQ sem lista de ferramentas permitidas")
    return tools


def _light_args(exe: str, mcp: str, allowed_tools: list[str] | None,
                disallowed_tools: list[str] | None = None) -> list[str]:
    """No MCP but vidIQ (when asked), no shell, no browser, no hooks/plugins: what a light run may use. Web search only
    when the caller allows WebSearch/WebFetch on Claude too (curation's fact check), never when it denies them."""
    tools = _vidiq_tools(allowed_tools) if mcp == "vidiq" else []
    off = [f for f in LIGHT_DISABLED if f in _features(exe)]
    flags = tuple(a for feat in off for a in ("--disable", feat))
    # what is still on with our flags (codex 0.169 keeps unified_exec on whatever we pass: it is only the shell's
    # backend and gives no tool once shell_tool is off, so it is accepted only then)
    effective = _features(exe, *flags)
    shell_off = not effective.get("shell_tool", ("", True))[1]
    if left := sorted(f for f, (stage, on) in effective.items()
                      if on and stage != "removed" and f not in LIGHT_SAFE
                      and not (shell_off and f in ("unified_exec", "unified_exec_tty"))):
        raise RuntimeError(f"recurso do Codex ligado fora da lista segura ({', '.join(left)}): execução isolada recusada")
    web = (bool({"WebSearch", "WebFetch"} & set(allowed_tools or []))
           and not {"WebSearch", "WebFetch"} & set(disallowed_tools or []))
    args = [*flags, "-c", f'web_search="{"live" if web else "disabled"}"']
    for name in _user_servers(exe):
        if not _SAFE_NAME.fullmatch(name):
            raise RuntimeError(f"servidor MCP do Codex com nome inesperado ({name!r}): execução isolada recusada")
        if name == "vidiq" and mcp == "vidiq":
            continue
        args += ["-c", f"mcp_servers.{name}.enabled=false"]
    if mcp == "vidiq":
        args += ["-c", VIDIQ, "-c", "mcp_servers.vidiq.enabled=true",
                 "-c", 'mcp_servers.vidiq.default_tools_approval_mode="approve"',
                 "-c", "mcp_servers.vidiq.enabled_tools=" + json.dumps(tools)]
    return args


def _env() -> dict:
    from cvm.scripting.claude_runner import _path_with_uv

    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
    env["PATH"] = _path_with_uv(env.get("PATH", ""))
    return env


def _body(path) -> str:
    """A .md without its YAML front matter."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        text = parts[2] if len(parts) == 3 else text
    return text.strip()


def prompt_for(claude_prompt: str, style: str | None = None, extra_skills: list[str] | None = None) -> str:
    """The Claude prompt rewritten for Codex: every subagent it names becomes a section of instructions to follow
    directly, plus the style skill the scriptwriter would load (and `extra_skills`, e.g. "editorial" for an
    editorial long video)."""
    agents = list(dict.fromkeys(m.lower() for m in _SUBAGENT.findall(claude_prompt)))
    parts = ["You work alone (no subagents). Where the task below says 'use the X subagent', do that work yourself "
             "following X's instructions, which are included after the task. Use the cvm MCP tools for all project "
             "data; never edit files. Write in the language each project/render/channel asks for.",
             "## Task", claude_prompt]
    for name in agents:
        parts += [f"## Instructions of the {name} subagent", _body(AGENTS / f"{name}.md")]
        if (extra := private.agent_rules(name)) and extra not in claude_prompt:  # build_prompt may have added it
            parts.append(extra)
    if "scriptwriter" in agents:
        for name in dict.fromkeys([*([style] if style else []), *(extra_skills or [])]):
            skill = SKILLS / f"script-style-{name.replace('_', '-')}" / "SKILL.md"
            if skill.exists():
                parts += [f"## Skill script-style-{name.replace('_', '-')} (the scriptwriter must follow it)", _body(skill)]
                if (extra := private.skill_rules(f"script-style-{name}")) and extra not in claude_prompt:
                    parts.append(extra)  # with_private may already have put it in the task
    return "\n\n".join(p for p in parts if p)


def run_codex(prompt: str, timeout: int = 1800, mcp: str | None = "cvm", allowed_tools: list[str] | None = None,
              disallowed_tools: list[str] | None = None) -> dict:
    """{"result": last agent message, "events": n}. Raises ClaudeLimit(provider="codex") at the usage limit.
    mcp: "cvm" (agent steps: the project's cvm server + live web search), "vidiq" (only vidIQ, limited to
    `allowed_tools`), "empty"/None (no MCP); the last two are isolated light runs (_light_args)."""
    from sqlmodel import Session

    from cvm import claude_limit
    from cvm.db import engine
    from cvm.scripting.claude_runner import RUN_DIR

    with Session(engine) as session:
        if until := claude_limit.blocked_until(session, provider="codex"):
            raise claude_limit.ClaudeLimit(until, (claude_limit.state(session, "codex") or {}).get("message", ""), "codex")
    if mcp not in ("cvm", None, *LIGHT_MCP):
        raise ValueError(f"mcp: {mcp!r}")
    exe = shutil.which(settings.codex_bin) or settings.codex_bin
    cmd = [exe, "exec", "--json", "--skip-git-repo-check",
           "--ephemeral", "-s", "read-only", "-C", str(REPO_ROOT),
           "-c", 'approval_policy="never"', "-c", "notify=[]"]
    if mcp == "cvm":
        cmd += ["-c", 'web_search="live"',
                "-c", 'mcp_servers.cvm.command="uv"', "-c", 'mcp_servers.cvm.args=["run","--project","backend","cvm-mcp"]',
                "-c", 'mcp_servers.cvm.default_tools_approval_mode="approve"', "-c", "mcp_servers.cvm.tool_timeout_sec=600"]
    else:
        cmd += _light_args(exe, mcp or "empty", allowed_tools, disallowed_tools)
    if settings.codex_model:
        cmd += ["-m", settings.codex_model]
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(cmd + [prompt], cwd=REPO_ROOT, env=_env(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    marker = RUN_DIR / f"{proc.pid}.codex"
    marker.touch()
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    finally:
        marker.unlink(missing_ok=True)
    last, errors = "", []
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        item = ev.get("item") or {}
        if item.get("type") == "agent_message":
            last = item.get("text") or last
        if ev.get("type") in ("error", "turn.failed"):
            errors.append(json.dumps(ev.get("error") or ev.get("message") or ev, ensure_ascii=False))
    problem = " ".join(errors) + " " + (err if proc.returncode else "")
    if claude_limit.is_limit(problem):
        text = problem.strip()[-500:]
        until = claude_limit.reset_time(text)
        with Session(engine) as session:
            claude_limit.set_limit(session, until, text, "codex")
        raise claude_limit.ClaudeLimit(until, text, "codex")
    if proc.returncode != 0 or errors:
        raise RuntimeError(f"codex exited {proc.returncode}: {problem.strip()[-2000:]}")
    return {"result": last, "events": len(out.splitlines())}
