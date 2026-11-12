"""Public collection behavior across profiles, process scopes, and metric changes."""
import time
import subprocess

import pytest

from perfzy.collectors.windows import FRAME_EVENT_GRACE_SECONDS
from perfzy.collectors.windows_frame_source import (
    FrameCandidate, FULL, APPLICATION, DXGI, PROBE_SECONDS, presentmon_command)
from perfzy.domain.provenance import metric_provenance
from test_windows_collector import make_collector, frame_row, FakePresentMon
from test_dxgi_etw import FakeDxgi


class OfflineCandidate(FrameCandidate):
    def start(self):
        self.started_at = time.monotonic()
        self.probe_started_at = self.started_at
        if self.profile == DXGI:
            self.native = FakeDxgi(self._accept, self.pids)
            self.native.start()
            self.session, self.started_at = self.native.session, self.native.started_at
        else:
            self.process = FakePresentMon()


@pytest.fixture
def source_policy(monkeypatch):
    collector = make_collector(enabled={'fps', 'frame_time', 'gpu_time', 'dropped_frame_rate'})
    collector._presentmon_owned = True
    collector._presentmon_started_at = time.monotonic() - FRAME_EVENT_GRACE_SECONDS - 1
    original, created = collector._presentmon, []
    def candidate(profile):
        result = OfflineCandidate(profile, 'PresentMon.exe', collector._frame_pids,
                                  accepting=lambda: not collector._frame_capture_paused)
        created.append(result)
        return result
    monkeypatch.setattr(collector, '_new_frame_candidate', candidate)
    monkeypatch.setattr(collector, '_presentmon_path', lambda: 'PresentMon.exe')
    monkeypatch.setattr('perfzy.collectors.windows.subprocess.Popen', lambda *args, **kwargs: FakePresentMon())
    yield collector, original, created
    collector.stop()


def feed(candidate, *, pid=4242, runtime='DXGI', frame_type='Application'):
    for index in range(3):
        row = frame_row(20, pid=pid)
        row.update(SwapChainAddress='0x1', CPUStartTime=str(100 + index * 20),
                   PresentRuntime=runtime, FrameType=frame_type, GPUTime='8')
        candidate._accept(row)


def reach_native(collector):
    collector.sample(1)
    application = collector._probing_candidate
    assert application.profile == APPLICATION
    application.probe_started_at -= PROBE_SECONDS + 1
    collector.sample(2)
    native = collector._probing_candidate
    assert native.profile == DXGI
    feed(native)
    collector.sample(3)
    assert collector._capture_profile == DXGI
    return native


def test_successful_launch_or_other_process_frames_cannot_replace_primary(source_policy):
    collector, original, created = source_policy
    collector._presentmon_read_rows = 9999  # activity from other applications
    collector.sample(1)
    assert len(created) == 1 and created[0].profile == APPLICATION
    assert not original.terminated and collector._capture_profile == FULL
    feed(created[0], pid=9999)
    collector.sample(2)
    assert not created[0].ready and not original.terminated
    feed(created[0])
    collector.sample(3)
    assert original.terminated and collector._capture_profile == APPLICATION
    result = collector.sample(4)
    assert result.values['fps'] == 50
    assert 'gpu_time' not in result.values and 'dropped_frame_rate' not in result.values


def test_native_is_promoted_only_after_target_frames_and_preserves_missing_metrics(source_policy):
    collector, original, created = source_policy
    native = reach_native(collector)
    assert original.terminated
    result = collector.sample(4)
    assert result.values['fps'] == 50 and result.frames[0]['source'] == 'DXGI ETW (compatibility)'
    assert 'gpu_time' not in result.values and 'dropped_frame_rate' not in result.values
    details = collector.diagnostics()
    assert details['mode'] == DXGI and details['source_transitions'][-1]['verified_target_rows'] == 3
    provenance = metric_provenance('windows', {'fps', 'frame_jank'}, collector_details=details)
    assert all('DXGI' in item['source'] for item in provenance.values())
    assert '正在等待' not in collector.status
    assert native.native.pids() == {4242}


