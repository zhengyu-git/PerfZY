"""Collection-age, pending-mark and saved-scope boundaries, without devices."""
from pathlib import Path

import pytest

from test_controller import controller, select, WINDOWS_DEVICE
from perfzy.application import controller as module
from perfzy.application.preferences import Preferences
from perfzy.domain.collection_status import collection_metric_status
from perfzy.domain.models import Platform, Sample


def health(**options):
    defaults = dict(requested={'fps', 'memory'}, samples={}, statuses={}, fields={},
                    elapsed=20., collecting=True)
    defaults.update(options)
    return {row['group']: row for row in collection_metric_status(**defaults)}


def test_health_uses_actual_observation_age_and_preserves_valid_zero():
    result = health(samples={'fps': [{'x': 20, 'y': 60}]},
                    fields={'fps': {'last_elapsed': 2}, 'memory': {'last_elapsed': 18}})
    assert result['fps']['state'] == 'stale' and result['fps']['age_s'] == 18
    assert result['memory']['state'] == 'available'
    assert result['cpu']['state'] == 'disabled'
    zero = health(samples={'fps': [{'x': 20, 'y': 0}]})
    assert zero['fps']['state'] == 'available'
    assert zero['memory']['state'] == 'missing'


def test_pause_and_historical_status_are_not_live_freshness_claims():
    values = {'fps': [{'x': 2, 'y': 60}]}
    assert health(samples=values, paused=True)['fps']['state'] == 'paused'
    assert health(samples=values, collecting=False)['fps']['state'] == 'recorded'
    assert health(elapsed=1)['fps']['state'] == 'waiting'


def test_ios_held_cache_does_not_refresh_central_health_age(controller, monkeypatch):
    monkeypatch.setattr(controller, '_current_platform', lambda: Platform.IOS)
    monkeypatch.setattr(module.time, 'monotonic', lambda: 110.)
    controller._collecting, controller._started_at = True, 100.
    controller._active_collection_token = 1
    controller._sample_inflight_token = 1
    controller._accept_sample({'token': 1, 'sample': Sample(2, {'fps': 60}),
                              'requested': ['fps'], 'elapsed_ms': 1})
    controller._sample_inflight_token = 1
    controller._accept_sample({'token': 1, 'sample': Sample(10, {'fps': 60}, {'fps': 2}, held_keys=('fps',)),
                              'requested': ['fps'], 'elapsed_ms': 1})
    fps = next(row for row in controller.collectionMetricStatus if row['group'] == 'fps')
    assert fps['state'] == 'stale' and fps['age_s'] == 8
    assert len(controller._samples['fps']) == 1


def test_scene_pending_stop_requires_resolution_and_zero_duration_stays_pending(controller, monkeypatch):
    clock = [100.]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    controller._collecting, controller._started_at = True, 99.
    assert controller.toggleSceneMark('战斗')
    assert not controller.finishSceneMark()
    requests = []
    controller.sceneMarkStopRequested.connect(lambda: requests.append(True))
    controller.stopCollection()
    assert requests == [True] and controller.collecting and controller.markingScene
    clock[0] = 106.
    assert controller.finishSceneMark()
    assert not controller.markingScene
    assert controller._scenes[-1]['start'] == 1. and controller._scenes[-1]['end'] == 7.
    restored = Preferences(Path(controller._preferences._settings.fileName()))
    assert restored.scene_presets[0] == '战斗'


def test_scene_pause_excludes_pause_time_and_discard_keeps_completed_scenes(controller, monkeypatch):
    monkeypatch.setattr(module.time, 'monotonic', lambda: 120.)
    controller._collecting, controller._started_at = True, 100.
    controller._paused_total = 5.
    assert controller.toggleSceneMark('传送')
    controller._paused, controller._pause_started = True, 125.
    assert controller.finishSceneMark()
    assert controller._scenes[-1]['end'] == 20.
    controller._marking_scene_start = 20.
    controller.discardSceneMark()
    assert len(controller._scenes) == 1 and not controller.markingScene


def test_scope_setting_persists_but_history_and_active_capture_cannot_change_it(controller):
    select(controller, WINDOWS_DEVICE)
    controller.setWindowsResourceScope('tree')
    restored = Preferences(Path(controller._preferences._settings.fileName()))
    assert restored.windows_resource_scope == 'tree'
    controller._collecting = True
    controller.setWindowsResourceScope('selected')
    assert controller.windowsResourceScope == 'tree'
    controller._collecting = False
    controller._viewing_platform = Platform.WINDOWS
    controller._saved_telemetry = {}
    assert controller.windowsResourceScope == ''
    assert '未记录' in controller.windowsResourceScopeText
    controller.setWindowsResourceScope('selected')
    assert controller._preferences.windows_resource_scope == 'tree'
    controller._saved_telemetry = {'collector': {'process_scope': {'resource_scope': 'selected'}}}
    assert controller.windowsResourceScope == 'selected'


def test_scene_presets_remain_bounded_unique_and_portable(controller):
    for i in range(20):
        controller.rememberScenePreset('场景' + str(i))
    controller.rememberScenePreset('场景19')
    restored = Preferences(Path(controller._preferences._settings.fileName()))
    assert len(restored.scene_presets) == 12
    assert len(set(restored.scene_presets)) == 12
    assert restored.scene_presets[0] == '场景19'
