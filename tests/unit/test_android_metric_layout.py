import copy
import pytest
from PySide6.QtCore import QSettings
from perfzy.application.charts import (DEFAULT_CHART_ORDER, ANDROID_VISIBLE_CHARTS,
    build_chart_catalog, group_key_for_metric)
from perfzy.application.preferences import Preferences, ANDROID_DEFAULT_METRICS
from perfzy.application.metric_status import update_android_status
from perfzy.application import controller as module
from perfzy.domain.metrics import METRICS, METRIC_BY_KEY, platform_metric_note
from perfzy.domain.models import Platform, Device
from perfzy.domain.provenance import metric_provenance
from perfzy.reporting import _report_payload
from test_android_collector import make_collector, MEMINFO, SURFACE_TEMPLATE, FRAMESTATS_WITH_SENTINEL


def test_android_complete_unique_single_unit_groups_and_compact_defaults():
    rows = build_chart_catalog(Platform.ANDROID, DEFAULT_CHART_ORDER, ANDROID_VISIBLE_CHARTS, ANDROID_DEFAULT_METRICS, 6)
    keys = [key for row in rows for key in row['metricKeys']]
    expected = {m.key for m in METRICS if Platform.ANDROID in m.platforms
                and (not m.key.startswith('cpu_core_') or int(m.key.split('_')[2]) < 6)}
    assert set(keys) == expected and len(keys) == len(set(keys))
    assert {r['key'] for r in rows if r['visible']} == set(ANDROID_VISIBLE_CHARTS)
    for row in rows:
        assert len({METRIC_BY_KEY[k].unit for k in row['metricKeys']}) == 1, row['key']
        assert all(group_key_for_metric(k, Platform.ANDROID) == row['key'] for k in row['metricKeys'])
        assert all(m['reason'] and m['status'] == '待检测' for m in row['metrics'])
    catalog = {r['key']:r for r in rows}
    assert catalog['memory']['metricKeys'] == ['memory']
    assert catalog['cpu']['metricKeys'] == ['app_cpu']
    assert catalog['power']['metricKeys'] == ['power']
    assert catalog['gpu']['metricKeys'] == ['gpu']
    assert catalog['network']['tier'] == 'system'
    assert catalog['frame']['tier'] == 'advanced'
    # Jank is discoverable in the basic directory without silently starting
    # extra frame-distribution queries or changing the compact default charts.
    stutter = catalog['stutter']
    assert stutter['tier'] == 'basic' and not stutter['visible']
    assert set(stutter['metricKeys']) == {'jank', 'big_jank', 'small_jank'}
    assert not any(stutter['seriesEnabled'])
    assert set(ANDROID_VISIBLE_CHARTS) == {'fps', 'cpu', 'memory', 'gpu'}
    assert set(ANDROID_DEFAULT_METRICS) == {'fps', 'app_cpu', 'memory', 'gpu'}
    assert 'Slow Frames' in stutter['title'] and 'Fixed Threshold' in stutter['title']
    thresholds = {'jank': '(33.34,50.01]', 'big_jank': '>50.01', 'small_jank': '(16.67,33.34]'}
    for key, threshold in thresholds.items():
        note = platform_metric_note(key, Platform.ANDROID)
        assert threshold in note and '固定阈值' in note
        assert '桶近似' in note and '不等同 PerfDog' in note