def test_primary_recovery_wins_over_an_already_ready_probe(source_policy):
    collector, original, created = source_policy
    collector.sample(1)
    probe = collector._probing_candidate
    feed(probe)
    collector._present_rows.put(frame_row(25))
    result = collector.sample(2)
    assert result.values['fps'] == 40
    assert collector._probing_candidate is None and probe._stopping
    assert not original.terminated and collector._capture_profile == FULL


def test_failed_candidates_leave_generic_profile_and_do_not_fake_fps(source_policy):
    collector, original, created = source_policy
    collector.sample(1)
    app = collector._probing_candidate
    app.probe_started_at -= PROBE_SECONDS + 1
    collector.sample(2)
    native = collector._probing_candidate
    native.probe_started_at -= PROBE_SECONDS + 1
    result = collector.sample(3)
    assert 'fps' not in result.values
    assert collector._capture_profile == FULL and collector._probing_candidate is None
    assert app._stopping and native._stopping
    assert len(collector.diagnostics()['source_probe_history']) == 2


def test_generic_profile_can_recover_from_native_with_other_runtime_rows(source_policy):
    collector, original, created = source_policy
    native = reach_native(collector)
    collector.sample(4)
    collector._last_valid_frame_at = collector._presentmon_started_at = time.monotonic() - FRAME_EVENT_GRACE_SECONDS - 1
    collector.sample(5)
    universal = collector._probing_candidate
    assert universal.profile == FULL
    feed(universal, runtime='Other')  # kernel-correlated Vulkan/OpenGL-style rows
    collector.sample(6)
    assert native._stopping and collector._capture_profile == FULL
    result = collector.sample(7)
    assert result.values['fps'] == 50 and result.values['gpu_time'] == 8


def test_metric_changes_and_pause_of_frame_metrics_keep_one_verified_profile(source_policy):
    collector, original, created = source_policy
    native = reach_native(collector)
    collector.sample(4)
    collector.set_enabled_metrics(frozenset({'fps', 'gpu_busy', 'memory'}))
    collector.activate_pending_sources()
    assert collector._active_candidate is native and not native._stopping
    collector.set_enabled_metrics(frozenset())
    collector.activate_pending_sources()
    assert native._stopping and collector._active_candidate is None
    assert collector.status == ''
    collector.set_enabled_metrics(frozenset({'fps'}))
    collector.activate_pending_sources()
    assert collector._active_candidate is not None
    assert collector._active_candidate.profile == DXGI
    assert collector.diagnostics()['source_transitions'][-1]['verified_resume']


def test_no_candidate_data_leaks_after_collection_stop(source_policy):
    collector, original, created = source_policy
    collector.sample(1)
    pending = collector._probing_candidate
    collector.stop()
    feed(pending)
    assert pending.valid_rows == 0 and pending.rows.empty()
    assert collector._active_candidate is None and collector._probing_candidate is None


def test_probe_validation_rechecks_live_process_scope_and_invalid_frames():
    pids = {4242}
    candidate = OfflineCandidate(APPLICATION, '', lambda: pids)
    candidate.start()
    feed(candidate, frame_type='Repeated')
    for invalid in ('nan', 'inf', '-1', '0'):
        candidate._accept({'ProcessID': '4242', 'FrameTime': invalid})
    assert not candidate.ready and candidate.valid_rows == 0
    feed(candidate, runtime='D3D9')
    assert candidate.ready
    pids.clear()
    assert not candidate.ready and list(candidate.take_rows()) == []
    candidate.stop()


