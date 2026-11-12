"""Approved visual workbench ships offline, never from the demo export."""
import json
import re

from perfzy.reporting import generate_local_report


def test_generated_workbench_has_no_demo_assets(tmp_path):
    session = tmp_path / 'session.json'
    session.write_text(json.dumps({
        'id': 'workbench-delivery', 'name': '离线工作台验证', 'duration': 2,
        'device': {'platform': 'windows', 'id': 'test-pc', 'name': 'Test PC'},
        'target': {'name': 'Test App'},
        'samples': {'fps': [{'x': 0, 'y': 30}, {'x': 1, 'y': 0}, {'x': 2, 'y': 60}]},
        'screenshots': [], 'scenes': [],
    }, ensure_ascii=False), encoding='utf8')
    report = generate_local_report(session, tmp_path / 'exports')
    text = report.read_text(encoding='utf8')
    assert 'id="report-workbench-theme"' in text
    assert 'id="report-workbench-layout"' in text
    assert 'decimateMetricSegment' in text
    assert 'studioSidebar' in text
    assert '__PERFZY_' not in text
    assert '改版预览' not in text
    assert 'previewScreenshotData' not in text
    assert '演示配图' not in text
    assert 'initializePreviewScreenshots' not in text
    assert 'fetch(' not in text
    assert '<script src=' not in text
    match = re.search(r'<script id="reportData" type="application/json">(.*?)</script>', text, re.S)
    payload = json.loads(match.group(1))
    assert payload['screenshots'] == []
    assert payload['screenshot_total'] == 0
    assert payload['samples'][1]['fps'] == 0
