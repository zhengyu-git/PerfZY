"""Regression guards for the repository-owned offline report engine."""

import json
import re
import pytest

from perfzy import reporting
from perfzy.reporting import generate_local_report, safe_report_file_stem


@pytest.mark.parametrize("mode", ["none", "compressed", "original"])
def test_report_screenshot_modes(tmp_path, mode):
    import base64
    from PIL import Image
    path = tmp_path / "shot.png"
    Image.effect_noise((1200, 800), 60).convert("RGB").save(path)
    original = path.read_bytes()
    session = {"samples": {"fps": [{"x": 1, "y": 30}]},
               "screenshots": [{"seconds": 1, "image": str(path)}]}
    payload = reporting._report_payload(session, screenshot_mode=mode)
    assert payload["samples"] == [{"elapsed": 1.0, "fps": 30.0}]
    if mode == "none":
        assert payload["screenshots"] == []
    else:
        assert len(payload["screenshots"]) == 1
        raw = base64.b64decode(payload["screenshots"][0]["image"].split(",", 1)[1])
        if mode == "original":
            assert raw == original
        else:
            assert len(raw) < len(original)
    assert path.read_bytes() == original


def test_invalid_report_mode_fails_before_reading(tmp_path):
    with pytest.raises(ValueError, match="截图导出模式"):
        generate_local_report(tmp_path / "absent.json", tmp_path, screenshot_mode="bad")


def test_screenshot_compression_preserves_original(tmp_path):
    import base64
    import io
    from PIL import Image
    image = Image.effect_noise((1600, 900), 60).convert("RGB")
    path = tmp_path / "shot.png"
    image.save(path)
    original = path.read_bytes()
    uri = reporting._image_data_uri(path, max_edge=1280, jpeg_quality=70)
    encoded = base64.b64decode(uri.split(",", 1)[1])
    assert len(encoded) < len(original)
    assert path.read_bytes() == original
    with Image.open(io.BytesIO(encoded)) as result:
        assert result.size == (1280, 720)


def test_report_generation_has_no_machine_specific_engine_dependency(tmp_path):
    session_dir = tmp_path / "data" / "sessions" / "case-1"
    session_dir.mkdir(parents=True)
    session_path = session_dir / "session.json"
    session_path.write_text(json.dumps({
        "id": "case-1",
        "name": "战斗测试",
        "end_reason": "采样中断：设备断开",
        "duration": 2.0,
        "started_at": "2026-08-27T12:00:00+08:00",
        "device": {"name": "Test Phone", "os": "Android 15"},
        "target": {"name": "Example Game", "id": "com.example.game"},
        "samples": {
            "fps": [{"x": 1.0, "y": 30.0}, {"x": 2.0, "y": 29.0}],
            "app_cpu": [{"x": 1.0, "y": 40.0}, {"x": 2.0, "y": 42.0}],
        },
        "screenshots": [],
        "scenes": [],
    }, ensure_ascii=False), encoding="utf-8")

    report = generate_local_report(session_path, tmp_path / "exports", "portable-report")
    document = report.read_text(encoding="utf-8")
    assert report.name == "portable-report.html"
    assert "__PERFZY_" not in document
    assert "Documents\\Codex" not in document
    assert "function drawStatisticsBox" in document
    assert "boxW=Math.min(250" not in document
    match = re.search(
        r'<script id="reportData" type="application/json">(.*?)</script>', document, re.S)
    payload = json.loads(match.group(1))
    assert payload["name"] == "战斗测试"
    assert "采集结束原因：采样中断：设备断开" in payload["notes"]
    assert payload["groups"][0] == {"key": "fps", "title": "FPS", "keys": ["fps"]}
    assert payload["samples"][-1]["app_cpu"] == 42.0


def test_compact_fallback_embeds_screenshots_instead_of_machine_paths(tmp_path, monkeypatch):
    session_dir = tmp_path / "data" / "sessions" / "fallback"
    screenshots = session_dir / "screenshots"
    screenshots.mkdir(parents=True)
    image = screenshots / "000001000.jpg"
    image.write_bytes(b"jpeg-data")
    session_path = session_dir / "session.json"
    session_path.write_text(json.dumps({
        "id": "fallback", "duration": 1.0,
        "device": {"name": "PC"}, "target": {"name": "Game"},
        "samples": {"fps": [{"x": 1.0, "y": 30.0}]},
        "screenshots": [{"image": image.as_uri(), "seconds": 1.0}],
        "scenes": [],
    }), encoding="utf-8")
    monkeypatch.setattr(reporting, "_REPORT_TEMPLATE_V10", tmp_path / "missing.html")

    report = generate_local_report(session_path, tmp_path / "exports")
    document = report.read_text(encoding="utf-8")

    assert "data:image/jpeg;base64," in document
    assert str(tmp_path).replace("\\", "/") not in document


