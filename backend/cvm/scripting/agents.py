"""Which writer runs an agent step: Claude (the user's subscription) always first; Codex (ChatGPT subscription) only
while Claude is at its usage limit, and only where the user accepts its quality (2026-11-13):

- SEO and ideas/analyses: any channel;
- scripts: pt-BR / en-US channels that are not kids (kids series and Arabic wait for Claude), unless the channel sets
  `prefs.codex_scripts` (true = always allowed, false = never).

Each provider has its own limit (cvm/claude_limit.py); a step neither can take waits until the earliest reset.
Configurações (cvm/engines.py) can turn the Codex reserve off, or make Codex the first writer ("só Codex") where the
rules above let it write; kids/Arabic scripts then still go to Claude.
"""

from datetime import datetime

from sqlmodel import Session, col, select

from cvm import claude_limit, engines
from cvm.models import Channel, Job, Project, Render, Script

CODEX_SCRIPT_LANGS = ("pt-BR", "en-US")


def _claude_ready() -> bool:
    from cvm.setup import ai

    return bool(ai.status()["claude"]["logged_in"])


def codex_allowed(session: Session, step: str, project: Project | None = None) -> bool:
    if step != "scripting":
        return True  # seo, ideas
    if not project:
        return False
    if engines.writer() == "codex" and not _claude_ready():
        return True  # Codex-only install: it writes every script, kids and Arabic included
    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    own = ((channel.prefs or {}) if channel else {}).get("codex_scripts")
    if own is not None:
        return bool(own)
    kids = str(project.style) == "kids" or bool(channel and channel.made_for_kids)
    langs = [channel.language] if channel else (project.languages or [])
    return not kids and bool(langs) and all(lang in CODEX_SCRIPT_LANGS for lang in langs)


def wait_until(session: Session, step: str, project: Project | None = None) -> datetime | None:
    """None when some writer can take the step now; else the earliest time one of them is free again."""
    from cvm.scripting import codex_runner

    claude = claude_limit.blocked_until(session)
    if codex_first(session, step, project):
        codex = claude_limit.blocked_until(session, provider="codex")
        return min(claude, codex) if claude and codex else None
    if not claude:
        return None
    if not (codex_allowed(session, step, project) and codex_runner.available()):
        return claude
    codex = claude_limit.blocked_until(session, provider="codex")
    return min(claude, codex) if codex else None


def _tag(session: Session, project_id: int, step: str, since_script: int, writer: str) -> None:
    """Mark what Codex wrote, so the project page can say "escrito pelo Codex"."""
    if step == "scripting":
        for sc in session.exec(select(Script).where(Script.project_id == project_id, col(Script.id) > since_script)).all():
            sc.content = {**(sc.content or {}), "writer": writer}
            session.add(sc)
    elif step == "seo":
        for r in session.exec(select(Render).where(Render.project_id == project_id)).all():
            if r.seo and not (r.meta or {}).get("seo_writer"):
                r.meta = {**(r.meta or {}), "seo_writer": writer}
                session.add(r)
    session.commit()


def codex_first(session: Session, step: str, project: Project | None = None) -> bool:
    """The user picked "só Codex" (Setting "engines".writer) and Codex may write this step here."""
    from cvm import engines
    from cvm.scripting import codex_runner

    return engines.writer() == "codex" and codex_allowed(session, step, project) and codex_runner.available()


def _codex_prompt(session: Session, project: Project | None, prompt: str) -> str:
    """Codex cannot load skills: inline the style skill, plus the editorial one for an editorial long video."""
    from cvm import editorial
    from cvm.scripting import codex_runner

    extra = ["editorial"] if project and editorial.long_languages(session, project) else []
    return codex_runner.prompt_for(prompt, str(project.style) if project else None, extra)


def run(prompt: str, step: str, session: Session | None = None, project: Project | None = None,
        timeout: int = 1800) -> dict:
    """Run `prompt` with Claude; at Claude's limit, with Codex when allowed for this step. Returns the runner's result
    plus {"writer": "claude" | "codex"}. Raises ClaudeLimit (until the earliest reset) when nobody can take it.
    Writer "codex" (Configurações): Codex goes first where it may write; at its limit Claude takes the step."""
    from cvm.db import engine
    from cvm.scripting import codex_runner
    from cvm.scripting.claude_runner import run_claude

    from cvm.scripting.claude_runner import project_private_prompt

    prompt = project_private_prompt(prompt, step, project.id if project else None)  # idempotent
    own = session is None
    session = session or Session(engine)

    def codex() -> dict:
        last_script = (session.exec(select(Script.id).where(Script.project_id == project.id)
                                    .order_by(col(Script.id).desc())).first() or 0) if project else 0
        result = codex_runner.run_codex(_codex_prompt(session, project, prompt), timeout=timeout)
        if project:
            _tag(session, project.id, step, last_script, "codex")
        return {**result, "writer": "codex"}

    try:
        codex_until = None
        if codex_first(session, step, project):
            try:
                return codex()
            except claude_limit.ClaudeLimit as c:
                codex_until = c  # Codex at its limit: Claude takes this one
        try:
            return {**run_claude(prompt, timeout=timeout, step=step), "writer": "claude"}
        except claude_limit.ClaudeLimit as e:
            claude_until = e.until
        if codex_until is not None:
            raise claude_limit.ClaudeLimit(min(claude_until, codex_until.until), codex_until.message, "codex") from codex_until
        if not (codex_allowed(session, step, project) and codex_runner.available()):
            raise claude_limit.ClaudeLimit(claude_until, "limite do Claude; Codex não disponível para esta etapa")
        try:
            return codex()
        except claude_limit.ClaudeLimit as c:
            raise claude_limit.ClaudeLimit(min(claude_until, c.until), c.message, "codex") from c
    finally:
        if own:
            session.close()


