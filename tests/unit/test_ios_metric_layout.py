import copy
import pytest
from PySide6.QtCore import QSettings
from perfzy.application.charts import (DEFAULT_CHART_ORDER, IOS_VISIBLE_CHARTS,
                                       build_chart_catalog, group_key_for_metric)
from perfzy.application.preferences import Preferences, IOS_DEFAULT_METRICS
from perfzy.application.metric_status import update_ios_status
from perfzy.application import controller as module
from perfzy.domain.metrics import METRICS, METRIC_BY_KEY, platform_metric_title
from perfzy.domain.models import Platform, Device
from perfzy.domain.provenance import metric_provenance
from perfzy.collectors.ios import IOSCollector
from perfzy.reporting import _report_payload


def test_ios_layout_is_compact_complete_and_units_are_separate():
    rows = build_chart_catalog(Platform.IOS, DEFAULT_CHART_ORDER, IOS_VISIBLE_CHARTS, IOS_DEFAULT_METRICS, 6)
    keys = [key for row in rows for key in row['metricKeys']]
    expected = {metric.key for metric in METRICS if Platform.IOS in metric.platforms
                and (not metric.key.startswith('cpu_core_') or int(metric.key.split('_')[2]) < 6)}
    assert set(keys) == expected and len(keys) == len(set(keys))
    assert {row['key'] for row in rows if row['visible']} == set(IOS_VISIBLE_CHARTS)
    for row in rows:
        assert len({METRIC_BY_KEY[key].unit for key in row['metricKeys']}) == 1
        assert all(group_key_for_metric(key, Platform.IOS) == row['key'] for key in row['metricKeys'])
        assert all(metric['reason'] for metric in row['metrics'])
    assert {'jank', 'big_jank', 'small_jank', 'frame_time_p95', 'gpu_time'}.isdisjoint(keys)
    by_key = {row['key']: row for row in rows}
    assert by_key['memory']['metricKeys'] == ['ios_physical_footprint']
    assert by_key['virtual_memory']['metricKeys'] == ['ios_virtual_memory']
    assert by_key['network']['tier'] == 'system'
    assert by_key['frame']['tier'] == 'advanced'
    note = by_key['frame']['capabilityNote']
    assert all(name in note for name in ('Jank', 'Big Jank', 'Small Jank', 'FrameTimes'))
    assert '暂不可采集' in note and '1000/FPS' in note
    assert by_key['frame']['metricKeys'] == ['frame_time']
    assert 'Estimated' in by_key['frame']['title']


def test_ios_migration_preserves_android_windows_and_empty_selection(tmp_path):
    path = tmp_path / 'settings.ini'
    prefs = Preferences(path)
    windows = set(prefs.enabled_for(Platform.WINDOWS))
    settings = prefs._settings
    settings.remove('migrations/ios_layout_v1')
    settings.remove('metrics/ios/compact_enabled')
    settings.setValue('metrics/ios/enabled', ['fps', 'network_send', 'ios_virtual_memory'])
    prefs.enabled_for(Platform.ANDROID).clear()
    prefs.enabled_for(Platform.ANDROID).update({'fps', 'java_heap'})
    settings.setValue('metrics/android/compact_enabled', ['fps', 'java_heap'])
    settings.sync()
    prefs = Preferences(path)
    assert prefs.enabled_for(Platform.IOS) == set(IOS_DEFAULT_METRICS)
    assert prefs.enabled_for(Platform.ANDROID) == {'fps', 'java_heap'}
    assert prefs.enabled_for(Platform.WINDOWS) == windows
    assert set(prefs._settings.value('migrations/ios_layout_v1_previous_enabled')) == {'fps', 'network_send', 'ios_virtual_memory'}
    prefs.enabled_for(Platform.IOS).clear()
    prefs.ios_visible_charts.clear()
    prefs.save()
    assert not Preferences(path).enabled_for(Platform.IOS)
    assert not Preferences(path).ios_visible_charts


def test_ios_provenance_distinguishes_device_estimates_and_process():
    keys = {'fps','frame_time','gpu','ios_gpu_renderer','ios_gpu_allocated_memory','network_send',
            'power','temperature','ios_physical_footprint','ios_virtual_memory','disk_read','ios_interrupt_wakeups'}
    data = metric_provenance('ios', keys)
    for key in ('gpu','ios_gpu_renderer','ios_gpu_allocated_memory','network_send','power','temperature'):
        assert '目标进程' not in data[key]['scope']
    assert 'system.netBytesOut' in data['network_send']['source']
    assert '非逐帧' in data['frame_time']['method']
    assert data['ios_physical_footprint']['scope'] == data['disk_read']['scope'] == '目标进程'
    assert 'Estimated' in platform_metric_title('frame_time', Platform.IOS)
    assert '1000/FPS' in platform_metric_title('frame_time', Platform.IOS)
    assert 'Device' in platform_metric_title('gpu', Platform.IOS)


