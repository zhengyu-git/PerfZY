from PySide6.QtCore import QSettings
import pytest

from perfzy.application import controller as module
from perfzy.application.charts import (DEFAULT_CHART_ORDER, WINDOWS_VISIBLE_CHARTS,
                                       build_chart_catalog, group_key_for_metric, groups_with_data)
from perfzy.application.metric_status import update_windows_status
from perfzy.application.preferences import Preferences, WINDOWS_DEFAULT_METRICS, WINDOWS_DEFAULT_VISIBLE_CHARTS
from perfzy.domain.metrics import METRIC_BY_KEY, METRICS
from perfzy.domain.models import Device, Platform


def test_compact_migration_is_recoverable_and_preserves_mobile(tmp_path):
    path = tmp_path / 'preferences.ini'
    settings = QSettings(str(path), QSettings.IniFormat)
    settings.setValue('metrics/windows/enabled', ['fps', 'app_kernel_cpu', 'total_cpu'])
    settings.setValue('metrics/android/enabled', ['fps', 'native_heap'])
    settings.setValue('charts/visible', ['cpu', 'network'])
    # Isolate the Windows migration: Android has already adopted its layout.
    settings.setValue('migrations/android_layout_v1', True)
    settings.setValue('metrics/android/compact_enabled', ['fps', 'native_heap'])
    settings.setValue('charts/android_visible', ['cpu', 'network'])
    settings.sync()
    preferences = Preferences(path)
    assert preferences.enabled_for(Platform.WINDOWS) == set(WINDOWS_DEFAULT_METRICS)
    assert preferences.enabled_for(Platform.ANDROID) == {'fps', 'native_heap'}
    assert preferences.visible_charts == {'cpu', 'network'}
    assert preferences.windows_visible_charts == set(WINDOWS_DEFAULT_VISIBLE_CHARTS)
    assert set(preferences._settings.value('migrations/windows_layout_v3_previous_enabled')) == {'fps', 'app_kernel_cpu', 'total_cpu'}
    preferences.enabled_for(Platform.WINDOWS).clear()
    preferences.windows_visible_charts.clear()
    preferences.save()
    reloaded = Preferences(path)
    assert not reloaded.enabled_for(Platform.WINDOWS)
    assert not reloaded.windows_visible_charts


def test_older_process_saving_old_selection_cannot_undo_compact_choice(tmp_path):
    path = tmp_path / 'preferences.ini'
    preferences = Preferences(path)
    preferences.set_metric_enabled(Platform.WINDOWS, 'fps', False)
    preferences.save()
    old = QSettings(str(path), QSettings.IniFormat)
    old.setValue('metrics/windows/enabled', ['fps', 'app_kernel_cpu', 'total_cpu'])
    old.sync()
    assert Preferences(path).enabled_for(Platform.WINDOWS) == set(WINDOWS_DEFAULT_METRICS) - {'fps'}


def test_windows_charts_have_one_scope_and_one_unit():
    catalog = build_chart_catalog(Platform.WINDOWS, DEFAULT_CHART_ORDER, set(), set(), 8)
    seen = []
    for chart in catalog:
        seen.extend(chart['metricKeys'])
        assert len({METRIC_BY_KEY[key].unit for key in chart['metricKeys']}) == 1, chart['key']
        assert all(group_key_for_metric(key, Platform.WINDOWS) == chart['key'] for key in chart['metricKeys'])
    expected = {item.key for item in METRICS if Platform.WINDOWS in item.platforms
                and not item.key.startswith('cpu_core_')}
    assert expected <= set(seen)
    assert len(seen) == len(set(seen))
    rows = {row['key']: row for row in catalog}
    assert rows['cpu']['metricKeys'] == ['app_cpu']
    assert rows['memory']['metricKeys'] == ['memory', 'private_memory']
    assert rows['system_memory']['tier'] == rows['network']['tier'] == 'system'
    assert rows['cpu_detail']['tier'] == 'advanced'
    assert {row['key'] for row in catalog if row['tier'] == 'basic'} == set(WINDOWS_VISIBLE_CHARTS) | {'jank_events'}


def test_saved_windows_metrics_use_new_groups_without_changing_mobile():
    samples = {'total_cpu': [1], 'app_user_cpu': [1], 'jank': [1], 'available_memory': [1]}
    assert groups_with_data(samples, Platform.WINDOWS) == {'system_cpu', 'cpu_detail', 'stutter', 'system_memory'}
    assert group_key_for_metric('total_cpu', Platform.ANDROID) == 'system_cpu'