def run_job_step(job: Job, session: Session, prompt: str) -> dict:
    project = session.get(Project, job.project_id)
    return run(prompt, job.state, session, project)


class VidiqUnavailable(RuntimeError):
    """A vidIQ run would go to Codex, Codex has no vidIQ login and Claude can't take it: callers degrade as on any
    vidIQ failure (yt-dlp search, "pesquisa falhou", no snapshot)."""


def _mcp_config(mcp: str | None):
    """The Claude MCP config each light caller used before run_light (same files, cvm/grow/viral_search)."""
    from cvm.grow.viral_search import mcp_config_empty, mcp_config_with_vidiq

    return {"vidiq": mcp_config_with_vidiq, "empty": mcp_config_empty}.get(mcp, lambda: None)()


class ClaudeBusy(RuntimeError):
    """run_light's claude_slot gave no slot: every Claude slot is taken by pipeline steps."""


def run_light(prompt: str, step: str, timeout: int = 900, mcp: str | None = None,
              allowed_tools: list[str] | None = None, disallowed_tools: list[str] | None = None,
              claude_slot=None) -> dict:
    """A light call (curation, viral scoring, vidIQ research) on the chosen writer. Returns the runner's result plus
    {"writer"}.

    - writer "claude" (or "manual"): run_claude exactly as the callers ran it before; at Claude's limit, Codex when it
      is available (reserve on, installed, logged in). vidIQ runs never move to Codex here: they spend the user's
      vidIQ credits, so with writer "claude" they stay on Claude (at its limit the caller's own fallback answers).
    - writer "codex": Codex first (isolated: only vidIQ or no MCP at all); at Codex's limit, or vidIQ not logged in
      on Codex, Claude takes it when it is logged in, else ClaudeLimit / VidiqUnavailable.
    mcp: None (Claude's default project MCP; Codex gets none), "empty" (no MCP), "vidiq" (only vidIQ).
    claude_slot: optional context factory yielding True/False, held only around the Claude call (a Codex run never
    occupies a Claude slot); no slot → ClaudeBusy."""
    from cvm.scripting import codex_runner
    from cvm.scripting.claude_runner import run_claude, with_private

    prompt = with_private(prompt, step=step)  # idempotent; unmapped steps get the named subagents' addenda only

    def claude() -> dict:
        if claude_slot is not None:
            with claude_slot() as got:
                if not got:
                    raise ClaudeBusy("Claude ocupado com outras etapas")
                return {**run_claude(prompt, timeout=timeout, step=step, mcp_config=_mcp_config(mcp),
                                     allowed_tools=allowed_tools, disallowed_tools=disallowed_tools),
                        "writer": "claude"}
        return {**run_claude(prompt, timeout=timeout, step=step, mcp_config=_mcp_config(mcp),
                             allowed_tools=allowed_tools, disallowed_tools=disallowed_tools), "writer": "claude"}

    def codex() -> dict:
        return {**codex_runner.run_codex(prompt, timeout=timeout, mcp=mcp or "empty", allowed_tools=allowed_tools,
                                         disallowed_tools=disallowed_tools),
                "writer": "codex"}

    if engines.writer() == "codex" and codex_runner.available():
        if mcp == "vidiq" and not codex_runner.vidiq_ready():
            if _claude_ready():
                return claude()
            raise VidiqUnavailable("vidIQ não está conectado no Codex (codex mcp login vidiq) e o Claude não está "
                                   "disponível")
        try:
            return codex()
        except claude_limit.ClaudeLimit as c:
            if not _claude_ready():
                raise
            try:
                return claude()
            except claude_limit.ClaudeLimit as e:
                raise claude_limit.ClaudeLimit(min(e.until, c.until), c.message, "codex") from c
    try:
        return claude()
    except claude_limit.ClaudeLimit as e:
        if not codex_runner.available() or mcp == "vidiq":  # vidIQ: Codex only when the user picked it (writer)
            raise
        try:
            return codex()
        except claude_limit.ClaudeLimit as c:
            raise claude_limit.ClaudeLimit(min(e.until, c.until), c.message, "codex") from c


def light(mcp: str | None):
    """run_light with run_claude's call shape, the default `run` of the light callers (tests pass their own fake).
    The caller's mcp_config is ignored: run_light builds the same file from `mcp`."""
    def run(prompt: str, timeout: int = 900, step: str = "light", mcp_config=None,
            allowed_tools: list[str] | None = None, disallowed_tools: list[str] | None = None) -> dict:
        return run_light(prompt, step, timeout=timeout, mcp=mcp, allowed_tools=allowed_tools,
                         disallowed_tools=disallowed_tools)
    return run
