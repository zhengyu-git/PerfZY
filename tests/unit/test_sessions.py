"""Guards on local session storage.

Recordings are the only durable output of the tool, and history listing relies on
a small sidecar staying in step with the full session file.
"""

import json

import pytest

from perfzy.storage.sessions import (DISCARD_MARKER_NAME, LIVE_SAMPLES_DIR,
                                              METADATA_NAME, SUMMARY_NAME,
                                              LiveSessionWriter, delete_session,
                                              discard_incomplete_session, load_session,
                                              load_session_records, new_session_id,
                                              normalize_session_name, recover_incomplete_sessions,
                                              rename_session, save_session,
                                              save_session_from_spool)


DEVICE = {"id": "android:SERIAL", "name": "Test Phone", "platform": "android",
          "model": "TP-1", "os": "Android 15"}


@pytest.mark.parametrize("spooled", [False, True])
@pytest.mark.parametrize("reason", ["用户停止采集", "采样中断：进程退出", ""])
def test_end_reason_survives_both_save_paths(tmp_path, spooled, reason):
    if spooled:
        writer = LiveSessionWriter(tmp_path, "reason-test", manifest={})
        writer.append(1.0, {"fps": 30})
        path = save_session_from_spool(tmp_path, DEVICE, {}, writer.close(), [],
                                       {"fps"}, "reason-test", end_reason=reason)
    else:
        path = save_session(tmp_path, DEVICE, {}, {"fps": [{"x": 1, "y": 30}]},
                            [], {"fps"}, end_reason=reason)
    assert json.loads(path.read_text(encoding="utf-8"))["end_reason"] == reason

TARGET = {"id": "android:SERIAL:com.example.game", "name": "Example Game", "pid": 0}
SAMPLES = {"fps": [{"x": 1.0, "y": 60.0}, {"x": 2.0, "y": 59.0}],
           "app_cpu": [{"x": 1.0, "y": 20.0}, {"x": 2.0, "y": 22.0}]}
SCENES = [{"id": 1, "name": "战斗", "start": 1.0, "end": 2.0, "color": "#4269d4"}]


def write_session(root, session_id="20260101_120000_abcdef", name="第一次采集",
                  samples=None, screenshots=None):
    return save_session(root, DEVICE, TARGET, samples if samples is not None else SAMPLES,
                        screenshots or [], {"fps", "app_cpu"}, session_id, SCENES,
                        "2026-01-01T12:00:00+08:00", 2.0, name)


def test_session_round_trip(tmp_path):
    path = write_session(tmp_path)
    assert path.is_file()

    payload = load_session(tmp_path, "20260101_120000_abcdef")
    assert payload["name"] == "第一次采集"
    assert payload["samples"]["fps"] == SAMPLES["fps"]
    assert payload["scenes"] == SCENES
    assert payload["duration"] == pytest.approx(2.0)
    assert payload["device"]["model"] == "TP-1"
    assert payload["session_path"] == str(path)


def test_loading_history_hides_legacy_frame_timestamp_sentinels(tmp_path):
    write_session(tmp_path, samples={
        "frame_time": [{"x": 1.0, "y": 32.0},
                       {"x": 2.0, "y": 298_652_166.8}],
        "frame_time_p95": [{"x": 2.0, "y": 298_652_166.8}],
        "dropped_frames": [{"x": 2.0, "y": 35_838_262.0}],
    })

    payload = load_session(tmp_path, "20260101_120000_abcdef")

    assert payload["samples"]["frame_time"] == [{"x": 1.0, "y": 32.0}]
    assert "frame_time_p95" not in payload["samples"]
    assert "dropped_frames" not in payload["samples"]


def test_live_spool_finalizes_to_the_existing_session_format_without_loading_all_points(tmp_path):
    writer = LiveSessionWriter(tmp_path, "streamed")
    writer.append(1.0, {"fps": 60.0, "app_cpu": 20.0})
    writer.append(2.0, {"fps": 59.0, "app_cpu": 22.0})
    files = writer.close()

    path = save_session_from_spool(
        tmp_path, DEVICE, TARGET, files, [], {"fps", "app_cpu"}, "streamed",
        SCENES, "2026-01-01T12:00:00+08:00", 2.0, "流式采集")
    payload = load_session(tmp_path, "streamed")

    assert path.is_file()
    assert payload["samples"] == SAMPLES
    assert payload["name"] == "流式采集"
    assert not (path.parent / LIVE_SAMPLES_DIR).exists()


