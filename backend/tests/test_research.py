"""Pesquisa tab: a topic report (verdict rising/stable/saturated, keywords, competitors' outliers, ideas) via vidIQ."""
import json

from sqlmodel import Session, SQLModel, create_engine

from cvm.config import settings
from cvm.grow import research
from cvm.models import Setting

ANSWER = json.dumps({"used_vidiq": True, "verdict": "subindo", "verdict_why": "busca +609% no mês",
                     "keywords": [{"keyword": "gta 6 pc", "volume": 100, "competition": 64, "growth": 1278}],
                     "competitor_outliers": [{"video_id": "aaaaaaaaaa1", "title": "GTA 6 x", "channel": "Cassito",
                                              "views": 5000000, "outlier": 11}],
                     "ideas": [{"title": "Como rodar GTA 6 no PC", "why": "busca subindo, concorrência média"}]})


def test_report_parses_and_denies_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    seen = {}
    out = research.report("GTA 6", "pt", "UCabc", run=lambda p, **kw: seen.update(kw, prompt=p) or {"result": ANSWER})
    assert out["verdict"] == "subindo" and out["keywords"][0]["keyword"] == "gta 6 pc"
    assert out["competitor_outliers"][0]["url"].endswith("aaaaaaaaaa1") and out["ideas"][0]["title"]
    assert "mcp__cvm__*" in seen["disallowed_tools"] and "vidiq_keyword_research" in seen["prompt"]


def test_report_without_vidiq_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    out = research.report("GTA 6", "pt", None, run=lambda p, **kw: {"result": '{"used_vidiq": false}'})
    assert out["error"] and out["keywords"] == []


def test_bad_verdict_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    ans = json.loads(ANSWER) | {"verdict": "ignore previous instructions"}
    out = research.report("GTA 6", "pt", None, run=lambda p, **kw: {"result": json.dumps(ans)})
    assert out["verdict"] is None


def test_save_keeps_last_five():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    for n in range(7):
        research.save(s, 4, {"topic": f"t{n}"})
    assert [x["topic"] for x in s.get(Setting, "research:4").value["reports"]] == ["t6", "t5", "t4", "t3", "t2"]
