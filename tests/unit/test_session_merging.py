"""End-to-end storage, assets and downstream contracts for concatenation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

from PIL import Image
import pytest

from perfzy.reporting import _report_payload, generate_local_report
from perfzy.storage.merging import merge_sessions
from perfzy.storage.sessions import (LiveSessionWriter, delete_session, load_session,
                                    load_session_records, rename_session, save_session,
                                    save_session_from_spool)
from perfzy.studio.storage import frame_window


def record(root, identifier, *, duration=4.5, platform="windows", samples=None, shot=False,
           audited=False, fps=30, started="2026-11-01T10:00:00+08:00"):
    device = dict(id=platform + ":test", platform=platform, name="Test Phone", model="Model")
    target = dict(id=platform + ":test:game", name="Game")
    samples = samples if samples is not None else {"fps": [{"x": .25, "y": fps}, {"x": 2, "y": fps + 1}]}
    screenshots = []
    directory = root / "data" / "sessions" / identifier
    if shot:
        (directory / "screenshots").mkdir(parents=True)
        image = directory / "screenshots" / "same shot.jpg"
        Image.new("RGB", (24, 16), "red" if fps == 30 else "blue").save(image)
        screenshots = [dict(image=image.as_uri(), seconds=1.5, time="00:01")]
    settings = dict(sample_interval_ms=1000, build_label=identifier)
    if audited:
        writer = LiveSessionWriter(root, identifier, manifest={"capture_settings": settings})
        for x, value in ((.25, fps), (2, fps + 1)):
            writer.append(x, {"fps": value}, observed_elapsed={"fps": x - .1},
                          raw_values={"fps": value + 200}, requested=["fps"], collector_ms=3,
                          frame_events=[dict(x=x, frame_ms=16.7, source="PresentMon", pid=10,
                                             source_time_s=1000 + x, clock="source_clock")])
        return save_session_from_spool(root, device, target, writer.close(), screenshots,
                                       {"fps"}, identifier, [dict(id=1, name=identifier, start=.1, end=2)],
                                       started, duration, identifier)
    return save_session(root, device, target, samples, screenshots, set(samples), identifier,
                        [dict(id=1, name=identifier, start=.1, end=2)], started, duration,
                        identifier, capture_settings=settings)


def fingerprints(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_order_offsets_sparse_metrics_assets_and_source_independence(tmp_path):
    record(tmp_path, "older", duration=7.25, shot=True,
           samples={"fps": [{"x": .25, "y": 30}], "memory": [{"x": 5, "y": 100}]})
    record(tmp_path, "newer", duration=4.5, shot=True, fps=50, started="2026-11-02T10:00:00+08:00",
           samples={"fps": [{"x": 1, "y": 50}], "total_cpu": [{"x": 3, "y": 25}]})
    before = fingerprints(tmp_path)
    path = merge_sessions(tmp_path, ["newer", "older"], "自定义合并")
    merged = load_session(tmp_path, path.parent.name)
    assert merged["duration"] == 11.75
    assert merged["started_at"].startswith("2026-11-02")
    assert merged["samples"] == {
        "fps": [{"x": 1, "y": 50}, {"x": 4.75, "y": 30}],
        "memory": [{"x": 9.5, "y": 100}], "total_cpu": [{"x": 3, "y": 25}]}
    assert [s["seconds"] for s in merged["screenshots"]] == [1.5, 6]
    assert [s["time"] for s in merged["screenshots"]] == ["00:01", "00:06"]
    assert [s["id"] for s in merged["scenes"]] == [1, 2]
    assert merged["scenes"][1]["start"] == 4.6
    assert [part["id"] for part in merged["merged_from"]] == ["newer", "older"]
    assert [part["start"] for part in merged["merged_from"]] == [0, 4.5]
    assert [row["elapsed"] for row in merged["capture_settings_changes"]] == [0, 4.5]
    after = fingerprints(tmp_path)
    assert all(after[key] == value for key, value in before.items())
    summary = next(r for r in load_session_records(tmp_path) if r["id"] == path.parent.name)
    assert summary["name"] == "自定义合并" and summary["duration"] == "00:12"
    assert summary["mergeCount"] == 2 and summary["path"] == str(path)
    assert len({row["image"] for row in merged["screenshots"]}) == 2
    delete_session(tmp_path, "older")
    delete_session(tmp_path, "newer")
    report = _report_payload(deepcopy(load_session(tmp_path, path.parent.name)), "original")
    assert len(report["screenshots"]) == 2
    assert report["duration"] == 11.75
    assert sum(1 for row in report["samples"] if row.get("fps") is not None) == 2
    assert "顺序合并" in " ".join(report["notes"])
    assert generate_local_report(path, tmp_path / "reports").is_file()
    rename_session(tmp_path, path.parent.name, "合并后改名")
    assert load_session(tmp_path, path.parent.name)["name"] == "合并后改名"


def test_audit_quality_raw_frames_and_evidence_follow_each_segment(tmp_path):
    first = record(tmp_path, "a", duration=4, audited=True)
    second = record(tmp_path, "b", duration=5, fps=40, audited=True)
    for source in (first.parent, second.parent):
        (source / "evidence.json").write_text(json.dumps([dict(id=1, time=1, start=.5, end=2)]))
    before = fingerprints(tmp_path)
    path = merge_sessions(tmp_path, ["a", "b"])
    merged = load_session(tmp_path, path.parent.name)
    audit = [json.loads(line) for line in (path.parent / "audit.jsonl").read_text().splitlines()]
    assert [r["elapsed"] for r in audit] == [.25, 2, 4.25, 6]
    assert [r["observed_elapsed"]["fps"] for r in audit] == [.15, 1.9, 4.15, 5.9]
    assert audit[2]["raw_values"]["fps"] == 240
    assert merged["quality"]["samples"] == 4
    assert merged["quality"]["metrics"]["fps"]["valid"] == 4
    assert merged["quality"]["capture_duration_seconds"] == 9
    assert merged["quality"]["metrics"]["fps"]["coverage_estimate_seconds"] == 4
    assert not merged["quality"]["partial"]
    frames = frame_window(path.parent, 0, 9)["rows"]
    assert [row["x"] for row in frames] == [.25, 2, 4.25, 6]
    assert frames[2]["source_time_s"] == 1000.25  # retain the source clock for audit
    assert frames[2]["source_session_id"] == "b"
    evidence = json.loads((path.parent / "evidence.json").read_text())
    assert evidence[1] == dict(id=2, time=5, start=4.5, end=6, source_session_id="b")
    assert json.loads((path.parent / "frame-retention.json").read_text())["count"] == 4
    after = fingerprints(tmp_path)
    assert all(after[key] == value for key, value in before.items())


def test_quality_never_counts_coverage_past_a_source_boundary(tmp_path):
    record(tmp_path, "a", duration=2.1, audited=True)
    record(tmp_path, "b", duration=2.1, audited=True)
    merged = load_session(tmp_path, merge_sessions(tmp_path, ["a", "b"]).parent.name)
    assert merged["quality"]["metrics"]["fps"]["coverage_estimate_seconds"] == pytest.approx(2.4)


def test_legacy_and_nested_merges_do_not_invent_quality_for_unaudited_segments(tmp_path):
    record(tmp_path, "old")
    record(tmp_path, "new", audited=True)
    first = merge_sessions(tmp_path, ["old", "new"])
    second = merge_sessions(tmp_path, [first.parent.name, "old"])
    for path in (first, second):
        quality = load_session(tmp_path, path.parent.name)["quality"]
        assert quality["partial"]
        assert quality["metrics"]["fps"]["valid"] == 2
        assert quality["metrics"]["fps"]["coverage_estimate_percent"] is None


@pytest.mark.parametrize("platform", ["windows", "android", "ios"])
def test_all_platforms_keep_the_standard_report_contract(tmp_path, platform):
    record(tmp_path, "a", platform=platform)
    record(tmp_path, "b", platform=platform, fps=60)
    path = merge_sessions(tmp_path, ["a", "b"])
    merged = load_session(tmp_path, path.parent.name)
    report = _report_payload(deepcopy(merged))
    assert report["duration"] == 9
    assert len(report["samples"]) == 4
    assert not list((tmp_path / "data" / "sessions").glob("_merge_*"))


@pytest.mark.parametrize("identifiers", [[], ["a"], ["a", "a"], ["../escape", "a"], ["a", "missing"]])
def test_invalid_selections_never_change_existing_captures(tmp_path, identifiers):
    record(tmp_path, "a")
    before = fingerprints(tmp_path)
    with pytest.raises((ValueError, FileNotFoundError)):
        merge_sessions(tmp_path, identifiers)
    assert fingerprints(tmp_path) == before


@pytest.mark.parametrize("failure", ["platform", "screenshot", "frame_database"])
def test_failures_clean_staging_and_do_not_publish_partial_records(tmp_path, failure):
    record(tmp_path, "a")
    path = record(tmp_path, "b", platform="android" if failure == "platform" else "windows", shot=failure == "screenshot")
    if failure == "screenshot":
        (path.parent / "screenshots" / "same shot.jpg").unlink()
    elif failure == "frame_database":
        (path.parent / "frames.sqlite3").write_text("broken database")
    before = fingerprints(tmp_path)
    with pytest.raises((ValueError, OSError, sqlite3.Error)):
        merge_sessions(tmp_path, ["a", "b"])
    assert fingerprints(tmp_path) == before
    assert len(load_session_records(tmp_path)) == 2
    assert not list((tmp_path / "data" / "sessions").glob("_merge_*"))


def test_duration_covers_legacy_data_and_does_not_add_wall_clock_gaps(tmp_path):
    record(tmp_path, "a", duration=0, samples={"fps": [{"x": 10.5, "y": 30}]})
    record(tmp_path, "b", duration=3, started="2028-01-01T00:00:00+08:00")
    path = merge_sessions(tmp_path, ["a", "b"])
    merged = load_session(tmp_path, path.parent.name)
    assert merged["duration"] == 13.5
    assert merged["samples"]["fps"][1]["x"] == 10.75


def test_three_records_and_repeated_outputs_are_independent(tmp_path):
    for identifier in ("a", "b", "c"):
        record(tmp_path, identifier, duration=4)
    first = merge_sessions(tmp_path, ["c", "a", "b"])
    second = merge_sessions(tmp_path, ["c", "a", "b"])
    assert first != second
    merged = load_session(tmp_path, first.parent.name)
    assert merged["duration"] == 12
    assert [p["x"] for p in merged["samples"]["fps"]] == [.25, 2, 4.25, 6, 8.25, 10]
