"""Light AI calls (curation, viral scoring, vidIQ research) go through the chosen writer: Claude as today, Codex when
the user picked it, Codex at Claude's limit with the reserve on. vidIQ through Codex only when Codex has it logged in."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from cvm import claude_limit, db, engines
from cvm.config import settings
from cvm.scripting import agents, claude_runner, codex_runner

REAL_VIDIQ_READY = getattr(codex_runner, "vidiq_ready", None)


@pytest.fixture(autouse=True)
def mem_db(monkeypatch, tmp_path):
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    monkeypatch.setattr(db, "engine", e)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(codex_runner, "available", lambda: True)
    monkeypatch.setattr(codex_runner, "vidiq_ready", lambda: True)
    monkeypatch.setattr(agents, "_claude_ready", lambda: True)
    return e


def _writer(monkeypatch, w):
    monkeypatch.setattr(engines, "writer", lambda: w)


def _limit(*a, **k):
    raise claude_limit.ClaudeLimit(datetime.now(UTC) + timedelta(hours=1), "limit")


def _record(calls, name, result="ok"):
    def run(prompt, **kw):
        calls.append((name, kw))
        return {"result": result}
    return run


def test_run_light_uses_claude_by_default(monkeypatch):
    _writer(monkeypatch, "claude")
    calls = []
    monkeypatch.setattr(claude_runner, "run_claude", _record(calls, "claude"))
    monkeypatch.setattr(codex_runner, "run_codex", _record(calls, "codex"))
    res = agents.run_light("p", "curation", timeout=900, mcp="empty", allowed_tools=["A"], disallowed_tools=["B"])
    assert res["writer"] == "claude" and [c[0] for c in calls] == ["claude"]
    kw = calls[0][1]
    assert kw["timeout"] == 900 and kw["step"] == "curation"
    assert kw["allowed_tools"] == ["A"] and kw["disallowed_tools"] == ["B"]
    assert json.loads(kw["mcp_config"].read_text())["mcpServers"] == {}


def test_run_light_uses_codex_when_writer_codex(monkeypatch):
    _writer(monkeypatch, "codex")
    calls = []
    monkeypatch.setattr(claude_runner, "run_claude", _record(calls, "claude"))
    monkeypatch.setattr(codex_runner, "run_codex", _record(calls, "codex"))
    res = agents.run_light("p", "research", timeout=600, mcp="vidiq")
    assert res["writer"] == "codex" and [c[0] for c in calls] == ["codex"]
    assert calls[0][1]["mcp"] == "vidiq" and calls[0][1]["timeout"] == 600


def test_run_light_codex_writer_without_mcp_runs_codex_with_no_mcp(monkeypatch):
    _writer(monkeypatch, "codex")
    calls = []
    monkeypatch.setattr(codex_runner, "run_codex", _record(calls, "codex"))
    agents.run_light("p", "viral", timeout=600)
    assert calls[0][1]["mcp"] == "empty"


def test_run_light_falls_back_to_codex_at_claude_limit(monkeypatch):
    _writer(monkeypatch, "claude")
    calls = []
    monkeypatch.setattr(claude_runner, "run_claude", _limit)
    monkeypatch.setattr(codex_runner, "run_codex", _record(calls, "codex"))
    res = agents.run_light("p", "viral", timeout=600, mcp="empty")
    assert res["writer"] == "codex" and calls[0][1]["mcp"] == "empty"


def test_run_light_at_claude_limit_without_codex_raises_the_limit(monkeypatch):
    _writer(monkeypatch, "claude")
    monkeypatch.setattr(claude_runner, "run_claude", _limit)
    monkeypatch.setattr(codex_runner, "available", lambda: False)
    with pytest.raises(claude_limit.ClaudeLimit):
        agents.run_light("p", "curation", mcp="empty")


def test_codex_writer_at_codex_limit_hands_to_claude(monkeypatch):
    _writer(monkeypatch, "codex")
    calls = []
    monkeypatch.setattr(codex_runner, "run_codex", _limit)
    monkeypatch.setattr(claude_runner, "run_claude", _record(calls, "claude"))
    assert agents.run_light("p", "curation", mcp="empty")["writer"] == "claude"


def test_vidiq_not_ready_on_codex_falls_back_to_claude(monkeypatch):
    _writer(monkeypatch, "codex")
    calls = []
    monkeypatch.setattr(codex_runner, "vidiq_ready", lambda: False)
    monkeypatch.setattr(claude_runner, "run_claude", _record(calls, "claude"))
    monkeypatch.setattr(codex_runner, "run_codex", _record(calls, "codex"))
    res = agents.run_light("p", "research", timeout=600, mcp="vidiq")
    assert res["writer"] == "claude" and [c[0] for c in calls] == ["claude"]
    assert list(json.loads(calls[0][1]["mcp_config"].read_text())["mcpServers"]) == ["vidiq"]


def test_vidiq_not_ready_and_no_claude_raises_vidiq_unavailable(monkeypatch):
    _writer(monkeypatch, "codex")
    monkeypatch.setattr(codex_runner, "vidiq_ready", lambda: False)
    monkeypatch.setattr(agents, "_claude_ready", lambda: False)
    monkeypatch.setattr(codex_runner, "run_codex", _record([], "codex"))
    with pytest.raises(agents.VidiqUnavailable):
        agents.run_light("p", "research", mcp="vidiq")


def test_vidiq_not_ready_at_claude_limit_does_not_go_to_codex(monkeypatch):
    _writer(monkeypatch, "claude")
    calls = []
    monkeypatch.setattr(codex_runner, "vidiq_ready", lambda: False)
    monkeypatch.setattr(claude_runner, "run_claude", _limit)
    monkeypatch.setattr(codex_runner, "run_codex", _record(calls, "codex"))
    with pytest.raises(claude_limit.ClaudeLimit):
        agents.run_light("p", "research", mcp="vidiq")
    assert calls == []


def test_vidiq_run_at_claude_limit_with_writer_claude_never_goes_to_codex(monkeypatch):
    """Writer "claude" (the owner): a vidIQ run at Claude's limit raises the limit as before (the caller falls back);
    it never moves to Codex, where it would spend vidIQ credits, even with vidIQ logged in on Codex."""
    _writer(monkeypatch, "claude")
    calls = []
    monkeypatch.setattr(codex_runner, "vidiq_ready", lambda: True)
    monkeypatch.setattr(claude_runner, "run_claude", _limit)
    monkeypatch.setattr(codex_runner, "run_codex", _record(calls, "codex"))
    with pytest.raises(claude_limit.ClaudeLimit):
        agents.run_light("p", "research", mcp="vidiq", allowed_tools=["mcp__vidiq__vidiq_outliers"])
    assert calls == []


def test_vidiq_caller_falls_back_at_claude_limit_with_writer_claude(monkeypatch):
    from cvm.grow import viral_search

    _writer(monkeypatch, "claude")
    calls = []
    monkeypatch.setattr(claude_runner, "run_claude", _limit)
    monkeypatch.setattr(codex_runner, "run_codex", _record(calls, "codex"))
    out = viral_search.search("GTA", fallback=lambda *a: {"items": [{"video_id": "x"}]})
    assert out["source"] == "yt-dlp" and calls == []


def test_vidiq_run_on_codex_when_writer_codex(monkeypatch):
    _writer(monkeypatch, "codex")
    calls = []
    monkeypatch.setattr(codex_runner, "run_codex", _record(calls, "codex"))
    assert agents.run_light("p", "research", mcp="vidiq")["writer"] == "codex"


class _Slot:
    def __init__(self, free=True):
        self.free, self.held, self.taken = free, False, 0

    def __call__(self):
        from contextlib import contextmanager

        @contextmanager
        def cm():
            self.taken += 1
            self.held = self.free
            try:
                yield self.free
            finally:
                self.held = False
        return cm()


def test_claude_slot_is_not_taken_on_the_codex_path(monkeypatch):
    _writer(monkeypatch, "codex")
    slot, seen = _Slot(), []

    def codex(prompt, **kw):
        seen.append(slot.held)
        return {"result": "ok"}
    monkeypatch.setattr(codex_runner, "run_codex", codex)
    assert agents.run_light("p", "curation", mcp="empty", claude_slot=slot)["writer"] == "codex"
    assert slot.taken == 0 and seen == [False]


def test_claude_slot_released_before_codex_at_claude_limit(monkeypatch):
    _writer(monkeypatch, "claude")
    slot, seen = _Slot(), []

    def codex(prompt, **kw):
        seen.append(slot.held)
        return {"result": "ok"}
    monkeypatch.setattr(claude_runner, "run_claude", _limit)
    monkeypatch.setattr(codex_runner, "run_codex", codex)
    assert agents.run_light("p", "curation", mcp="empty", claude_slot=slot)["writer"] == "codex"
    assert slot.taken == 1 and seen == [False]


def test_claude_slot_held_on_the_claude_path_and_busy_raises(monkeypatch):
    _writer(monkeypatch, "claude")
    slot, seen = _Slot(), []

    def claude(prompt, **kw):
        seen.append(slot.held)
        return {"result": "ok"}
    monkeypatch.setattr(claude_runner, "run_claude", claude)
    assert agents.run_light("p", "curation", mcp="empty", claude_slot=slot)["writer"] == "claude"
    assert seen == [True] and not slot.held
    with pytest.raises(agents.ClaudeBusy):
        agents.run_light("p", "curation", mcp="empty", claude_slot=_Slot(free=False))


# ---------------- codex argv ----------------

class _Proc:
    returncode, pid = 0, 4242

    def __init__(self, cmd, **kw):
        _Proc.cmd = cmd

    def communicate(self, timeout=None):
        return ('{"type":"item.completed","item":{"type":"agent_message","text":"{}"}}\n', "")


@pytest.fixture
def fake_codex(monkeypatch, tmp_path):
    monkeypatch.setattr(codex_runner.subprocess, "Popen", _Proc)
    monkeypatch.setattr(codex_runner, "_user_servers", lambda exe: ["node_repl", "computer-use", "vidiq"])
    monkeypatch.setattr(codex_runner, "_features", _fake_features(FEATURES))
    monkeypatch.setattr(claude_runner, "RUN_DIR", tmp_path / "runs")
    return _Proc


# what codex 0.169 reports on this Mac (the parts that matter), as {name: (stage, enabled)}
FEATURES = {f: ("stable", True) for f in (
    "plugins", "apps", "hooks", "shell_tool", "unified_exec", "browser_use", "browser_use_external",
    "browser_use_full_cdp_access", "computer_use", "in_app_browser", "image_generation", "multi_agent",
    "skill_mcp_dependency_install", "code_mode_host", "fast_mode", "guardian_approval")}
FEATURES.update({"collaboration_modes": ("removed", True), "web_search_cached": ("deprecated", False)})


def _fake_features(known, sticky=("unified_exec",)):
    """`codex features list [--disable …]`: what --disable switches off (codex 0.169 keeps unified_exec on)."""
    def features(exe, *args):
        off = {args[i + 1] for i, a in enumerate(args) if a == "--disable"} - set(sticky)
        return {f: (stage, on and f not in off) for f, (stage, on) in known.items()}
    return features


def _overrides(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]


def test_vidiq_mcp_passed_to_codex(fake_codex):
    codex_runner.run_codex("p", timeout=60, mcp="vidiq",
                           allowed_tools=["mcp__vidiq__vidiq_outliers", "mcp__vidiq__vidiq_trending_videos"])
    cmd, cfg = fake_codex.cmd, _overrides(fake_codex.cmd)
    assert 'mcp_servers.vidiq.url="https://mcp.vidiq.com/mcp"' in cfg
    assert 'mcp_servers.vidiq.enabled_tools=["vidiq_outliers", "vidiq_trending_videos"]' in cfg
    assert not any(c.startswith("mcp_servers.cvm") for c in cfg)  # no cvm server
    assert "mcp_servers.node_repl.enabled=false" in cfg and "mcp_servers.computer-use.enabled=false" in cfg
    assert "mcp_servers.vidiq.enabled=false" not in cfg
    assert 'web_search="disabled"' in cfg
    disabled = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--disable"]
    for feat in ("plugins", "apps", "hooks", "shell_tool", "unified_exec", "browser_use", "browser_use_external",
                 "browser_use_full_cdp_access", "computer_use", "in_app_browser", "image_generation", "multi_agent",
                 "skill_mcp_dependency_install", "code_mode_host"):
        assert feat in disabled
    assert "browser_annotation_api" not in disabled  # unknown to this Codex: `--disable` would fail the run
    assert cmd[-1] == "p"


def test_codex_light_run_refuses_an_unknown_enabled_feature(fake_codex, monkeypatch):
    monkeypatch.setattr(codex_runner, "_features", _fake_features({**FEATURES, "teleport_tool": ("stable", True)}))
    with pytest.raises(RuntimeError, match="teleport_tool"):
        codex_runner.run_codex("p", timeout=60, mcp="empty")


def test_codex_light_run_refuses_when_a_disable_does_not_take(fake_codex, monkeypatch):
    monkeypatch.setattr(codex_runner, "_features", _fake_features(FEATURES, sticky=("unified_exec", "browser_use")))
    with pytest.raises(RuntimeError, match="browser_use"):
        codex_runner.run_codex("p", timeout=60, mcp="empty")
    monkeypatch.setattr(codex_runner, "_features", _fake_features(FEATURES, sticky=("unified_exec", "shell_tool")))
    with pytest.raises(RuntimeError, match="shell_tool"):  # unified_exec is accepted only with the shell off
        codex_runner.run_codex("p", timeout=60, mcp="empty")


def test_codex_features_list_is_parsed(monkeypatch):
    class R:
        returncode, stderr = 0, ""
        stdout = "browser_use                              stable             true\n" \
                 "agent_message_board_mcp                  under development  false\n"
    monkeypatch.setattr(codex_runner.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(codex_runner, "_features_seen", {})
    assert codex_runner._features("codex") == {"browser_use": ("stable", True),
                                               "agent_message_board_mcp": ("under development", False)}


def test_vidiq_run_without_an_allow_list_is_refused(fake_codex):
    with pytest.raises(ValueError):
        codex_runner.run_codex("p", timeout=60, mcp="vidiq")
    with pytest.raises(ValueError):
        codex_runner.run_codex("p", timeout=60, mcp="vidiq", allowed_tools=["WebSearch"])


@pytest.mark.parametrize("tool", ["vidiq_update_video", "vidiq_social_post_publish", "vidiq_reply_to_comment",
                                  "vidiq_video_upload", "vidiq_bookmarks_save", "vidiq_*",
                                  "vidiq_generate_comment_replies", "vidiq_video_studio_start", "vidiq_generate_video",
                                  "vidiq_generate_thumbnail", "vidiq_generate_music", "vidiq_image_generate",
                                  "vidiq_voiceover_generate", "vidiq_youtube_channel_audit", "vidiq_brand_new_tool"])
def test_vidiq_write_tools_are_never_enabled(fake_codex, tool):
    with pytest.raises(ValueError):
        codex_runner.run_codex("p", timeout=60, mcp="vidiq", allowed_tools=["mcp__vidiq__vidiq_outliers", f"mcp__vidiq__{tool}"])


def test_light_callers_vidiq_tools_are_read_only():
    from cvm.grow import research, vidiq_review, vidiq_stats, viral_search

    union = set()
    for tools in (research.TOOLS, vidiq_review.TOOLS, vidiq_stats.TOOLS, viral_search.TOOLS):
        assert codex_runner._vidiq_tools(tools)
        union |= {t.removeprefix("mcp__vidiq__") for t in tools}
    assert union == codex_runner.VIDIQ_READ_ONLY  # the allow set is exactly what the callers use, nothing more


def test_codex_light_web_search_follows_the_callers_tools(fake_codex):
    from cvm import curation
    from cvm.grow import viral_search

    codex_runner.run_codex("p", timeout=60, mcp="empty", allowed_tools=curation.ALLOWED, disallowed_tools=curation.DENY)
    assert 'web_search="live"' in _overrides(fake_codex.cmd)
    codex_runner.run_codex("p", timeout=60, mcp="empty", disallowed_tools=viral_search.DENY)
    assert 'web_search="disabled"' in _overrides(fake_codex.cmd)


def test_codex_empty_mcp_has_no_servers(fake_codex):
    codex_runner.run_codex("p", timeout=60, mcp="empty")
    cfg = _overrides(fake_codex.cmd)
    assert not any(c.startswith(("mcp_servers.cvm", "mcp_servers.vidiq.url")) for c in cfg)
    assert "mcp_servers.vidiq.enabled=false" in cfg and "mcp_servers.node_repl.enabled=false" in cfg
    assert 'web_search="disabled"' in cfg


def test_codex_default_keeps_the_cvm_server(fake_codex):
    codex_runner.run_codex("p", timeout=60)
    cfg = _overrides(fake_codex.cmd)
    assert 'mcp_servers.cvm.command="uv"' in cfg and 'web_search="live"' in cfg
    assert "--disable" not in fake_codex.cmd


def test_codex_light_run_refuses_unsafe_server_names(fake_codex, monkeypatch):
    monkeypatch.setattr(codex_runner, "_user_servers", lambda exe: ['bad"name'])
    with pytest.raises(RuntimeError):
        codex_runner.run_codex("p", timeout=60, mcp="empty")


@pytest.mark.parametrize("status,ready", [("o_auth", True), ("bearer_token", True), ("not_logged_in", False),
                                          ("unsupported", False)])
def test_vidiq_ready_reads_codex_mcp_list(monkeypatch, status, ready):
    seen = {}

    class R:
        returncode = 0
        stdout = json.dumps([{"name": "node_repl", "auth_status": "unsupported"},
                             {"name": "vidiq", "enabled": True, "auth_status": status}])
        stderr = ""

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return R()
    monkeypatch.setattr(codex_runner, "installed", lambda: True)
    monkeypatch.setattr(codex_runner.subprocess, "run", run)
    assert REAL_VIDIQ_READY() is ready
    assert seen["cmd"][1:4] == ["mcp", "list", "--json"]
    assert 'mcp_servers.vidiq.url="https://mcp.vidiq.com/mcp"' in seen["cmd"]


def test_vidiq_ready_false_when_codex_missing_or_broken(monkeypatch):
    monkeypatch.setattr(codex_runner, "installed", lambda: False)
    assert REAL_VIDIQ_READY() is False
    monkeypatch.setattr(codex_runner, "installed", lambda: True)
    monkeypatch.setattr(codex_runner.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert REAL_VIDIQ_READY() is False


# ---------------- the six callers, writer "claude": run_claude gets exactly what it got before ----------------

def _capture_claude(monkeypatch, result="{}"):
    seen = {}

    def run(prompt, **kw):
        seen.update(kw)
        return {"result": result}
    monkeypatch.setattr(claude_runner, "run_claude", run)
    return seen


def _servers(path):
    return json.loads(path.read_text())["mcpServers"]


def test_callers_on_claude_keep_their_run_claude_args(monkeypatch):
    from cvm.grow import research, vidiq_review, vidiq_stats, viral_search
    from cvm.lives import viral
    from cvm.models import Channel, Render

    _writer(monkeypatch, "claude")
    vs_deny = viral_search.DENY

    seen = _capture_claude(monkeypatch, "[]")
    viral.score([{"start": 0.0, "end": 10.0, "text": "x"}], "t", False)
    assert seen["timeout"] == 600 and seen["step"] == "viral" and _servers(seen["mcp_config"]) == {}
    assert seen["disallowed_tools"] == vs_deny and seen.get("allowed_tools") is None

    for call, timeout, tools in (
        (lambda: viral_search.search("GTA", fallback=lambda *a: {"items": []}), 900, viral_search.TOOLS),
        (lambda: research.report("GTA"), 900, research.TOOLS),
        (lambda: vidiq_stats.fetch("UCx"), 600, vidiq_stats.TOOLS),
        (lambda: vidiq_review.review(Render(id=1, profile="youtube", seo={"title": "t"}), Channel(id=1, name="c")),
         600, vidiq_review.TOOLS),
    ):
        seen = _capture_claude(monkeypatch)
        call()
        assert seen["timeout"] == timeout and seen["step"] == "research"
        assert list(_servers(seen["mcp_config"])) == ["vidiq"]
        assert seen["allowed_tools"] == tools and seen["disallowed_tools"] == vs_deny


def test_curation_on_claude_keeps_its_run_claude_args(monkeypatch):
    from cvm import curation

    _writer(monkeypatch, "claude")
    seen = _capture_claude(monkeypatch, '{"ok": true, "problems": []}')
    from sqlmodel import Session

    from cvm.models import Project, Script

    with Session(db.engine) as s:
        p = Project(title="t", topic="t", style="dark", languages=["pt-BR"])
        s.add(p)
        s.commit()
        s.refresh(p)
        sc = Script(project_id=p.id, language="pt-BR", content={"scenes": [{"index": 0, "narration": "oi"}]})
        s.add(sc)
        s.commit()
        check = curation.check_content(s, p, {"pt-BR": sc}, [])
    assert check["ok"] is True
    assert seen["timeout"] == 900 and seen["step"] == "curation" and _servers(seen["mcp_config"]) == {}
    assert seen["allowed_tools"] == curation.ALLOWED and seen["disallowed_tools"] == curation.DENY


def _curation_project(s):
    from cvm.models import Project, Script

    p = Project(title="t", topic="t", style="dark", languages=["pt-BR"])
    s.add(p)
    s.commit()
    s.refresh(p)
    sc = Script(project_id=p.id, language="pt-BR", content={"scenes": [{"index": 0, "narration": "oi"}]})
    s.add(sc)
    s.commit()
    return p, sc


@pytest.mark.parametrize("codex_ok", [True, False])
def test_curation_with_claude_limit_recorded_goes_to_codex_or_skips(monkeypatch, codex_ok):
    from sqlmodel import Session

    from cvm import curation

    _writer(monkeypatch, "claude")
    calls = []
    monkeypatch.setattr(claude_runner, "run_claude", _limit)
    monkeypatch.setattr(codex_runner, "run_codex", _record(calls, "codex", '{"ok": true, "problems": []}'))
    monkeypatch.setattr(codex_runner, "available", lambda: codex_ok)
    with Session(db.engine) as s:
        claude_limit.set_limit(s, datetime.now(UTC) + timedelta(hours=2), "limit")
        p, sc = _curation_project(s)
        check = curation.check_content(s, p, {"pt-BR": sc}, [])
    if codex_ok:
        assert check["ok"] is True and [c[0] for c in calls] == ["codex"]
        assert calls[0][1]["mcp"] == "empty" and calls[0][1]["allowed_tools"] == curation.ALLOWED
    else:
        assert check.get("skipped") and calls == []


def test_caller_degrades_like_today_when_vidiq_unavailable(monkeypatch):
    """Codex-only install without vidIQ logged in on Codex: the Cortes search still answers through yt-dlp."""
    from cvm.grow import viral_search

    _writer(monkeypatch, "codex")
    monkeypatch.setattr(codex_runner, "vidiq_ready", lambda: False)
    monkeypatch.setattr(agents, "_claude_ready", lambda: False)
    out = viral_search.search("GTA", fallback=lambda *a: {"items": [{"video_id": "x"}]})
    assert out["source"] == "yt-dlp"


@pytest.mark.parametrize("writer", ["codex", "claude"])
def test_curation_with_every_claude_slot_busy(monkeypatch, writer):
    """Codex check: runs even when every Claude slot is busy (it never took one). Claude check: skipped as before."""
    from sqlmodel import Session

    from cvm import curation

    _writer(monkeypatch, writer)
    calls = []
    monkeypatch.setattr(claude_runner, "run_claude", _record(calls, "claude", '{"ok": true, "problems": []}'))
    monkeypatch.setattr(codex_runner, "run_codex", _record(calls, "codex", '{"ok": true, "problems": []}'))
    busy = _Slot(free=False)
    with Session(db.engine) as s:
        p, sc = _curation_project(s)
        check = curation.check_content(s, p, {"pt-BR": sc}, [], claude_slot=busy)
    if writer == "codex":
        assert check["ok"] is True and [c[0] for c in calls] == ["codex"] and busy.taken == 0
    else:
        assert check.get("skipped") and "ocupado" in check["detail"] and calls == []
