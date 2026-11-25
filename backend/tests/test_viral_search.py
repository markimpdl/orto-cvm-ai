"""Viral video suggestions for a topic: vidIQ through a research claude -p, yt-dlp search as the free fallback."""
import json
import subprocess
import time

from sqlmodel import Session, SQLModel, create_engine

from cvm.grow import viral_search as vs
from cvm.models import Setting

VIDIQ_ANSWER = json.dumps({"used_vidiq": True,
    "items": [{"video_id": "aaaaaaaaaa1", "title": "GTA 6 secreto", "channel": "Canal", "views": 300000, "vph": 9000,
               "outlier": 12.5, "duration": 40, "published": "2026-11-20", "why": "curiosidade", "cut_idea": "o carro raro"},
              {"video_id": "bbbbbbbbbb2", "title": "GTA 6 meme", "channel": "Outro", "views": 900000, "vph": 20000,
               "outlier": 3, "duration": 12, "published": "2026-11-19", "why": "humor", "cut_idea": "a reação"}],
    "patterns": [{"format": "detalhes e segredos", "example": "GTA 6 – detalhes", "views": 1460000, "why": "curiosidade"}]})


def test_search_parses_vidiq_answer(tmp_path, monkeypatch):
    from cvm.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    calls = []

    def run(prompt, **kw):
        calls.append(kw)
        return {"result": "```json\n" + VIDIQ_ANSWER + "\n```"}
    out = vs.search("GTA 6", "pt", run=run, fallback=lambda *a: {"items": []})
    assert out["source"] == "vidiq" and [i["video_id"] for i in out["items"]] == ["bbbbbbbbbb2", "aaaaaaaaaa1"]  # by vph
    assert out["items"][0]["url"] == "https://www.youtube.com/watch?v=bbbbbbbbbb2"
    assert out["patterns"][0]["format"] == "detalhes e segredos"
    assert calls[0]["mcp_config"].exists() and any("vidiq" in t for t in calls[0]["allowed_tools"])


def test_search_falls_back_when_claude_fails(tmp_path, monkeypatch):
    from cvm.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    def run(prompt, **kw):
        raise RuntimeError("claude exited 1")
    out = vs.search("GTA 6", "pt", run=run, fallback=lambda topic, lang: {"items": [{"video_id": "x", "vph": 1.0}]})
    assert out["source"] == "yt-dlp" and out["items"][0]["video_id"] == "x"


def test_ytdlp_search_computes_vph():
    now = time.time()
    lines = [{"id": "ccccccccccc", "title": "GTA 6 x", "channel": "C", "view_count": 10000, "timestamp": now - 10 * 3600,
              "duration": 30},
             {"id": "ddddddddddd", "title": "GTA 6 y", "channel": "D", "view_count": 500, "duration": 600}]
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "\n".join(json.dumps(x) for x in lines), "")  # noqa: E731
    out = vs.ytdlp_search("GTA 6", "pt", run=run)
    a = next(i for i in out["items"] if i["video_id"] == "ccccccccccc")
    assert 900 < a["vph"] < 1100
    assert next(i for i in out["items"] if i["video_id"] == "ddddddddddd")["vph"] is None


def test_save_keeps_last_five():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    for n in range(7):
        vs.save(s, 4, {"topic": f"t{n}", "items": []})
    saved = s.get(Setting, "viral:4").value["searches"]
    assert [x["topic"] for x in saved] == ["t6", "t5", "t4", "t3", "t2"]
