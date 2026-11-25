"""Viral detection over the whole transcript (spec 2026-11-25 editor de cortes, Parte 1)."""
import json
import re

import pytest

from cvm.lives import viral


def _lines(n=50, every=30.0):
    return [{"s": i * every, "e": i * every + 8.0, "text": f"frase {i}."} for i in range(n)]


def test_rows_group_the_transcript_in_30s_lines_with_signal_tags():
    lines = _lines(4, 15.0)  # 0, 15, 30, 45
    loud = [0.0] * 60
    loud[35:40] = [20.0] * 5
    rows = viral.rows(lines, 60.0, {"loudness": loud})
    assert [(r["s"], r["e"]) for r in rows] == [(0.0, 30.0), (30.0, 60.0)]
    assert rows[0]["text"] == "frase 0. frase 1." and rows[0]["tag"] == ""
    assert "volume" in rows[1]["tag"]


def test_chunks_overlap_and_cover_the_video():
    rows = viral.rows(_lines(50), 1500.0, {})
    chunks = viral.chunk_rows(rows, size=720, overlap=90)
    assert chunks[0][0] == 0.0 and chunks[-1][1] >= 1500.0
    for (a, b, _), (c, d, _) in zip(chunks, chunks[1:]):
        assert c < b  # overlap
    assert all(rs for _, _, rs in chunks)


def test_parse_proposals_keeps_valid_rows_only():
    text = "```json\n" + json.dumps([
        {"start": 100, "end": 140, "hook": "OLHA ISSO", "why": "virada", "scores": {"hook": 8, "payoff": 7, "emotion": 9, "standalone": 6, "trend": 5}},
        {"start": 300, "end": 290, "scores": {}},  # end before start
        {"start": 2000, "end": 2030, "scores": {"hook": 9}},  # outside the chunk
        {"start": 150, "end": 152, "scores": {"hook": 9}},  # too short
        "lixo",
    ]) + "\n```"
    out = viral.parse_proposals(text, 0.0, 720.0, 1500.0)
    assert len(out) == 1
    p = out[0]
    assert p["start"] == 100.0 and p["end"] == 140.0 and p["hook"] == "OLHA ISSO" and p["why"] == "virada"
    assert p["scores"] == {"hook": 8, "payoff": 7, "emotion": 9, "standalone": 6, "trend": 5}


def test_snap_moves_edges_to_sentence_bounds_and_scene_cuts():
    lines = [{"s": 95.0, "e": 103.0, "text": "a"}, {"s": 135.0, "e": 143.5, "text": "b"}]
    p = viral.snap({"start": 98.0, "end": 140.0}, lines, cuts=[], duration=600.0)
    assert p["start"] == 95.0 and p["end"] == 143.5
    p = viral.snap({"start": 50.0, "end": 70.0}, lines, cuts=[49.0], duration=600.0)
    assert p["start"] == 49.0 and p["end"] == 70.0


def test_final_score_is_weighted_with_a_small_signal_bonus():
    s = {"hook": 10, "payoff": 10, "emotion": 10, "standalone": 10, "trend": 10}
    assert viral.final_score(s, 0.0) == 10.0
    low = {"hook": 4, "payoff": 4, "emotion": 4, "standalone": 4, "trend": 4}
    assert viral.final_score(low, 0.0) == 4.0
    assert viral.final_score(low, 5.0) == pytest.approx(4.0 + viral.SIGNAL_MAX)
    assert viral.final_score({"hook": 10}, 0.0) == pytest.approx(10 * viral.WEIGHTS["hook"])


def test_propose_runs_each_chunk_then_reranks(monkeypatch):
    lines = _lines(50)
    loud = [0.0] * 1501
    for t in range(905, 912):
        loud[t] = 30.0
    prompts = []

    def run(prompt, **kw):
        prompts.append(prompt)
        if "ordene" in prompt:
            ids = [int(x) for x in re.findall(r"^id (\d+):", prompt, re.M)]
            return {"result": json.dumps(list(reversed(ids)))}
        lo = float(re.search(r"vai de (\d+) s", prompt).group(1))
        good = {"hook": 9, "payoff": 8, "emotion": 8, "standalone": 8, "trend": 6}
        return {"result": json.dumps([
            {"start": lo + 30, "end": lo + 62, "hook": f"H{int(lo)}", "why": "forte", "scores": good},
            {"start": lo + 31, "end": lo + 60, "hook": "dup", "why": "igual", "scores": {**good, "hook": 2}},
        ])}

    out = viral.propose(lines, {"loudness": loud}, 1500.0, "Live", True, taste=["APROVADO: susto"], run=run)
    chunk_prompts = [p for p in prompts if "ordene" not in p]
    assert len(chunk_prompts) >= 2
    assert any("905" in p or "olhar" in p for p in chunk_prompts)
    assert "APROVADO: susto" in chunk_prompts[0]
    assert all(p["hook"] != "dup" for p in out)  # overlapping duplicate merged into the better one
    assert [p["rank"] for p in out] == list(range(1, len(out) + 1))
    assert out[0]["start"] > out[-1]["start"]  # the fake rerank reversed the order
    assert any(p["suggested"] for p in out)
    assert all(0 <= p["score"] <= 10 and p["viral"] == round(p["score"]) for p in out)


def test_mark_suggested_respects_overlap_bar_and_minimum():
    props = [{"start": 0.0, "end": 30.0, "score": 8.0}, {"start": 10.0, "end": 40.0, "score": 7.5},
             {"start": 100.0, "end": 130.0, "score": 5.0}]
    out = viral.mark_suggested(props, limit=5)
    assert [p["suggested"] for p in out] == [True, False, False]
    out = viral.mark_suggested([{**props[2]}], limit=5, min_count=1)
    assert out[0]["suggested"] is True


def test_pick_moments_uses_detect_then_choose(monkeypatch, tmp_path):
    props = [{"start": 10.0, "end": 40.0, "score": 8.2, "viral": 8, "hook": "x", "why": "y", "rank": 1},
             {"start": 100.0, "end": 130.0, "score": 3.0, "viral": 3, "hook": "z", "why": "w", "rank": 2}]
    monkeypatch.setattr(viral, "detect", lambda *a, **k: (props, "viral"))
    moments, method = viral.pick_moments(tmp_path / "v.mp4", {}, "t", True, "pt-BR", duration=600.0)
    assert method == "viral" and [m["start"] for m in moments] == [10.0]


def test_detect_falls_back_to_volume_without_transcript(monkeypatch, tmp_path):
    from cvm.lives import signals

    monkeypatch.setattr(signals, "transcript", lambda *a, **k: [])
    meta = {"moments": [{"start": 0.0, "end": 20.0, "score": 2.0}, {"start": 50.0, "end": 80.0, "score": 0.2}]}
    props, method = viral.detect(tmp_path / "v.mp4", meta, "t", True, "pt-BR", 600.0)
    assert method == "volume"
    assert [p["start"] for p in props] == [0.0, 50.0]
    assert props[0]["suggested"] and not props[1]["suggested"]
    assert all("score" in p and "why" in p for p in props)