def test_subscription_diagnostics_is_read_only_and_detects_missing_fields():
    collector = IOSCollector()
    collector.set_enabled_metrics(frozenset({'ios_private_memory','ios_physical_footprint','app_cpu','network_send'}))
    collector._accept_stream_event({'type': 'subscription', 'process_fields': ['pid','name'], 'system_fields': ['netBytesOut']})
    data = collector.diagnostics()
    assert set(data['unsupported_metrics']) == {'ios_private_memory'}
    assert data['raw_frame_events'] is data['gpu_deep_counters'] is False
    collector._accept_stream_event({'type':'configuration', 'sysmon':True, 'interval':1000})
    assert not collector.diagnostics()['unsupported_metrics']


def test_ios_unavailable_recovers_and_zero_is_valid():
    status, misses = {}, {}
    for _ in range(5):
        status = update_ios_status(status, misses, ['gpu'], {}, {})
    assert status['gpu']['label'] == '暂不可用'
    status = update_ios_status(status, misses, ['gpu'], {'gpu': {'y': 0}}, {})
    assert status['gpu']['label'] == '可用'
    status = update_ios_status(status, misses, ['ios_private_memory'], {},
                               {'unsupported_metrics': {'ios_private_memory':'memRPrvt 未订阅'}})
    assert status['ios_private_memory']['label'] == '本轮未支持'
    assert 'memRPrvt' in status['ios_private_memory']['reason']


def test_battery_waits_for_slow_channel():
    status, misses = {}, {}
    for _ in range(10):
        status = update_ios_status(status, misses, ['power'], {}, {'sample_interval_ms': 500})
    assert status['power']['label'] == '检测中'


def test_renderer_is_not_substituted_for_total_gpu():
    from test_ios_collector import make_collector
    collector = make_collector({'gpu','ios_gpu_renderer'},
                               graphics={'Renderer Utilization %':40})
    values = collector.sample(1).values
    assert values['gpu'] is None
    assert values['ios_gpu_renderer'] == 40


def ios_session():
    return {'id':'synthetic', 'device':{'id':'ios:SYNTHETIC','platform':'ios'}, 'target':{'name':'Synthetic'},
            'samples':{key:[{'x':i,'y':value} for i in range(1,4)] for key,value in
                       {'fps':10,'frame_time':100,'gpu':50,'network_send':1,'ios_virtual_memory':99999,
                        'ios_physical_footprint':500,'ios_context_switches':2,'app_threads':10}.items()}}




def test_report_names_groups_notes_and_corrects_known_old_provenance():
    session = ios_session()
    old = {'network_send':{'source':'Instruments 进程网络计数器','scope':'目标进程'}}
    session['telemetry'] = {'provenance': old}
    payload = _report_payload(session, screenshot_mode='none')
    assert payload['measurement']['frame_time_estimated']
    assert 'Estimated' in payload['metrics']['frame_time']['title']
    assert '1000/FPS' in payload['metrics']['frame_time']['title']
    assert payload['recorded_provenance'] == old
    assert 'system.netBytesOut' in payload['metric_provenance']['network_send']['source']
    groups = {group['key']:group for group in payload['groups']}
    assert groups['virtual_memory']['keys'] == ['ios_virtual_memory']
    assert groups['process_rates']['keys'] == ['ios_context_switches']
    assert 'System Network' in groups['network']['title'] and 'Shared' in groups['network']['title']
    assert any('非 PID' in note for note in payload['notes'])


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'WORK_ROOT', tmp_path)
    monkeypatch.setattr(module, 'discover_all', lambda: [])
    instance = module.AppController()
    instance._device_models = [Device('ios:SYNTHETIC', Platform.IOS, 'Synthetic iPhone')]
    instance._devices = [{'id':'ios:SYNTHETIC','name':'Synthetic iPhone'}]
    instance._selected_device = 0
    yield instance
    instance.shutdown()


def test_ios_chart_reopen_never_reselects_and_history_is_reversible(controller, monkeypatch):
    controller.setMetricEnabled('ios_private_memory', True)
    controller.setMetricEnabled('app_rss', False)
    controller.setChartVisible('memory_detail', True)
    controller.setChartVisible('memory_detail', False)
    controller.setChartVisible('memory_detail', True)
    assert 'app_rss' not in controller._enabled_metrics()
    before = set(controller._preferences.ios_visible_charts)
    monkeypatch.setattr(module, 'load_session', lambda root, key: ios_session())
    controller.openSession('synthetic')
    assert 'virtual_memory' in {row['key'] for row in controller.chartCatalog}
    controller.closeSessionView()
    assert controller._preferences.ios_visible_charts == before