def test_android_one_time_migration_backs_up_selection_and_preserves_other_platforms(tmp_path):
    path = tmp_path / 'prefs.ini'
    prefs = Preferences(path)
    saved = {p:set(prefs.enabled_for(p)) for p in (Platform.WINDOWS,Platform.IOS)}
    settings = prefs._settings
    settings.remove('migrations/android_layout_v1')
    settings.remove('metrics/android/compact_enabled')
    settings.setValue('metrics/android/enabled', ['fps','java_heap','network_send'])
    settings.setValue('charts/visible', ['memory','network'])
    settings.sync()
    prefs = Preferences(path)
    assert prefs.enabled_for(Platform.ANDROID) == set(ANDROID_DEFAULT_METRICS)
    assert prefs.visible_charts == set(ANDROID_VISIBLE_CHARTS)
    assert set(prefs._settings.value('migrations/android_layout_v1_previous_enabled')) == {'fps','java_heap','network_send'}
    for platform, keys in saved.items():
        assert prefs.enabled_for(platform) == keys
    prefs.set_metric_enabled(Platform.ANDROID,'gpu',False)
    prefs.save()
    old = QSettings(str(path),QSettings.IniFormat)
    old.setValue('metrics/android/enabled',['fps','java_heap','network_send'])
    old.setValue('charts/visible',['network'])
    old.sync()
    assert Preferences(path).enabled_for(Platform.ANDROID) == set(ANDROID_DEFAULT_METRICS)-{'gpu'}
    assert Preferences(path).visible_charts == set(ANDROID_VISIBLE_CHARTS)
    prefs.enabled_for(Platform.ANDROID).clear();prefs.visible_charts.clear();prefs.save()
    assert not Preferences(path).enabled_for(Platform.ANDROID)
    assert not Preferences(path).visible_charts


def test_android_initial_explicit_empty_selection_stays_empty(tmp_path):
    path = tmp_path/'empty.ini'
    settings = QSettings(str(path),QSettings.IniFormat)
    settings.setValue('metrics/android/enabled',[]);settings.sync()
    assert not Preferences(path).enabled_for(Platform.ANDROID)


def test_android_availability_zero_recovery_slow_channel_and_source_labels():
    state, misses = {}, {}
    for _ in range(5):
        state = update_android_status(state,misses,['gpu','memory'],{}, {'sample_interval_ms':500})
    assert state['gpu']['state'] == 'unavailable'
    assert state['memory']['state'] == 'checking'
    state = update_android_status(state,misses,['gpu','frame_time'],{'gpu':{'y':0},'frame_time':{'y':50}},
                                  {'metric_sources':{'frame_time':'fps_estimate'}})
    assert state['gpu']['state'] == 'available'
    assert 'FPS估算' in state['frame_time']['label']
    state = update_android_status(state,misses,['frame_time_p95'],{'frame_time_p95':{'y':33}},
                                  {'metric_sources':{'frame_time_p95':'surface_histogram'}})
    assert '桶近似' in state['frame_time_p95']['label']


def test_android_actual_sources_are_cached_without_extra_device_reads():
    response = {'dumpsys SurfaceFlinger --timestats -dump':SURFACE_TEMPLATE.format(frames=100,average=60,smooth=80)}
    collector = make_collector(response, {'fps','frame_time','frame_time_p95'})
    collector._frame_tracking = True
    collector.sample(1)
    response['dumpsys SurfaceFlinger --timestats -dump'] = SURFACE_TEMPLATE.format(frames=160,average=60,smooth=140)
    collector.sample(2)
    calls = len(collector.calls)
    diag = collector.diagnostics()
    assert diag['metric_sources']['fps'] == 'surfaceflinger_counter'
    assert diag['metric_sources']['frame_time_p95'] == 'surface_histogram'
    assert diag['metric_source_counts']['frame_time']['surface_histogram'] == 1
    assert len(collector.calls) == calls
    diag['metric_source_counts']['frame_time']['surface_histogram'] = 999
    assert collector.diagnostics()['metric_source_counts']['frame_time']['surface_histogram'] == 1


def test_android_gpu_memory_fallback_source_is_not_dedicated_vram():
    response = {'dumpsys meminfo':MEMINFO}
    collector = make_collector(response, {'app_gpu_memory'})
    assert collector.sample(1).values['app_gpu_memory'] == 300
    assert collector.diagnostics()['metric_sources']['app_gpu_memory'] == 'graphics_pss_fallback'
    response['dumpsys gpu'] = 'Proc 1234 total: 1048576\n'
    assert collector.sample(2).values['app_gpu_memory'] == 1
    assert collector.diagnostics()['metric_sources']['app_gpu_memory'] == 'dumpsys_gpu_pid'


