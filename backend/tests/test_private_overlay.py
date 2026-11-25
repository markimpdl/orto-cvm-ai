from types import SimpleNamespace

from cvm import private
from cvm.channels import brief_text
from cvm.models import Channel, Job, Platform
from cvm.scripting import claude_runner, codex_runner


def _job():
    return Job(id=7, project_id=3, state="scripting")


def _channel():
    return Channel(name="Studio", platform=Platform.youtube, youtube_handle="@CozinhaDaVila")


def test_missing_private_dir_is_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(private, "PRIVATE_DIR", tmp_path / "nope")
    assert private.text("agents/x.md") == ""
    assert private.json("a.json", {"k": 1}) == {"k": 1}
    plain = claude_runner.SCRIPTING.format(project_id=3, job_id=7, feedback="")
    assert claude_runner.build_prompt(_job()) == plain
    assert "Regras privadas" not in brief_text(_channel())
    assert "Private rules" not in codex_runner.prompt_for(plain, "kids")


def test_private_addenda_appended(tmp_path, monkeypatch):
    monkeypatch.setattr(private, "PRIVATE_DIR", tmp_path)
    for rel, body in {"agents/scriptwriter.md": "AGENT-SECRET", "skills/script-style-kids.md": "SKILL-SECRET",
                      "channel_rules/cozinhadavila.md": "CHANNEL-SECRET", "x.json": '{"a": 2}'}.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    prompt = claude_runner.build_prompt(_job())
    assert prompt.count("AGENT-SECRET") == 1
    assert brief_text(_channel()).count("CHANNEL-SECRET") == 1
    assert private.json("x.json", None) == {"a": 2}
    codex = codex_runner.prompt_for(prompt, "kids")  # already in the prompt: not repeated
    assert codex.count("AGENT-SECRET") == 1
    assert codex.count("SKILL-SECRET") == 1
    assert codex_runner.prompt_for(claude_runner.SCRIPTING, "kids").count("AGENT-SECRET") == 1


def test_codex_does_not_repeat_skill_rules_already_in_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(private, "PRIVATE_DIR", tmp_path)
    _write(tmp_path, {"skills/script-style-kids.md": "SKILL-SECRET", "agents/scriptwriter.md": "AGENT-SECRET"})
    task = claude_runner.SCRIPTING + "\n\nSKILL-SECRET"  # the Claude prompt already carries the skill addendum
    codex = codex_runner.prompt_for(task, "kids")
    assert codex.count("SKILL-SECRET") == 1 and codex.count("AGENT-SECRET") == 1
    assert codex_runner.prompt_for(claude_runner.SCRIPTING, "kids").count("SKILL-SECRET") == 1


def _write(root, files):
    for rel, body in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)


def test_skill_rules_normalise_underscores(tmp_path, monkeypatch):
    monkeypatch.setattr(private, "PRIVATE_DIR", tmp_path)
    _write(tmp_path, {"skills/script-style-top-list.md": "TOP"})
    assert private.skill_rules("script-style-top_list") == "TOP"


def test_channel_rules_by_id_when_name_slugs_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(private, "PRIVATE_DIR", tmp_path)
    _write(tmp_path, {"channel_rules/12.md": "BYID"})
    ch = Channel(id=12, name="قناة", platform=Platform.youtube)
    assert private.channel_rules(ch) == "BYID"
    assert "BYID" in brief_text(ch)


def test_with_private_step_agents_style_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(private, "PRIVATE_DIR", tmp_path)
    _write(tmp_path, {"agents/trend-analyst.md": "IDEAS-SECRET", "agents/scriptwriter.md": "SW",
                      "skills/script-style-kids.md": "KIDS", "skills/script-style-editorial.md": "ED"})
    p = claude_runner.with_private("Propose ideas for a channel.", step="ideas")  # names no subagent
    assert p.count("IDEAS-SECRET") == 1 and claude_runner.with_private(p, step="ideas") == p
    p = claude_runner.with_private("x", step="scripting", style="kids", editorial=True)
    assert p.count("KIDS") == 1 and p.count("ED") >= 1 and p.count("SW") == 1
    assert "KIDS" not in claude_runner.with_private("x", step="seo", style="kids")


def test_get_project_private_rules(tmp_path, monkeypatch):
    from cvm.mcp_server import server
    monkeypatch.setattr(private, "PRIVATE_DIR", tmp_path)
    _write(tmp_path, {"skills/script-style-kids.md": "KIDS"})
    assert server._private_style_rules(SimpleNamespace(style="kids"), False) == "KIDS"
    assert server._private_style_rules(SimpleNamespace(style="dark"), False) == ""
