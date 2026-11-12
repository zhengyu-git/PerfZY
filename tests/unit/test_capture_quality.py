import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from perfzy.collectors.base import FpsStabilizer
from perfzy.collectors.android import AndroidCollector
from perfzy.domain.models import Sample
from perfzy.domain.quality import QualityTracker
from perfzy.domain.validation import metric_value_is_plausible
from perfzy.storage.sessions import LiveSessionWriter, save_session_from_spool, recover_incomplete_sessions
from perfzy.reporting import _report_payload
from perfzy.comparison import comparison_model, generate_comparison_report
from perfzy import diagnostics


@pytest.mark.parametrize('old,new,confirmations', [(30,34,6),(60,68,6),(90,102,6),(120,136,6),(30,60,3),(60,120,3)])
def test_sustained_rises_have_a_finite_confirmation_window(old, new, confirmations):
    stabilizer = FpsStabilizer()
    for _ in range(12):
        stabilizer.accept(old)
    values = [stabilizer.accept(new) for _ in range(3600)]
    assert values[:confirmations-1] == [old] * (confirmations-1)
    assert values[confirmations-1:] == [new] * (3601-confirmations)
    assert len(stabilizer.high_candidates) <= 6


def test_unstable_upward_noise_is_bounded_and_isolated_spike_is_still_suppressed():
    stabilizer = FpsStabilizer()
    for _ in range(12): stabilizer.accept(30)
    for index in range(3600):
        assert stabilizer.accept(100 if index % 2 else 40) == 30
        assert len(stabilizer.high_candidates) <= 6
    assert stabilizer.accept(20) == 20
    assert not stabilizer.high_candidates


def test_cached_observation_does_not_establish_fps_switch_and_invalidation_wins():
    stabilizer = FpsStabilizer()
    for _ in range(12): stabilizer.accept(30)
    for _ in range(100): assert stabilizer.accept(60, observation_id=1) == 30
    assert len(stabilizer.high_candidates) == 1
    assert stabilizer.accept(60, observation_id=2) == 30
    assert stabilizer.accept(60, observation_id=3) == 60
    assert stabilizer.accept(None, observation_id=3) is None
    assert not stabilizer.high_candidates


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, 1001, 'invalid'])
def test_invalid_fps_cannot_poison_history(value):
    stabilizer = FpsStabilizer()
    assert stabilizer.accept(value) is None
    assert not stabilizer.history
    assert stabilizer.last_reason == 'invalid_fps'


def test_gfxinfo_keeps_complete_nine_second_frame_but_rejects_incomplete_sentinel():
    header = '---PROFILEDATA---\nFlags,IntendedVsync,FrameCompleted\n'
    text = header + '0,1000000000,10000000000\n0,1000000000,9223372036854775807\n---PROFILEDATA---'
    assert AndroidCollector._parse_framestats(text, 0) == [(10_000_000_000, 9000)]
    assert metric_value_is_plausible('frame_time', 9000)
    assert not metric_value_is_plausible('frame_time', 1e12)


def finalize(root, writer):
    return save_session_from_spool(root, {}, {}, writer.close(), [], {'fps', 'app_cpu'},
                                   writer.session_id, duration=3)


def test_raw_values_missing_and_processing_survive_finalize(tmp_path):
    writer = LiveSessionWriter(tmp_path, 'quality', manifest={'capture_settings': {'build_label':'A'}})
    writer.append(1, {'fps':30, 'app_cpu':None}, raw_values={'fps':41, 'app_cpu':None},
                  adjustments={'fps':'pending_upward_change'}, requested=['fps','app_cpu'], collector_ms=20)
    writer.append(2, {'fps':34, 'app_cpu':0}, requested=['fps','app_cpu'], collector_ms=40)
    payload = json.loads(finalize(tmp_path, writer).read_text(encoding='utf-8'))
    assert payload['quality']['metrics']['fps']['valid_percent'] == 100
    assert payload['quality']['metrics']['fps']['adjusted'] == 1
    assert payload['quality']['metrics']['app_cpu']['valid_percent'] == 50
    assert payload['quality']['collector_avg_ms'] == 30
    assert payload['capture_settings']['build_label'] == 'A'
    audit = [json.loads(line) for line in (tmp_path/'data/sessions/quality/audit.jsonl').read_text(encoding='utf-8').splitlines()]
    assert audit[0]['raw_values']['fps'] == 41
    assert audit[0]['values']['fps'] == 30
    assert audit[0]['adjustments']['fps'] == 'pending_upward_change'
    report = _report_payload(payload)
    assert report['quality']['metrics']['app_cpu']['missing'] == 1
    assert report['capture_settings']['build_label'] == 'A'