def test_live_spool_rejects_non_finite_and_non_numeric_points(tmp_path):
    writer = LiveSessionWriter(tmp_path, "finite-only")
    writer.append(1.0, {"fps": float("nan"), "app_cpu": float("inf"),
                        "total_cpu": "bad", "memory": 128.0})
    files = writer.close()

    assert set(files) == {"memory"}
    assert json.loads(files["memory"].read_text(encoding="utf-8"))["y"] == 128.0


def test_live_spool_rejects_implausible_frame_latency_and_drop_counts(tmp_path):
    writer = LiveSessionWriter(tmp_path, "plausible-frames-only")
    writer.append(1.0, {"frame_time": 298_652_166.8,
                        "frame_time_p95": 298_652_166.8,
                        "dropped_frames": 35_838_262.0,
                        "fps": 30.0})
    files = writer.close()

    assert set(files) == {"fps"}


def test_live_spool_rejects_negative_elapsed_time(tmp_path):
    writer = LiveSessionWriter(tmp_path, "nonnegative-only")
    writer.append(-1.0, {"fps": 30.0})
    writer.append(0.0, {"fps": 31.0})
    files = writer.close()

    assert json.loads(files["fps"].read_text(encoding="utf-8")) == {"x": 0.0, "y": 31.0}


def test_incomplete_live_spool_is_recovered_on_next_start(tmp_path):
    writer = LiveSessionWriter(tmp_path, "interrupted", manifest={
        "started_at": "2026-08-27T18:00:00+08:00",
        "device": {"name": "PC", "platform": "windows"},
        "target": {"name": "Game"},
        "enabled_metrics": ["fps"],
    })
    writer.append(1.0, {"fps": 30.0})
    writer.append(2.0, {"fps": 29.0})
    writer.close()

    recovered = recover_incomplete_sessions(tmp_path)

    assert len(recovered) == 1
    payload = load_session(tmp_path, "interrupted")
    assert payload["samples"]["fps"][-1] == {"x": 2.0, "y": 29.0}
    assert payload["name"] == "已恢复 Game"
    assert not (recovered[0].parent / LIVE_SAMPLES_DIR).exists()


def test_recovery_skips_a_crash_truncated_final_jsonl_row(tmp_path):
    writer = LiveSessionWriter(tmp_path, "truncated", manifest={
        "device": {"name": "PC", "platform": "windows"},
        "target": {"name": "Game"}, "enabled_metrics": ["fps"],
    })
    writer.append(1.0, {"fps": 30.0})
    sample_file = writer.close()["fps"]
    with sample_file.open("a", encoding="utf-8") as stream:
        stream.write('{"x":2')

    recovered = recover_incomplete_sessions(tmp_path)
    payload = load_session(tmp_path, "truncated")

    assert len(recovered) == 1
    assert payload["samples"]["fps"] == [{"x": 1.0, "y": 30.0}]
    assert not (recovered[0].parent / LIVE_SAMPLES_DIR).exists()


def test_discarded_live_session_is_never_recovered(tmp_path):
    writer = LiveSessionWriter(tmp_path, "discard-me", manifest={
        "device": {"name": "PC", "platform": "windows"},
        "target": {"name": "Game"}, "enabled_metrics": ["fps"],
    })
    writer.append(1.0, {"fps": 30.0})
    writer.close()
    discard_incomplete_session(tmp_path, "discard-me")
    session_dir = tmp_path / "data" / "sessions" / "discard-me"
    assert (session_dir / DISCARD_MARKER_NAME).is_file()

    assert recover_incomplete_sessions(tmp_path) == []
    assert not session_dir.exists()