def test_compact_fallback_ignores_malformed_optional_data(tmp_path, monkeypatch):
    session_dir = tmp_path / "data" / "sessions" / "fallback-corrupt"
    session_dir.mkdir(parents=True)
    session_path = session_dir / "session.json"
    session_path.write_text(json.dumps({
        "id": "fallback-corrupt", "duration": "NaN", "device": 12, "target": [],
        "samples": {"fps": [None, {"x": -1, "y": 80}, {"x": 1, "y": 30}],
                    "memory": "not-a-list"},
        "screenshots": ["bad", {"image": "missing.jpg", "seconds": "bad"}],
        "scenes": [None, {"start": "bad", "end": 3}, {"start": -3, "end": -1}],
    }), encoding="utf-8")
    monkeypatch.setattr(reporting, "_REPORT_TEMPLATE_V10", tmp_path / "missing.html")

    report = generate_local_report(session_path, tmp_path / "exports")
    document = report.read_text(encoding="utf-8")

    assert "FPS" in document
    assert "本次采集没有截图" in document
    assert "本次采集没有添加时间段标记" in document
    assert not report.with_suffix(".html.tmp").exists()


def test_windows_reserved_report_names_are_made_safe():
    assert safe_report_file_stem("CON") == "_CON"
    assert safe_report_file_stem("lpt1.txt") == "_lpt1.txt"


def test_reports_omit_legacy_frame_timestamp_sentinels():
    session = {
        "samples": {
            "frame_time": [{"x": 1.0, "y": 32.0},
                           {"x": 2.0, "y": 298_652_166.8}],
            "dropped_frames": [{"x": 2.0, "y": 35_838_262.0}],
        },
        "screenshots": [], "scenes": [], "device": {}, "target": {},
    }

    adapted, _metrics, _groups = reporting._legacy_session(session)

    assert adapted["samples"] == [{"elapsed": 1.0, "frame_time": 32.0}]


def test_new_windows_metrics_keep_their_chart_groups_in_reports():
    session = {
        "samples": {
            "fps_1_low": [{"x": 1.0, "y": 48.0}],
            "cpu_frame_busy": [{"x": 1.0, "y": 6.5}],
            "gpu_busy": [{"x": 1.0, "y": 9.0}],
            "gpu_memory": [{"x": 1.0, "y": 2048.0}],
            "disk_read_iops": [{"x": 1.0, "y": 120.0}],
        },
        "screenshots": [], "scenes": [], "device": {}, "target": {},
    }

    _adapted, metrics, groups = reporting._legacy_session(session)
    group_map = {key: set(keys) for key, _title, keys in groups}

    assert metrics["fps_1_low"]["unit"] == "FPS"
    assert group_map["fps"] == {"fps_1_low"}
    assert group_map["frame_pipeline"] == {"cpu_frame_busy"}
    assert group_map["gpu_frame"] == {"gpu_busy"}
    assert group_map["gpu_memory"] == {"gpu_memory"}
    assert group_map["io_ops"] == {"disk_read_iops"}


def test_fps_quality_calculation_contract_in_actual_report_template():
    import shutil
    import subprocess
    from pathlib import Path
    node=shutil.which('node')
    if node is None:
        pytest.skip('Node.js is only needed to execute report JavaScript tests')
    script=Path(__file__).with_name('report_fps_statistics.cjs')
    result=subprocess.run([node,str(script)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr


def test_generated_report_embeds_fps_quality_helpers(tmp_path):
    source=tmp_path/'session.json'
    source.write_text(json.dumps({'samples':{'fps':[{'x':0,'y':30},{'x':1,'y':18}]}}),encoding='utf-8')
    document=generate_local_report(source,tmp_path/'exports',screenshot_mode='none').read_text(encoding='utf-8')
    for label in ('FPS>=18[%]','FPS>=25[%]','Var(FPS)','Drop(FPS)[/h]'):
        assert label in document
    assert 'function fpsQualityStats(' in document
    assert 'function statisticsRanges(' in document
