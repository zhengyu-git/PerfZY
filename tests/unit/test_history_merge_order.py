import json

from test_session_merging import record
from perfzy.storage.merging import merge_sessions
from perfzy.storage.sessions import load_session, load_session_records


def test_new_merges_sort_by_merge_time_and_retain_capture_start(tmp_path):
    record(tmp_path, "old_a", started="2020-01-01T10:00:00+08:00")
    record(tmp_path, "old_b", started="2020-01-02T10:00:00+08:00")
    first = merge_sessions(tmp_path, ["old_a", "old_b"])
    second = merge_sessions(tmp_path, ["old_b", "old_a"])
    records = load_session_records(tmp_path)
    assert [row["id"] for row in records[:2]] == [second.parent.name, first.parent.name]
    assert records[0]["recordedAt"] != records[0]["startedAt"]
    assert load_session(tmp_path, second.parent.name)["started_at"] == "2020-01-02T10:00:00+08:00"
    assert records[0]["sortTimestamp"] > records[1]["sortTimestamp"]


def test_old_merge_summaries_gain_merge_sorting_without_rewriting_source_data(tmp_path):
    record(tmp_path, "a", started="2020-01-01T10:00:00+08:00")
    record(tmp_path, "b", started="2020-01-02T10:00:00+08:00")
    merged = merge_sessions(tmp_path, ["a", "b"])
    before = merged.read_bytes()
    summary_path = merged.parent / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for key in ("recordedAt", "sortTimestamp"):
        summary.pop(key)
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    records = load_session_records(tmp_path)
    assert records[0]["id"] == merged.parent.name
    assert records[0]["recordedAt"] != records[0]["startedAt"]
    assert merged.read_bytes() == before
    repaired = json.loads(summary_path.read_text(encoding="utf-8"))
    assert repaired["sortTimestamp"] == records[0]["sortTimestamp"]


def test_history_orders_merge_time_among_ordinary_capture_times(tmp_path):
    first = record(tmp_path, "a", started="2026-11-02T10:00:00+08:00")
    record(tmp_path, "b", started="2026-11-02T11:00:00+08:00")
    merged = merge_sessions(tmp_path, ["a", "b"])
    payload = json.loads(merged.read_text(encoding="utf-8"))
    payload["created_at"] = "2026-11-02T15:00:00+08:00"
    merged.write_text(json.dumps(payload), encoding="utf-8")
    records = load_session_records(tmp_path)
    assert [row["id"] for row in records] == [merged.parent.name, "b", "a"]
    assert records[0]["sortTimestamp"] > records[1]["sortTimestamp"]
    assert json.loads(first.read_text(encoding="utf-8"))["started_at"].startswith("2026-11-02T10")
