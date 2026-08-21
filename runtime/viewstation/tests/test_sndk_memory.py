"""The Memory view's two contracts: the overview is a read-only inventory of the
SNDK RAG store, and a query reaches the real history CLI only through validated
flags (user input reaching a subprocess — every value is checked, unknown
commands such as `rollup --force` are refused)."""
import json

import server
import snapshot


def test_memory_overview_reads_the_store(tmp_path, monkeypatch):
    rag = tmp_path / "sndk_rag"
    (rag / "slices").mkdir(parents=True)
    rows = [
        {"kind": "slice", "rag_v": 2, "meta": {"date": "2026-08-19", "time": "10:12", "vector": "down"}, "narrative": "first"},
        {"kind": "slice", "rag_v": 2, "meta": {"date": "2026-08-19", "time": "14:21", "vector": "up"}, "narrative": "last line"},
        {"kind": "not-a-slice"},
    ]
    (rag / "slices" / "2026-08-19.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (rag / "summaries.jsonl").write_text(json.dumps({"kind": "day_summary", "date": "2026-08-05", "rag_v": 2}) + "\n")
    (rag / "terrain.json").write_text(json.dumps({"built": "2026-08-03", "sessions": 4, "rag_v": 1, "narrative": "1100 magnet"}))
    monkeypatch.setattr(snapshot, "_RAG_DIR", rag)
    ov = snapshot.sndk_memory_overview()
    assert ov["slices_total"] == 2 and len(ov["days"]) == 1
    d = ov["days"][0]
    assert d["date"] == "2026-08-19" and d["first"] == "10:12" and d["last"] == "14:21"
    assert d["vectors"] == {"up": 1, "down": 1, "none": 0} and d["last_line"] == "last line"
    assert ov["summaries"] == {"n": 1, "first": "2026-08-05", "last": "2026-08-05", "rag_v": [2]}
    assert ov["terrain"]["built"] == "2026-08-03" and ov["terrain"]["sessions"] == 4


def test_memory_overview_survives_an_empty_store(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "_RAG_DIR", tmp_path / "nope")
    ov = snapshot.sndk_memory_overview()
    assert ov["days"] == [] and ov["slices_total"] == 0 and ov["terrain"] is None


def test_memory_args_build_only_documented_flags():
    assert server._memory_args({"tier": ["slices"], "date": ["2026-08-21"], "limit": ["5"]}) == \
        ["query", "--tier", "slices", "--date", "2026-08-21", "--limit", "5"]
    assert server._memory_args({"tier": ["days"], "text": ["rejected the call wall"], "near": ["1600"], "min_move": ["-5"]}) == \
        ["query", "--tier", "days", "--near-strike", "1600", "--min-move", "-5", "--text", "rejected the call wall"]
    assert server._memory_args({"tier": ["month"]}) == ["query", "--tier", "month"]
    assert server._memory_args({"kind": ["series"], "date": ["2026-08-21"], "step": ["10"], "strike": ["1600"]}) == \
        ["series", "--date", "2026-08-21", "--step", "10", "--strike", "1600"]
    assert server._memory_args({}) == ["query", "--tier", "slices"]          # defaults to today's moments


def test_memory_args_refuse_bad_input():
    assert server._memory_args({"tier": ["x"]}) is None
    assert server._memory_args({"date": ["21-08-2026"]}) is None
    assert server._memory_args({"from": ["9am"]}) is None
    assert server._memory_args({"near": ["abc"]}) is None
    assert server._memory_args({"limit": ["999"]}) is None
    assert server._memory_args({"text": ["x" * 300]}) is None
    assert server._memory_args({"kind": ["rollup"]}) is None                  # never the write command
    assert server._memory_args({"kind": ["series"], "step": ["0"]}) is None


def test_memory_query_rejects_before_touching_the_cli(monkeypatch):
    called = []
    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: called.append(a))
    assert server._memory_query({"tier": ["nope"]}) == {"error": "bad query"}
    assert called == []
