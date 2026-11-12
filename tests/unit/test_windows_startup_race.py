"""First target FPS must not wait behind serial source recovery timeouts."""
import time

import pytest

from perfzy.collectors.windows import FRAME_EVENT_GRACE_SECONDS
from perfzy.collectors.windows_frame_source import FULL, DXGI
from perfzy.domain.provenance import metric_provenance
from test_windows_collector import make_collector, frame_row
from test_windows_frame_policy import OfflineCandidate, feed


@pytest.fixture
def startup(monkeypatch):
    collector=make_collector(enabled={'fps','frame_time','frame_jank','gpu_time','dropped_frame_rate'})
    collector._presentmon_owned=True
    original=collector._presentmon
    created=[]
    def candidate(profile):
        source=OfflineCandidate(profile,'PresentMon.exe',collector._frame_pids,
                                accepting=lambda:not collector._frame_capture_paused)
        created.append(source)
        return source
    monkeypatch.setattr(collector,'_new_frame_candidate',candidate)
    collector._start_startup_frame_source()
    yield collector,original,created[0]
    collector.stop()


def test_startup_accepts_only_verified_target_frames(startup):
    collector,original,fast=startup
    feed(fast,pid=9999)
    assert 'fps' not in collector.sample(1).values
    assert collector._capture_profile==FULL and not original.terminated
    # A live native source without unique source timestamps is also insufficient.
    for _ in range(4):fast._accept(frame_row(20))
    assert 'fps' not in collector.sample(2).values


def test_first_verified_fps_is_available_while_full_source_is_still_starting(startup):
    collector,original,fast=startup
    feed(fast)
    sample=collector.sample(1)
    assert sample.values['fps']==50
    assert all(row['source']=='DXGI ETW (compatibility)' for row in sample.frames)
    assert not {'gpu_time','dropped_frame_rate'} & sample.values.keys()
    assert collector._capture_profile==FULL and not original.terminated
    details=collector.diagnostics()
    assert details['mode']==DXGI and details['startup_compatibility']['running']
    assert details['presentation_fallback']
    provenance=metric_provenance('windows',{'fps','frame_jank'},collector_details=details)
    assert all('DXGI' in item['source'] for item in provenance.values())


def test_full_source_has_priority_and_restores_all_real_timings_after_fast_fps(startup):
    collector,original,fast=startup
    feed(fast)
    first=collector.sample(1)
    row=frame_row(25)
    row.update(CPUStartTime='500',GPUTime='7',DisplayedTime='25')
    collector._presentmon_tracks_display=True
    collector._present_rows.put(row)
    second=collector.sample(2)
    assert second.values['fps']==40 and second.values['gpu_time']==7
    assert second.values['dropped_frame_rate']==0
    assert second.frames[0]['source']=='PresentMon'
    assert first.frames[0]['stream_epoch']!=second.frames[0]['stream_epoch']
    assert fast._stopping and collector._startup_candidate is None
    assert not original.terminated and collector.diagnostics()['mode']==FULL


def test_full_source_wins_if_both_sources_are_ready_in_same_sample(startup):
    collector,original,fast=startup
    feed(fast)
    collector._present_rows.put(frame_row(10))
    sample=collector.sample(1)
    assert sample.values['fps']==100 and len(sample.frames)==1
    assert sample.frames[0]['source']=='PresentMon'
    assert fast._stopping and not original.terminated


def test_old_primary_backlog_cannot_replay_already_retained_fast_frame_times(startup):
    collector,original,fast=startup
    collector._presentmon_started_at=fast.started_at
    feed(fast)
    collector.sample(1)
    row=frame_row(25)
    row.update(CPUStartTime='50',SwapChainAddress='0x1')
    collector._present_rows.put(row)  # older than fast source's last 140 ms
    sample=collector.sample(2)
    assert not sample.frames and 'fps' not in sample.values
    assert not fast._stopping and not original.terminated
    row['CPUStartTime']='500'
    collector._present_rows.put(row)
    assert collector.sample(3).values['fps']==40
    assert fast._stopping and not original.terminated


def test_only_proven_fast_source_is_retained_after_full_warmup(startup):
    collector,original,fast=startup
    collector._presentmon_started_at-=FRAME_EVENT_GRACE_SECONDS+1
    feed(fast)
    sample=collector.sample(1)
    assert sample.values['fps']==50
    assert original.terminated and collector._active_candidate is fast
    assert collector._startup_candidate is None and not fast._stopping
    assert collector.diagnostics()['source_transitions'][-1]['verified_startup']
    assert collector._capture_profile==DXGI


def test_pause_and_delayed_pre_resume_frames_cannot_prove_startup_readiness(startup):
    collector,original,fast=startup
    fast.started_at-=10  # trace timestamps 100-140 ms are unambiguously old
    feed(fast)
    collector.sample(1)
    collector.set_capture_paused(True)
    feed(fast)
    assert fast.rows.empty() and collector._startup_verified_at==0
    collector.set_capture_paused(False)
    feed(fast)  # timestamps 100-140 ms from source start predate resume
    assert 'fps' not in collector.sample(2).values
    assert not original.terminated
    for index in range(3):
        row=frame_row(20)
        row.update(CPUStartTime=str((time.monotonic()-fast.started_at)*1000+index*20),SwapChainAddress='0x1')
        fast._accept(row)
    assert collector.sample(3).values['fps']==50


def test_disabling_frame_metrics_or_stopping_cleans_both_owned_sources(startup):
    collector,original,fast=startup
    collector.set_enabled_metrics(frozenset())
    collector.activate_pending_sources()
    assert fast._stopping and original.terminated
    assert collector._startup_candidate is None and collector._presentmon is None
    feed(fast)
    assert fast.rows.empty()


def test_stop_during_startup_cleans_both_sources_and_rejects_late_callbacks(startup):
    collector,original,fast=startup
    collector.stop()
    feed(fast)
    assert fast._stopping and original.terminated and fast.rows.empty()


def test_metrics_without_basic_frame_data_do_not_start_parallel_source(monkeypatch):
    collector=make_collector(enabled={'gpu_time','dropped_frame_rate'})
    calls=[]
    monkeypatch.setattr(collector,'_new_frame_candidate',lambda profile:calls.append(profile))
    collector._start_startup_frame_source()
    assert not calls and collector._startup_candidate is None
