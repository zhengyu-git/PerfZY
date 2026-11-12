import json
from pathlib import Path
import shutil
import subprocess

import pytest

from perfzy.reporting import generate_local_report


def test_report_preserves_capture_quality_settings_and_changes(tmp_path):
    quality = {'samples': 2, 'metrics': {'fps': {'valid': 1, 'requested': 2,
                'valid_percent': 50, 'coverage_estimate_percent': 40, 'held': 3}}}
    settings = {'build_label': 'A', 'conditions': 'High / 60 FPS'}
    changes = [{'elapsed': 1, 'settings': {'sample_interval_ms': 2000}}]
    session = tmp_path / 'session.json'
    source = {'id': 'quality-report', 'duration': 2, 'quality': quality,
              'capture_settings': settings, 'capture_settings_changes': changes,
              'capture_settings_changes_truncated': True,
              'device': {'platform': 'windows'}, 'target': {'name': 'QA'},
              'samples': {'fps': [{'x': 0, 'y': 30}, {'x': 2, 'y': 0}]}}
    session.write_text(json.dumps(source), encoding='utf8')
    before = session.read_bytes()
    report = generate_local_report(session, tmp_path / 'exports', screenshot_mode='none')
    import re
    text = report.read_text(encoding='utf8')
    payload = json.loads(re.search(r'<script id="reportData" type="application/json">(.*?)</script>', text, re.S).group(1))
    assert payload['quality'] == quality
    assert payload['capture_settings'] == settings
    assert payload['capture_settings_changes'] == changes
    assert payload['capture_settings_changes_truncated'] is True
    assert payload['samples'][-1]['fps'] == 0
    assert 'captureQualityDetails' in text
    assert 'report-workbench-theme' in text
    assert session.read_bytes() == before


def test_capture_quality_report_javascript_contract():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is needed to execute report JavaScript contracts')
    result = subprocess.run([node, str(Path(__file__).with_name('report_capture_quality.cjs'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