def test_availability_missing_is_not_zero_and_recovers():
    state, misses = {}, {}
    for _ in range(4):
        state = update_windows_status(state, misses, ['app_gpu_usage'], {}, {})
        assert state['app_gpu_usage']['state'] == 'checking'
    state = update_windows_status(state, misses, ['app_gpu_usage'], {}, {})
    assert state['app_gpu_usage']['state'] == 'unavailable'
    state = update_windows_status(state, misses, ['app_gpu_usage'], {'app_gpu_usage': {'y': 0}}, {})
    assert state['app_gpu_usage']['state'] == 'available'
    assert misses['app_gpu_usage'] == 0


def test_fallback_disables_even_unselected_display_counters():
    state = update_windows_status({}, {}, ['fps'], {'fps': {'y': 60}},
                                  {'unavailable_frame_metrics': ['gpu_time', 'dropped_frames']})
    assert state['gpu_time']['state'] == state['dropped_frames']['state'] == 'unavailable'
    assert state['fps']['state'] == 'available'


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'WORK_ROOT', tmp_path)
    monkeypatch.setattr(module, 'discover_all', lambda: [])
    instance = module.AppController()
    instance._device_models = [Device('windows:local', Platform.WINDOWS, '本机')]
    instance._selected_device = 0
    yield instance
    instance.shutdown()


def test_showing_chart_never_selects_previously_disabled_metrics(controller):
    controller.setMetricEnabled('app_user_cpu', True)
    controller.setMetricEnabled('app_kernel_cpu', False)
    controller.setChartVisible('cpu_detail', True)
    controller.setChartVisible('cpu_detail', False)
    controller.setChartVisible('cpu_detail', True)
    assert 'app_user_cpu' in controller._enabled_metrics()
    assert 'app_kernel_cpu' not in controller._enabled_metrics()
    controller.setMetricEnabled('app_user_cpu', False)
    controller.setChartVisible('cpu_detail', False)
    controller.setChartVisible('cpu_detail', True)
    assert not ({'app_user_cpu', 'app_kernel_cpu'} & controller._active_metric_keys())


def test_unavailable_can_be_deselected_but_not_newly_enabled(controller):
    controller._metric_status['gpu_time'] = {'state': 'unavailable', 'label': '不可用', 'reason': '测试'}
    controller.setMetricEnabled('gpu_time', True)
    assert 'gpu_time' not in controller._enabled_metrics()
    controller._enabled_metrics().add('gpu_time')
    controller.setMetricEnabled('gpu_time', False)
    assert 'gpu_time' not in controller._enabled_metrics()


def test_historical_windows_metrics_reveal_new_groups_and_restore_live_preferences(controller, monkeypatch):
    before = set(controller._preferences.windows_visible_charts)
    selection = set(controller._enabled_metrics())
    payload = {'id': 'synthetic', 'device': {'id': 'windows:local'}, 'target': {'name': 'Game'},
               'samples': {'total_cpu': [{'x': 1, 'y': 40}], 'jank': [{'x': 1, 'y': 2}]}}
    monkeypatch.setattr(module, 'load_session', lambda root, key: payload)
    controller.openSession('synthetic')
    assert {'system_cpu', 'stutter'} <= {row['key'] for row in controller.chartCatalog}
    assert controller._current_metric_status()['fps']['label'] == '未记录'
    controller.closeSessionView()
    assert controller._preferences.windows_visible_charts == before
    assert controller._enabled_metrics() == selection


def test_accepted_samples_update_availability_and_recovery(controller):
    from perfzy.domain.models import Sample
    controller._collecting = True
    controller._active_collection_token = 1
    for elapsed in range(1, 7):
        controller._sample_inflight_token = 1
        values = {'app_gpu_usage': 0} if elapsed == 6 else {}
        controller._accept_sample({'token': 1, 'requested': ['app_gpu_usage'],
                                  'sample': Sample(elapsed, values), 'elapsed_ms': 1})
        if elapsed == 5:
            assert controller._metric_status['app_gpu_usage']['label'] == '暂不可用'
    assert controller._metric_status['app_gpu_usage']['label'] == '可用'
    assert controller._samples['app_gpu_usage'][-1]['y'] == 0
    controller._collecting = False


def test_move_visible_windows_chart_skips_collapsed_hidden_groups(controller):
    controller.moveChart('cpu', -1)
    assert [row['key'] for row in controller.chartCatalog][:3] == ['fps', 'cpu', 'frame']


def test_private_memory_missing_does_not_substitute_working_set():
    from types import SimpleNamespace
    from test_windows_collector import make_collector
    collector = make_collector(enabled={'memory', 'private_memory'})
    collector._process.memory_info = lambda: SimpleNamespace(rss=1024 * 1024, vms=2048 * 1024)
    result = collector.sample(1).values
    assert result['memory'] == 1
    assert 'private_memory' not in result
