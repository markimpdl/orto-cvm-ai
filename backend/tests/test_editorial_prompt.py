"""Task 2 of the editorial format: the single "is this an editorial long video" rule, get_project telling the
scriptwriter, and save_script refusing editorial scripts that break the contract (Claude and Codex both save there)."""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from cvm import editorial
from cvm.models import Channel, Project, Script

SRC = "https://example.com/fonte"


@pytest.fixture
def engine(monkeypatch):
    from cvm.mcp_server import server
    import cvm.db

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(server, "engine", eng)
    monkeypatch.setattr(cvm.db, "engine", eng)
    with Session(eng) as s:
        s.add(Channel(id=1, name="Mesa", platform="youtube", language="pt-BR", presenter="editorial"))
        s.add(Channel(id=2, name="Cenas", platform="youtube", language="pt-BR", presenter="scenes"))
        s.add(Project(id=10, title="t", topic="t", channel_id=1, languages=["pt-BR"], platforms=["youtube"]))
        s.add(Project(id=11, title="t", topic="t", channel_id=2, languages=["pt-BR"], platforms=["youtube"]))
        s.add(Project(id=12, title="t", topic="t", channel_id=1, languages=["pt-BR"], platforms=["youtube_shorts"]))
        s.commit()
    return eng


def test_wanted_rule(engine):
    with Session(engine) as s:
        p = s.get(Project, 10)
        assert p.presenter is None  # the channel's default decides
        assert editorial.wanted(s, p, "pt-BR", "youtube")
        assert editorial.wanted(s, p, "en-US", "youtube")
        assert not editorial.wanted(s, p, "pt-BR", "youtube_shorts")
        assert not editorial.wanted(s, p, "ar-AE", "youtube")
        assert not editorial.wanted(s, s.get(Project, 11), "pt-BR", "youtube")
        other = s.get(Project, 11)
        other.presenter = "editorial"  # the project's choice beats the channel's
        assert editorial.wanted(s, other, "pt-BR", "youtube")
        p.presenter = "scenes"
        assert not editorial.wanted(s, p, "pt-BR", "youtube")


def test_wanted_only_for_narrated_styles(engine):
    """live/react/kids/lesson/review carry recorded audio or character lines: no anchors, the old path stays."""
    with Session(engine) as s:
        p = s.get(Project, 10)
        for style in ("explainer", "top_list", "dark"):
            p.style = style
            assert editorial.wanted(s, p, "pt-BR", "youtube"), style
        for style in ("live", "react", "kids", "lesson", "review"):
            p.style = style
            assert not editorial.wanted(s, p, "pt-BR", "youtube"), style


def test_get_project_flags_editorial_long(engine):
    from cvm.mcp_server import server

    assert server._project_data(10)["editorial_long"] is True
    assert server._project_data(11)["editorial_long"] is False
    assert server._project_data(12)["editorial_long"] is False  # Shorts only: keeps the vertical editorial


def _cenas(fx) -> list[dict]:
    out = []
    for c in fx.cenas[:8]:
        keep = {k: v for k, v in c.items() if k not in {"img", "src", "de", "titulo_tela", "selo", "escuro",
                                                         "destaque_em", "traducao"}}
        if c["tipo"] == "prova":
            keep["prova"] = {"url": SRC, "frase": c.get("texto") or "frase", "destaque_em": c.get("destaque_em")}
        if c["tipo"] == "video":
            keep["busca"] = {"termos": ["gta 6 trailer"], "preferir": "oficial"}
        out.append(keep)
    return out


def _content(fx, **extra) -> dict:
    return {"hook": "x", "scenes": [{"index": 0, "narration": fx.narration, "visual": {"type": "headline"}}],
            "sources": [SRC], **extra}


def test_save_script_rejects_bad_editorial(engine, editorial_set):
    fx = editorial_set
    from cvm.mcp_server import server

    with pytest.raises(ValueError, match="editorial"):  # editorial long video without the scene list
        server.save_script(10, "pt-BR", _content(fx))
    bad = _cenas(fx)
    bad[1]["em"] = "zzzinexistente"
    with pytest.raises(ValueError, match="zzzinexistente"):
        server.save_script(10, "pt-BR", _content(fx, editorial={"cenas": bad}))
    with Session(engine) as s:
        assert not s.exec(select(Script).where(Script.project_id == 10)).all()  # nothing saved
    ok = server.save_script(10, "pt-BR", _content(fx, editorial={"cenas": _cenas(fx)}))
    assert ok["version"] == 1
    # not editorial long: no scene list needed (other presenters, Shorts, Arabic)
    assert server.save_script(11, "pt-BR", _content(fx))["version"] == 1
    assert server.save_script(12, "pt-BR", _content(fx))["version"] == 1
    assert server.save_script(10, "ar-AE", _content(fx))["version"] == 1
    # an editorial list where none is wanted (e.g. an ar-AE copy) is dropped, never a reason to refuse
    ar = server.save_script(10, "ar-AE", _content(fx, editorial={"cenas": bad}))
    with Session(engine) as s:
        assert "editorial" not in s.get(Script, ar["script_id"]).content
    with Session(engine) as s:
        assert s.get(Script, ok["script_id"]).content["editorial"]["cenas"][0]["em"]


def test_codex_prompt_carries_editorial_skill(engine):
    """The Codex fallback inlines skills (it cannot load them): an editorial long project gets the editorial skill."""
    from cvm.scripting import agents, codex_runner

    text = codex_runner.prompt_for("Use the scriptwriter subagent", "explainer", extra_skills=["editorial"])
    assert "script-style-explainer" in text and "## Skill script-style-editorial" in text
    assert "## Skill script-style-editorial" not in codex_runner.prompt_for("Use the scriptwriter subagent", "explainer")
    with Session(engine) as s:
        assert agents._codex_prompt(s, s.get(Project, 10), "Use the scriptwriter subagent").count(
            "## Skill script-style-editorial") == 1
        assert "## Skill script-style-editorial" not in agents._codex_prompt(s, s.get(Project, 11),
                                                                            "Use the scriptwriter subagent")


def test_stray_invalid_editorial_block_dropped_not_refused(engine, editorial_set):
    """Review finding 8: an `editorial` block that breaks the schema (too few scenes, bad tipo, two-word anchors)
    on a project that is not editorial-long is dropped before ScriptContent validation, never refused."""
    from pydantic import ValidationError

    from cvm.mcp_server import server

    fx = editorial_set
    junk = {"cenas": [{"em": "duas palavras", "tipo": "nada"}]}
    for pid, lang in ((11, "pt-BR"), (12, "pt-BR"), (10, "ar-AE")):
        r = server.save_script(pid, lang, _content(fx, editorial=junk))
        with Session(engine) as s:
            assert "editorial" not in s.get(Script, r["script_id"]).content
    with pytest.raises(ValidationError):  # the editorial-long project still validates its block
        server.save_script(10, "pt-BR", _content(fx, editorial=junk))
    with pytest.raises(ValidationError):  # and the rest of the script is still checked everywhere
        server.save_script(11, "pt-BR", {"scenes": [], "editorial": junk})