def test_crash_recovery_rebuilds_quality_and_ignores_partial_audit_tail(tmp_path):
    writer = LiveSessionWriter(tmp_path, 'crashed', manifest={'target':{'name':'game'}})
    writer.append(1, {'fps':30})
    writer.append(2, {'fps':None}, raw_values={'fps':math.inf})
    writer.flush()
    # Simulate process death without the graceful-close quality checkpoint.
    for stream in writer._streams.values(): stream.close()
    writer._audit.close()
    with (tmp_path/'data/sessions/crashed/audit.jsonl').open('a', encoding='utf-8') as stream:
        stream.write('{"elapsed":3')
    recovered = recover_incomplete_sessions(tmp_path)
    payload = json.loads(recovered[0].read_text(encoding='utf-8'))
    assert payload['quality']['metrics']['fps']['valid_percent'] == 50
    assert payload['quality']['samples'] == 2


def test_long_frames_are_flagged_even_when_window_average_is_below_five_seconds():
    quality = QualityTracker()
    quality.record(1, {'frame_time':4510}, ['frame_time'],
                   {'frame_time':'long_complete_frame_needs_review'})
    assert quality.summary()['metrics']['frame_time']['suspicious_long'] == 1


def test_time_coverage_does_not_bridge_a_long_sampling_gap_or_count_overlap_twice():
    quality = QualityTracker()
    for stamp in (0, .5, 10):
        quality.record(stamp, {'fps':30}, ['fps'], interval_seconds=1)
    row = quality.summary(duration=11)['metrics']['fps']
    assert row['valid_percent'] == 100
    assert row['coverage_estimate_seconds'] == 2.5
    assert row['coverage_estimate_percent'] == pytest.approx(100*2.5/11)
    assert quality.summary()['max_gap_seconds'] == 9.5


def test_unknown_sampling_period_does_not_invent_time_coverage():
    quality=QualityTracker()
    quality.record(1, {'fps':30}, ['fps'])
    assert quality.summary()['metrics']['fps']['coverage_estimate_percent'] is None


def test_capture_period_changes_are_used_by_audit_and_final_quality(tmp_path):
    writer=LiveSessionWriter(tmp_path,'changed',manifest={'capture_settings':{'sample_interval_ms':1000}})
    writer.append(0,{'fps':30})
    writer.checkpoint({'capture_settings_changes':[{'elapsed':2,'settings':{'sample_interval_ms':2000}}]})
    writer.append(2,{'fps':30})
    payload=json.loads(finalize(tmp_path,writer).read_text(encoding='utf-8'))
    assert payload['quality']['metrics']['fps']['coverage_estimate_seconds']==2
    assert payload['capture_settings_changes'][0]['settings']['sample_interval_ms']==2000


def test_legacy_report_does_not_invent_quality_statistics():
    payload = _report_payload({'samples':{'fps':[{'x':1,'y':30}]}})
    assert payload['quality'] == {}


