import json
import math

import pytest

from perfzy import automation, reporting
from perfzy.domain.health import SamplingHealth
from perfzy.domain.models import Device, Platform, Sample, Target
from perfzy.domain.provenance import metric_provenance
from perfzy.domain.validation import sample_points
from perfzy.storage.sessions import LiveSessionWriter, save_session_from_spool


def test_per_metric_observation_time_survives_streaming_save(tmp_path):
    writer = LiveSessionWriter(tmp_path, 'clock', flush_every=1)
    writer.append(3, {'fps': 60, 'memory': 512}, observed_elapsed={'memory': 2.75})
    path = save_session_from_spool(tmp_path, {}, {}, writer.close(), [],
                                   {'fps', 'memory'}, 'clock', duration=3,
                                   telemetry={'samples': 1})
    result = json.loads(path.read_text(encoding='utf-8'))
    assert result['samples']['memory'] == [{'x': 2.75, 'y': 512}]
    assert result['samples']['fps'] == [{'x': 3, 'y': 60}]
    assert result['telemetry'] == {'samples': 1}


def test_invalid_channel_timestamp_is_not_replaced_with_current_tick():
    sample = Sample(3, {'fps': 60, 'memory': 512}, {'memory': math.nan})
    assert sample_points(sample) == {'fps': {'x': 3, 'y': 60}}


def test_api_timestamps_and_diagnostics_persist(tmp_path, monkeypatch):
    api = automation.PerfZY(tmp_path)
    class Collector:
        def start(self, *args): pass
        def stop(self): pass
        def sample(self, elapsed):
            api._stop_event.set()
            return Sample(5, {'memory': 256}, {'memory': 4.25})
        def diagnostics(self): return {'memory': {'completed': 1}}
    monkeypatch.setattr(automation, 'WindowsCollector', Collector)
    api.device = Device('windows:local', Platform.WINDOWS, 'PC')
    api.game = Target('windows:1', 'windows:local', 'Game', 1)
    try:
        api.start(metrics=['memory'], screenshots=False)
        api._thread.join(timeout=5)
        assert api._samples['memory'][0]['x'] == 4.25
        assert api.diagnostics()['samples'] == 1
        result = api.stop('clock')
        stored = json.loads(result.session_path.read_text(encoding='utf-8'))
        assert stored['samples']['memory'][0]['x'] == 4.25
        assert stored['telemetry']['collector']['memory']['completed'] == 1
    finally:
        api.close()


def test_health_remains_bounded_over_three_hour_simulation():
    health = SamplingHealth(.5)
    for i in range(21_600):
        point = {'fps': {'x': i * .5, 'y': 60}}
        health.observe(i * .5, point, 2, ['fps', 'memory'])
    result = health.snapshot(10_800)
    assert result['samples'] == 21_600
    assert len(health.recent_ms) == 128
    assert set(health.fields) == {'fps', 'memory'}
    assert result['metrics']['memory']['received'] == 0
    assert result['metrics']['memory']['missing'] == 21_600


def test_metric_scope_and_estimates_are_explicit():
    sources = metric_provenance('android', ['fps', 'network_recv', 'memory', 'gpu_busy_time'])
    assert '非目标应用独占' in sources['network_recv']['scope']
    assert sources['memory']['source'] == 'dumpsys meminfo --local'
    assert sources['gpu_busy_time']['method'] == '估算/比率'
    assert '尖峰过滤' in sources['fps']['method']


def test_basic_report_keeps_curves_without_analysis(tmp_path):
    session = {'id': 'simple', 'duration': 8, 'device': {'id': 'android:test', 'platform': 'android'},
               'samples': {'fps': [{'x': i, 'y': 20 if 2 <= i <= 4 else 60} for i in range(9)]},
               'scenes': []}
    path = tmp_path / 'session.json'
    path.write_text(json.dumps(session), encoding='utf-8')
    report = reporting.generate_local_report(path, tmp_path)
    text = report.read_text(encoding='utf-8')
    assert 'enhanced-analysis' not in text
    assert '__PERFZY_' not in text
    assert 'perfzy-analysis.json' not in text
    payload = reporting._report_payload(session, screenshot_mode='none')
    assert len(payload['samples']) == 9
    assert 'analysis' not in payload


def test_old_detection_thresholds_do_not_add_report_anomalies():
    session = {'samples': {'fps': [{'x': 0, 'y': 45}, {'x': 1, 'y': 45}]},
               'duration': 1, 'telemetry': {'thresholds': {'fps_below': 50}}}
    payload = reporting._report_payload(session, screenshot_mode='none')
    assert all(row['fps'] == 45 for row in payload['samples'])
    assert 'analysis' not in payload