def test_android_gfxinfo_source_and_raw_records_are_distinct_from_histograms():
    collector = make_collector({'dumpsys gfxinfo':FRAMESTATS_WITH_SENTINEL}, {'frame_time','frame_time_p95'})
    collector._frame_tracking = True
    collector._last_elapsed = 1.
    sample = collector.sample(2.)
    assert sample.values['frame_time'] == sample.values['frame_time_p95'] == 32.
    assert collector.diagnostics()['metric_sources']['frame_time'] == 'gfxinfo_framestats'
    assert len(sample.frames) == 1


def test_android_fps_estimate_never_invents_percentiles_or_raw_frames():
    response = {'dumpsys SurfaceFlinger --timestats -dump':SURFACE_TEMPLATE.format(frames=100,average=60,smooth=80).split('present2present')[0]}
    collector = make_collector(response, {'fps','frame_time','frame_time_p95'})
    collector._frame_tracking = True
    collector.sample(1)
    response['dumpsys SurfaceFlinger --timestats -dump'] = SURFACE_TEMPLATE.format(frames=160,average=60,smooth=80).split('present2present')[0]
    sample = collector.sample(2)
    assert sample.values['frame_time'] == pytest.approx(1000/60)
    assert sample.values.get('frame_time_p95') is None and not sample.frames
    assert collector.diagnostics()['metric_sources']['frame_time'] == 'fps_estimate'
    assert 'frame_time_p95' not in collector.diagnostics()['metric_sources']


def android_session():
    return {'id':'synthetic','device':{'platform':'android','id':'android:SYNTHETIC'},'target':{'name':'Synthetic'},
            'samples':{key:[{'x':i,'y':value} for i in range(1,4)] for key,value in
                       {'fps':10,'frame_time':100,'memory':512,'app_rss':600,'total_cpu':30,
                        'power':2,'battery_level':80,'app_gpu_memory':100,'dropped_frames':3}.items()}}


def test_android_report_preserves_data_and_distinguishes_sample_windows_and_sources():
    session = android_session();original = copy.deepcopy(session)
    # The report adapter intentionally consumes its private decoded input to
    # avoid retaining two full schemas. Caller-owned records stay untouched.
    payload = _report_payload(copy.deepcopy(session),screenshot_mode='none')
    assert not payload['measurement']['source_tracking_available']
    assert any('未保留实际来源' in n for n in payload['notes'])
    groups = {g['key']:g for g in payload['groups']}
    assert groups['power']['keys'] == ['power']
    assert groups['battery_level']['keys'] == ['battery_level']
    assert groups['system_cpu']['keys'] == ['total_cpu']
    assert 'Budget Estimate' in payload['metrics']['dropped_frames']['title']
    assert '回退' in payload['metrics']['app_gpu_memory']['note']
    assert session == original
    session['telemetry'] = {'collector':{'metric_source_counts':{'frame_time':{'surface_histogram':3}}}}
    assert _report_payload(copy.deepcopy(session),screenshot_mode='none')['measurement']['source_counts']['frame_time'] == {'surface_histogram':3}
    provenance = metric_provenance('android', ['gpu','app_gpu_memory','cpu_throttling'])
    assert '设备' in provenance['gpu']['scope']
    assert 'DVFS' in provenance['cpu_throttling']['note']


def test_android_reopen_does_not_reselect_and_history_restores_preferences(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'WORK_ROOT',tmp_path)
    monkeypatch.setattr(module,'discover_all',lambda:[])
    controller = module.AppController()
    controller._device_models = [Device('android:SYNTHETIC',Platform.ANDROID,'Synthetic')]
    controller._selected_device = 0
    try:
        controller.setMetricEnabled('native_heap',True)
        controller.setMetricEnabled('java_heap',False)
        controller.setChartVisible('memory_detail',True)
        controller.setChartVisible('memory_detail',False)
        controller.setChartVisible('memory_detail',True)
        assert 'java_heap' not in controller._enabled_metrics()
        previous = set(controller._preferences.visible_charts)
        monkeypatch.setattr(module,'load_session',lambda root,key:android_session())
        controller.openSession('synthetic')
        assert 'system_cpu' in {r['key'] for r in controller.chartCatalog}
        controller.closeSessionView()
        assert controller._preferences.visible_charts == previous
    finally:
        controller.shutdown()