def session(values, scenes=None):
    return dict(name='game', device={'id':'phone1','name':'Phone','os':'OS1'},
                target={'name':'Game'}, capture_settings=dict(sample_interval_ms=1000, screenshots=False,
                screenshot_interval_ms=3000, screenshot_max_width=1280, conditions='High / 60 FPS',
                collector_revision='quality-v2'), samples={'fps':[{'x':i,'y':v} for i,v in enumerate(values)]},
                scenes=scenes or [], quality={'metrics':{'fps':{'valid_percent':100}}})


def test_comparison_deltas_zero_missing_and_overlapping_scenes(tmp_path):
    a = session([0,30,30], [{'name':'battle','start':0,'end':2},{'name':'battle','start':1,'end':2}])
    b = session([0,60,60], [{'name':'battle','start':0,'end':2}])
    model = comparison_model(a,b)
    row = model['scopes'][1]['rows'][0]
    assert row['baseline']['count'] == 3
    assert row['delta'] == 20
    assert row['percent'] == 100
    assert comparison_model(session([0]), session([1]))['scopes'][0]['rows'][0]['percent'] is None
    missing = session([1]); missing['samples']['app_cpu'] = [{'x':1,'y':20}]
    assert any(row['baseline'] is None for row in comparison_model(a, missing)['scopes'][0]['rows'])
    a['name'] = '<script>alert(1)</script>'
    report = generate_comparison_report(a,b,tmp_path).read_text(encoding='utf-8')
    assert '<script>alert(1)</script>' not in report
    assert '&lt;script&gt;' in report
    assert 'data:text/csv;base64,' in report


def test_comparison_warns_on_different_or_unknown_conditions_and_capture_changes():
    a,b=session([30]),session([60])
    b['device']['id']='phone2'
    b['capture_settings']['conditions']=''
    b['capture_settings_changes']=[{'elapsed':3,'settings':{}}]
    b['quality']['metrics']['fps']['valid_percent']=50
    warnings=' '.join(comparison_model(a,b)['warnings'])
    assert '设备不同' in warnings
    assert '测试条件未完整记录' in warnings
    assert '中途调整' in warnings
    assert '缺失较多' in warnings


def test_comparison_does_not_equate_different_mobile_packages_with_the_same_name():
    a,b=session([30]),session([30])
    for item in (a,b): item['device']['platform']='ios'
    a['target']['id']='ios:device:com.game.a'
    b['target']['id']='ios:device:com.game.b'
    assert any('应用包标识不同' in text for text in comparison_model(a,b)['warnings'])


def test_comparison_csv_keeps_user_scene_names_as_text(tmp_path):
    import base64
    import re
    a,b=session([30,30]),session([40,40])
    for item in (a,b): item['scenes']=[{'name':'=1+1','start':0,'end':1}]
    report=generate_comparison_report(a,b,tmp_path).read_text(encoding='utf-8')
    csv=base64.b64decode(re.search('data:text/csv;base64,([^" ]+)',report).group(1)).decode('utf-8-sig')
    assert "'=1+1" in csv


@pytest.mark.parametrize('state,needle', [('unauthorized','尚未授权'),('offline','离线'),('device','已授权')])
def test_connection_check_distinguishes_android_states(tmp_path, monkeypatch, state, needle):
    commands=[]
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0,stdout=f'List of devices attached\nserial {state}\n',stderr='')
    monkeypatch.setattr(diagnostics.subprocess,'run',run)
    result=diagnostics.connection_check(tmp_path,'android')
    assert needle in result[-1]['detail']
    assert len(commands) == 1
    assert commands[0][-2:] == ['devices','-l']


def test_connection_check_reports_bridge_failure_without_claiming_driver_absence(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics.importlib.util,'find_spec',lambda name: object())
    def fail(*args, **kwargs): raise RuntimeError('pairing failed')
    monkeypatch.setattr(diagnostics,'run_json',fail)
    result=diagnostics.connection_check(tmp_path,'ios')
    assert result[-1]['status']=='需处理'
    assert 'pairing failed' in result[-1]['detail']


def test_sample_legacy_constructor_is_compatible():
    sample=Sample(1, {'fps':30})
    assert sample.raw_values == {}
