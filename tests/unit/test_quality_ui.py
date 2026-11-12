import json
import time

from test_controller import controller
from perfzy.storage.sessions import LiveSessionWriter
from perfzy.domain.models import Sample


def test_test_context_is_sanitized_and_cannot_change_during_collection(controller):
    controller.setTestContext('  2.9.1  ','  High / 60 FPS  ')
    assert controller.testBuildLabel=='2.9.1'
    assert controller._capture_settings()['conditions']=='High / 60 FPS'
    controller._collecting=True
    controller.setTestContext('changed','changed')
    assert controller.testBuildLabel=='2.9.1'
    controller._collecting=False


def test_connection_check_does_not_run_during_active_collection(controller):
    controller._collecting=True
    controller.runConnectionCheck()
    assert not controller.diagnosticRunning
    controller._collecting=False


def test_capture_setting_changes_leave_a_bounded_history(controller,tmp_path):
    spool=LiveSessionWriter(tmp_path,'settings',manifest={'capture_settings':controller._capture_settings()})
    controller._session_spool=spool
    controller._collecting=True
    controller._started_at=time.monotonic()
    for index in range(105):
        controller._preferences.sample_interval_ms=1000+index*100
        controller._record_capture_settings()
    assert len(spool._manifest['capture_settings_changes'])==100
    assert spool._manifest['capture_settings_changes_truncated']
    controller._collecting=False
    controller._session_spool=None
    spool.close()


def test_comparison_rejects_same_session_and_invalid_identifiers_before_io(controller):
    messages=[]
    controller.notificationRequested.connect(lambda *args:messages.append(args))
    controller.generateComparisonReport('same','same')
    controller.generateComparisonReport('../escape','candidate')
    assert len(messages)==2
    assert not controller.reportGenerating


def test_live_spool_retains_audit_details_without_repeating_held_observations(controller, tmp_path):
    spool = LiveSessionWriter(tmp_path, 'audit_merge', manifest={
        'capture_settings': controller._capture_settings()})
    controller._session_spool = spool
    controller._collecting = True
    controller._active_collection_token = 1
    samples = (
        Sample(10, {'fps': 30, 'app_cpu': None}, {'fps': 8},
               raw_values={'fps': 300, 'app_cpu': None},
               adjustments={'fps': 'spike_rejected'}, sources={'fps': 'Graphics'}),
        Sample(11, {'fps': 30, 'app_cpu': 15}, {'fps': 8, 'app_cpu': 10.5},
               held_keys=frozenset({'fps'}), raw_values={'fps': 30, 'app_cpu': 15}),
    )
    for sample in samples:
        controller._sample_inflight_token = 1
        controller._accept_sample({'token': 1, 'sample': sample,
                                  'requested': ['fps', 'app_cpu'], 'elapsed_ms': 12.5})
    spool.close()
    controller._session_spool = None
    controller._collecting = False
    audit = [json.loads(row) for row in (spool.directory.parent / 'audit.jsonl').read_text(
        encoding='utf-8').splitlines()]
    assert audit[0]['raw_values']['fps'] == 300
    assert audit[0]['values']['app_cpu'] is None
    assert audit[0]['adjustments']['fps'] == 'spike_rejected'
    assert audit[0]['sources']['fps'] == 'Graphics'
    assert audit[0]['collector_ms'] == 12.5
    assert audit[1]['held_keys'] == ['fps']
    fps = [json.loads(row) for row in spool.sample_files['fps'].read_text(
        encoding='utf-8').splitlines()]
    assert fps == [{'x': 8, 'y': 30}]
    assert controller._samples['fps'] == [{'x': 8, 'y': 30}]
    assert controller._samples['app_cpu'] == [{'x': 10.5, 'y': 15}]
