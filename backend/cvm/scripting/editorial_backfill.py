"""Backfill `editorial.cenas` for editorial scripts written before the format existed (a light AI step: the
narration and sources are already there, only the news-desk scene list is missing)."""
import logging

from sqlmodel import Session

from cvm import claude_limit
from cvm.models import Project, Script
from cvm.render.editorial.timeline import EditorialError
from cvm.scripting import agents
from cvm.scripting.schema import Editorial, editorial_problems
from cvm.voice.narrate import latest_script

log = logging.getLogger(__name__)

SKILL = "script-style-editorial"

PROMPT = """Você escreve a lista de cenas do formato editorial (mesa de notícias) para um roteiro que já existe.
Não altere a narração. Leia a skill abaixo e siga o contrato de `editorial.cenas` à risca.

=== SKILL {skill} ===
{skill_text}
=== FIM DA SKILL ===

Idioma do vídeo: {language}
Tema: {topic}
Fontes (as únicas URLs permitidas em prova.url):
{sources}

Narração, cena a cena (cada âncora `em` é UMA palavra dita, na ordem):
{narration}
{problems}
Responda SOMENTE com JSON: {{"cenas": [ ... ]}}, sem texto fora do JSON."""


def _skill_text() -> str:
    from cvm.scripting.codex_runner import SKILLS

    return (SKILLS / SKILL / "SKILL.md").read_text(encoding="utf-8")


def _ask(prompt: str, claude_slot=None) -> dict:
    """The runner's answer. Runner errors (limit, busy slots, timeouts, crashes) propagate as they are: they are not
    the script's fault, so the step waits or fails like any other step instead of sending the script back."""
    return agents.run_light(prompt, "editorial", timeout=900, mcp="empty", allowed_tools=[], disallowed_tools=None,
                            claude_slot=claude_slot)


def _cenas(res: dict) -> list:
    from cvm.curation import _json_object

    cenas = _json_object(str(res.get("result", ""))).get("cenas")
    if not isinstance(cenas, list):
        raise ValueError("a resposta não trouxe a lista `cenas`")
    return cenas


def _problems(content: dict, cenas: list, language: str) -> list[str]:
    try:
        Editorial.model_validate({"cenas": cenas})
    except Exception as e:  # noqa: BLE001
        return [f"cena fora do contrato: {str(e)[:300]}"]
    return editorial_problems({**content, "editorial": {"cenas": cenas}}, language)


def backfill(session: Session, project: Project, language: str, claude_slot=None) -> dict:
    """Returns the content with `editorial` (saved as a new Script version, `writer` kept). Present already: nothing
    is asked. At Claude's limit with nobody able to take it: ClaudeLimit (the job waits). Two bad answers:
    EditorialError (the job goes back to revision). Runner errors (timeouts, crashes, no free Claude slot →
    agents.ClaudeBusy) are raised as they are. `claude_slot`: the worker's Claude slot factory (agents.run_light)."""
    sc = latest_script(session, project.id, language)
    if sc is None:
        raise EditorialError(f"sem roteiro em {language} para gerar as cenas editoriais")
    content = dict(sc.content or {})
    if content.get("editorial"):
        return content
    if until := agents.wait_until(session, "editorial", project):
        raise claude_limit.ClaudeLimit(until, "cenas editoriais esperam o limite")
    narration = "\n".join(f"{s.get('index')}. {s.get('narration', '')}" for s in content.get("scenes") or [])
    base = dict(language=language, topic=(project.title or project.topic or "")[:300], skill=SKILL,
                skill_text=_skill_text(), narration=narration,
                sources="\n".join(content.get("sources") or []) or "(nenhuma)")
    problems: list[str] = []
    writer = None
    for attempt in range(2):
        extra = ("\nSua resposta anterior tinha estes problemas; corrija todos:\n- " + "\n- ".join(problems) + "\n"
                 ) if problems else ""
        res = _ask(PROMPT.format(problems=extra, **base), claude_slot)
        writer = res.get("writer")
        try:  # only a bad answer counts as a try that may end in EditorialError
            cenas = _cenas(res)
        except ValueError as e:
            problems = [str(e)]
            continue
        problems = _problems(content, cenas, language)
        if not problems:
            break
    if problems:
        raise EditorialError("não consegui gerar as cenas editoriais do roteiro antigo: " + "; ".join(problems)[:600])
    new = {**content, "editorial": {"cenas": cenas}}
    if writer == "codex":  # `writer` stays the script's own writer
        new["editorial_writer"] = writer
    session.add(Script(project_id=project.id, language=language, version=sc.version + 1, content=new))
    session.commit()
    log.info("project %s %s: editorial scenes backfilled (%d)", project.id, language, len(cenas))
    return new