def test_a_summary_sidecar_is_written_next_to_the_session(tmp_path):
    path = write_session(tmp_path)
    summary = json.loads((path.parent / SUMMARY_NAME).read_text(encoding="utf-8"))
    assert summary["name"] == "第一次采集"
    assert summary["application"] == "Example Game"
    assert summary["device"] == "Test Phone / TP-1"
    assert summary["duration"] == "00:02"
    assert summary["session_mtime_ns"] == path.stat().st_mtime_ns


def test_history_is_served_from_the_sidecar(tmp_path):
    path = write_session(tmp_path)
    summary_path = path.parent / SUMMARY_NAME
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["name"] = "来自旁挂文件"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    records = load_session_records(tmp_path)
    assert [record["name"] for record in records] == ["来自旁挂文件"]
    assert "session_mtime_ns" not in records[0]


def test_legacy_history_summary_gains_device_details_without_changing_recording(tmp_path):
    path = write_session(tmp_path)
    before = path.read_bytes()
    summary_path = path.parent / SUMMARY_NAME
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    fields = ("deviceName", "deviceModel", "deviceOS", "deviceCPU", "deviceMemory", "platform")
    for key in fields:
        summary.pop(key, None)
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    record = load_session_records(tmp_path)[0]
    assert record["deviceName"] == "Test Phone"
    assert record["deviceModel"] == "TP-1"
    assert record["deviceOS"] == "Android 15"
    assert record["platform"] == "android"
    assert record["deviceCPU"] == ""
    assert all(key in json.loads(summary_path.read_text(encoding="utf-8")) for key in fields)
    assert path.read_bytes() == before


def test_a_stale_sidecar_is_rebuilt_from_the_session(tmp_path):
    path = write_session(tmp_path)
    summary_path = path.parent / SUMMARY_NAME
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["name"] = "过期的名字"
    summary["session_mtime_ns"] = 1
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    assert [record["name"] for record in load_session_records(tmp_path)] == ["第一次采集"]
    rebuilt = json.loads(summary_path.read_text(encoding="utf-8"))
    assert rebuilt["session_mtime_ns"] == path.stat().st_mtime_ns


def test_renaming_does_not_rewrite_the_session_file(tmp_path):
    path = write_session(tmp_path)
    before = path.read_bytes()

    assert rename_session(tmp_path, "20260101_120000_abcdef", "  战斗   场景  ") == "战斗 场景"
    assert path.read_bytes() == before
    assert (path.parent / METADATA_NAME).is_file()
    assert load_session(tmp_path, "20260101_120000_abcdef")["name"] == "战斗 场景"
    assert [record["name"] for record in load_session_records(tmp_path)] == ["战斗 场景"]


def test_renaming_rejects_an_empty_name(tmp_path):
    write_session(tmp_path)
    with pytest.raises(ValueError):
        rename_session(tmp_path, "20260101_120000_abcdef", "   ")


def test_renaming_an_unknown_session_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        rename_session(tmp_path, "does-not-exist", "名字")


def test_deleting_a_session_removes_data_and_screenshots(tmp_path):
    path = write_session(tmp_path, screenshots=[{"image": "000001000.jpg", "seconds": 1.0}])
    screenshot = path.parent / "screenshots" / "000001000.jpg"
    screenshot.parent.mkdir()
    screenshot.write_bytes(b"jpeg")

    delete_session(tmp_path, "20260101_120000_abcdef")

    assert not path.parent.exists()
    assert load_session_records(tmp_path) == []


def test_deleting_a_session_rejects_paths_outside_the_sessions_root(tmp_path):
    write_session(tmp_path)
    with pytest.raises(ValueError):
        delete_session(tmp_path, "../20260101_120000_abcdef")


def test_screenshot_paths_are_rebound_to_the_session_folder(tmp_path):
    path = write_session(tmp_path, screenshots=[
        {"image": "file:///D:/old/machine/000001000.jpg", "time": "00:01", "seconds": 1.0}])
    shots = path.parent / "screenshots"
    shots.mkdir()
    (shots / "000001000.jpg").write_bytes(b"jpeg")

    payload = load_session(tmp_path, "20260101_120000_abcdef")
    assert payload["screenshots"][0]["image"] == (shots / "000001000.jpg").as_uri()