def test_full_profile_has_no_api_narrowing_flags_for_any_metric_subset():
    command = presentmon_command('PresentMon.exe', 'our-own-session', FULL)
    assert '--no_track_gpu' not in command and '--no_track_display' not in command
    assert '--process_name' not in command  # no game-name rules
    narrow = presentmon_command('PresentMon.exe', 'another-own-session', APPLICATION)
    assert '--no_track_gpu' in narrow and '--no_track_display' in narrow


def test_replayed_rows_do_not_prove_that_a_candidate_has_live_frames():
    candidate = OfflineCandidate(APPLICATION, '', lambda: {4242})
    candidate.start()
    repeated = {'ProcessID': '4242', 'FrameTime': '20', 'CPUStartTime': '100'}
    for _ in range(3):
        candidate._accept(repeated)
    assert not candidate.ready
    feed(candidate, runtime='Other')
    assert candidate.ready
    candidate.stop()


def test_source_control_waits_until_resource_snapshot_and_audit_are_complete(monkeypatch):
    collector = make_collector(enabled={'fps', 'app_cpu'})
    collector._present_rows.put(frame_row(20))
    snapshots = []
    original = collector.audited_sample
    def audited(*args, **kwargs):
        snapshots.append('snapshot')
        return original(*args, **kwargs)
    def source_control(now):
        assert snapshots == ['snapshot']
        # Switching after this point must not relabel the sample just produced.
        collector._dxgi_source = object()
    monkeypatch.setattr(collector, 'audited_sample', audited)
    monkeypatch.setattr(collector, '_recover_silent_frame_source', source_control)
    result = collector.sample(1)
    assert result.values['fps'] == 50 and 'app_cpu' in result.values
    assert result.frames[0]['source'] == 'PresentMon'
    collector._dxgi_source = None
    collector.stop()


def test_candidate_cleanup_remains_owned_even_when_process_wait_times_out(monkeypatch):
    class SlowExit(FakePresentMon):
        pid = 987654
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired('our-test-source', timeout)
    commands = []
    monkeypatch.setattr('perfzy.collectors.windows_frame_source.subprocess.run',
        lambda args, **kwargs: commands.append(args))
    candidate = FrameCandidate(APPLICATION, '', lambda: {4242})
    candidate.process = SlowExit()
    candidate.stop()
    assert commands == [['logman', 'stop', candidate.session, '-ets']]
    assert candidate.messages and not candidate.running


def test_failed_candidate_start_is_cleaned_up_without_stopping_primary(source_policy, monkeypatch):
    collector, original, created = source_policy
    class FailedStart(OfflineCandidate):
        def start(self):
            raise OSError('test source unavailable')
    candidate = FailedStart(DXGI, '', collector._frame_pids)
    monkeypatch.setattr(collector, '_new_frame_candidate', lambda profile: candidate)
    assert not collector._begin_frame_candidate(DXGI)
    assert candidate._stopping and not original.terminated
    assert collector._probing_candidate is None


def test_pause_resume_excludes_buffered_frames_and_delayed_pause_events(source_policy, monkeypatch):
    collector, original, created = source_policy
    active = reach_native(collector)
    collector.sample(4)
    began = active.started_at
    monkeypatch.setattr('time.monotonic', lambda: began + 10)
    feed(active)
    collector.set_capture_paused(True)
    assert active.rows.empty()
    feed(active)
    assert active.rows.empty()
    monkeypatch.setattr('time.monotonic', lambda: began + 20)
    collector.set_capture_paused(False)
    active._accept({'ProcessID': '4242', 'FrameTime': '5000', 'CPUStartTime': '15000'})
    assert active.rows.empty()  # delivered after resume, rendered during pause
    monkeypatch.setattr('time.monotonic', lambda: began + 20.2)
    for stamp in (20100, 20120, 20140):
        active._accept({'ProcessID': '4242', 'FrameTime': '20', 'CPUStartTime': str(stamp)})
    result = collector.sample(5)
    assert result.values['fps'] == 50
    assert len(result.frames) == 3
    assert collector._active_candidate is active and collector._probing_candidate is None