def test_legacy_screenshots_without_seconds_are_repaired(tmp_path):
    write_session(tmp_path, screenshots=[{"image": "", "time": "02:05"}])
    payload = load_session(tmp_path, "20260101_120000_abcdef")
    assert payload["screenshots"][0]["seconds"] == pytest.approx(125.0)


def test_a_missing_or_corrupt_session_reads_as_none(tmp_path):
    assert load_session(tmp_path, "nope") is None

    path = write_session(tmp_path)
    path.write_text("{ not json", encoding="utf-8")
    assert load_session(tmp_path, "20260101_120000_abcdef") is None


def test_corrupt_sessions_do_not_break_the_history_list(tmp_path):
    write_session(tmp_path, session_id="20260101_120000_aaaaaa", name="好的记录")
    broken = write_session(tmp_path, session_id="20260101_130000_bbbbbb", name="坏的记录")
    broken.write_text("{ not json", encoding="utf-8")
    (broken.parent / SUMMARY_NAME).unlink()

    assert [record["name"] for record in load_session_records(tmp_path)] == ["好的记录"]


def test_semantically_corrupt_sidecars_and_fields_do_not_break_history(tmp_path):
    path = write_session(tmp_path, name="可恢复记录")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["duration"] = "not-a-number"
    payload["device"] = ["wrong-type"]
    payload["samples"] = {"fps": ["wrong-row"]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    (path.parent / SUMMARY_NAME).write_text('["wrong-summary"]', encoding="utf-8")

    records = load_session_records(tmp_path)

    assert len(records) == 1
    assert records[0]["name"] == "可恢复记录"
    assert records[0]["duration"] == "00:00"


def test_non_object_session_and_screenshot_rows_are_ignored(tmp_path):
    path = write_session(tmp_path, screenshots=["bad", {"image": "", "time": "00:02"}])
    payload = load_session(tmp_path, "20260101_120000_abcdef")
    assert len(payload["screenshots"]) == 1
    assert payload["screenshots"][0]["seconds"] == pytest.approx(2.0)

    path.write_text("[]", encoding="utf-8")
    assert load_session(tmp_path, "20260101_120000_abcdef") is None


def test_loaded_sample_rows_are_repaired_sorted_and_filtered(tmp_path):
    path = write_session(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["samples"]["fps"] = [
        {"x": "3", "y": "30"}, {"x": 1, "y": 20},
        {"x": "bad", "y": 10}, {"x": 2, "y": float("nan")},
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_session(tmp_path, "20260101_120000_abcdef")

    assert loaded["samples"]["fps"] == [{"x": 1, "y": 20}, {"x": 3.0, "y": 30.0}]


def test_history_is_newest_first(tmp_path):
    write_session(tmp_path, session_id="a", name="早")
    save_session(tmp_path, DEVICE, TARGET, SAMPLES, [], {"fps"}, "b", SCENES,
                 "2026-03-01T09:00:00+08:00", 5.0, "晚")

    assert [record["name"] for record in load_session_records(tmp_path)] == ["晚", "早"]


def test_duration_falls_back_to_the_last_sample(tmp_path):
    save_session(tmp_path, DEVICE, TARGET, SAMPLES, [], {"fps"}, "c", SCENES,
                 "2026-01-01T12:00:00+08:00", 0.0, "无时长")
    record = next(item for item in load_session_records(tmp_path) if item["id"] == "c")
    assert record["duration"] == "00:02"


def test_an_unnamed_session_falls_back_to_the_application(tmp_path):
    save_session(tmp_path, DEVICE, TARGET, SAMPLES, [], {"fps"}, "d", SCENES,
                 "2026-01-01T12:00:00+08:00", 2.0, "")
    record = next(item for item in load_session_records(tmp_path) if item["id"] == "d")
    assert record["name"] == "Example Game"


def test_name_normalisation():
    assert normalize_session_name("  多个    空格 ") == "多个 空格"
    assert normalize_session_name(None) == ""
    assert len(normalize_session_name("名" * 200)) == 80


def test_session_ids_are_unique():
    assert len({new_session_id() for _ in range(50)}) == 50
