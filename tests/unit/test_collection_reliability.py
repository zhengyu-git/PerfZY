"""Offline, source-shaped regression cases; no phone or game is operated."""
import io
import queue
import copy
from unittest.mock import Mock
import pytest
from perfzy.collectors.frame_stream import RenderStreamSelector
from perfzy.collectors.windows import WindowsCollector
from perfzy.collectors.android import TelemetryReadError
from perfzy.domain.models import Sample
from perfzy.domain.validation import sample_points
from perfzy.domain.collection_quality import collection_quality_notes
from perfzy.application.metric_status import update_windows_status
from perfzy.reporting import _report_payload
from test_windows_collector import make_collector, v2_frame_row, frame_row, FakeChild
from test_android_collector import make_collector as android_collector, SURFACE_TEMPLATE
from test_ios_freshness import stream
from test_controller import controller


def stream_rows(pid, chain, count, ms=20):
    return [dict(frame_row(ms,pid=pid), SwapChainAddress=chain) for _ in range(count)]


def test_independent_swapchains_are_not_averaged_into_fps():
    collector = make_collector(stream_rows(4242,'main',60,16)+stream_rows(4242,'video',30,33))
    sample = collector.sample(1)
    assert sample.values['frame_time'] == 16
    assert sample.values['fps'] == 62.5
    assert len(sample.frames) == 60
    diag = collector.diagnostics()
    assert diag['render_stream']['swapchain'] == 'main'
    assert diag['render_stream']['excluded_other_stream_rows'] == 30


def test_stream_selection_is_sticky_but_sustained_challenger_can_take_over():
    collector = make_collector(stream_rows(4242,'main',10,20))
    collector.sample(1)
    for second in (2,3):
        for row in stream_rows(4242,'main',2,20)+stream_rows(4242,'new',10,40):
            collector._present_rows.put(row)
        assert collector.sample(second).values['fps'] == 50
    for row in stream_rows(4242,'main',2,20)+stream_rows(4242,'new',10,40):
        collector._present_rows.put(row)
    assert collector.sample(4).values['fps'] == 25
    assert collector.diagnostics()['render_stream']['switches'] == 1


def test_missing_old_stream_switches_immediately_and_resets_low_tail():
    collector = make_collector(stream_rows(4242,'old',2,100))
    collector.sample(1)
    for row in stream_rows(4242,'new',2,10):
        collector._present_rows.put(row)
    values = collector.sample(2).values
    assert values['fps'] == values['fps_1_low'] == 100


def test_equal_frame_durations_are_real_but_duplicate_timestamps_are_not():
    rows = [dict(v2_frame_row(20),CPUStartTime=stamp) for stamp in ('1000','1000','900','1020')]
    collector = make_collector(rows)
    assert len(collector.sample(2).frames) == 2
    diag = collector.diagnostics()['integrity']
    assert diag['duplicate_rows'] == diag['out_of_order_rows'] == 1


def test_reader_filters_known_unrelated_pids_before_the_bounded_queue():
    collector = WindowsCollector()
    collector._frame_pid_filter = frozenset({4242})
    collector._present_rows = queue.Queue(maxsize=2)
    collector._read_presentmon(io.StringIO('Application,ProcessID,FrameTime\nother.exe,9999,16\ngame.exe,4242,20\n'))
    assert collector._present_rows.qsize() == 1
    assert collector.diagnostics()['integrity']['filtered_other_pid_rows'] == 1


def test_overflow_is_reported_and_never_claims_completeness():
    collector = make_collector(enabled={'fps'})
    collector._present_rows = queue.Queue(maxsize=2)
    for _ in range(3): collector._enqueue_present_row(frame_row(20))
    sample = collector.sample(1)
    diag = collector.diagnostics()
    assert diag['integrity']['queue_overflow_rows'] == 1
    assert diag['integrity']['completeness'] == 'known_loss'
    assert any('不完整' in note for note in collection_quality_notes(diag))
    state = update_windows_status({}, {}, ['fps'], sample_points(sample), diag)
    assert state['fps']['label'] == '可用·有丢失'


def test_etw_loss_evidence_survives_later_stderr_messages():
    collector = WindowsCollector()
    collector._read_presentmon_errors(io.StringIO('warning: events were lost\nnormal message\n'))
    assert collector.diagnostics()['integrity']['etw_loss_reported']


@pytest.mark.parametrize('marker',['','bad','nan','-1'])
def test_empty_or_malformed_display_column_is_unknown_not_zero_drop(marker):
    collector = make_collector([v2_frame_row(20,displayed=marker)])
    sample = collector.sample(1)
    assert sample.values.get('dropped_frame_rate') is None
    assert sample.frames[0]['dropped'] is None
    assert collector.diagnostics()['integrity']['last_window']['unknown_display_status_frames'] == 1


def test_explicit_not_dropped_overrides_na_display_timing():
    collector = make_collector([v2_frame_row(20,displayed='NA',Dropped='0')])
    sample = collector.sample(1)
    assert sample.values['dropped_frames'] == 0
    assert sample.frames[0]['dropped'] is False


def test_gpu_scope_matches_selected_pid_while_child_render_scope_is_reported():
    collector = make_collector([frame_row(20,pid=5555)],enabled={'fps','app_gpu_usage'},children=[FakeChild(5555)])
    collector._gpu_counters.sample = Mock(return_value={'app_gpu_usage':20})
    collector.sample(1)
    collector._gpu_counters.sample.assert_called_once_with({4242},collector._enabled)
    notes = collection_quality_notes(collector.diagnostics())
    assert any('子进程' in text and '不包含' in text for text in notes)


def test_disabled_gpu_tracking_does_not_turn_csv_zero_into_gpu_timing():
    collector = make_collector([v2_frame_row(20,GPUBusy=0)],enabled={'fps','gpu_busy'})
    collector._presentmon_tracks_gpu = False
    sample = collector.sample(1)
    assert 'gpu_busy' not in sample.values
    assert sample.frames[0]['gpu_busy'] is None


def test_explicit_generated_frames_do_not_inflate_application_fps():
    collector = make_collector([dict(v2_frame_row(20),FrameType='Application'),
                               dict(v2_frame_row(10),FrameType='Intel XeSS-FG')])
    sample = collector.sample(1)
    assert sample.values['fps'] == 50 and len(sample.frames) == 1
    assert collector.diagnostics()['integrity']['excluded_explicit_generated_frame_rows'] == 1


def surface(frames):
    return SURFACE_TEMPLATE.format(frames=frames,average=60,smooth=frames)


def test_android_disable_enable_rebases_instead_of_spanning_the_off_period():
    response = {'--timestats -dump':surface(100)}
    collector = android_collector(response,{'fps'})
    collector._frame_tracking = True
    collector.sample(1)
    collector.set_enabled_metrics(frozenset());collector.activate_pending_sources()
    collector.set_enabled_metrics(frozenset({'fps'}));collector.activate_pending_sources()
    response['--timestats -dump'] = surface(1000)
    assert collector.sample(100).values['fps'] is None
    response['--timestats -dump'] = surface(1060)
    assert collector.sample(101).values['fps'] == 60


def test_android_counter_reset_requires_new_baseline_not_zero_fps():
    response = {'--timestats -dump':surface(100)}
    collector = android_collector(response,{'fps'})
    collector.sample(1)
    response['--timestats -dump'] = surface(10)
    assert collector.sample(2).values['fps'] is None
    response['--timestats -dump'] = surface(70)
    assert collector.sample(3).values['fps'] == 60
    assert collector.diagnostics()['frame_integrity']['counter_resets'] == 1


def test_android_transport_gap_rebases_and_does_not_make_a_gap_average(monkeypatch):
    response = {'--timestats -dump':surface(100)}
    collector = android_collector(response,{'fps'})
    collector.sample(1)
    original = collector._sample_impl
    monkeypatch.setattr(collector,'_sample_impl',Mock(side_effect=TelemetryReadError('gap')))
    assert collector.sample(2).values == {'fps':None}
    monkeypatch.setattr(collector,'_sample_impl',original)
    response['--timestats -dump'] = surface(280)
    assert collector.sample(4).values['fps'] is None
    assert collector.diagnostics()['frame_integrity']['last_reset_reason'] == 'transport_read_gap'


def test_android_fps_only_collection_also_checks_replacement_pid():
    collector = android_collector({'--timestats -dump':surface(100),'pidof':'5678'}, {'fps'})
    collector.sample(1)
    assert collector._pid == '5678'
    assert collector.diagnostics()['frame_integrity']['last_reset_reason'] == 'target_pid_changed'


def test_held_valid_cache_does_not_become_a_new_persistable_point():
    sample = Sample(2,{'fps':60,'app_cpu':0},held_keys=frozenset({'fps'}))
    assert sample_points(sample) == {'app_cpu':{'x':2,'y':0}}


def test_new_report_preserves_collection_loss_and_process_scope_notes():
    collector = make_collector([frame_row(20)],enabled={'fps'})
    collector.sample(1)
    collector._etw_loss_reported = True
    session = {'id':'synthetic','device':{'platform':'windows'},'target':{},
               'samples':{'fps':[{'x':1,'y':50}]},'telemetry':{'collector':collector.diagnostics()}}
    payload = _report_payload(copy.deepcopy(session),screenshot_mode='none')
    assert any('不完整' in note for note in payload['notes'])
    assert any('交换链' in note for note in payload['notes'])
    assert payload['measurement']['frame_data_incomplete']
    assert payload['collection_quality']['integrity']['etw_loss_reported']


def test_ios_cached_readings_stay_displayable_but_are_not_new_curve_points(stream):
    collector,clock,send = stream
    collector._enabled = frozenset({'fps','app_cpu'})
    send(0,graphics={'framesPerSecond':30})
    clock[0] = 100.5
    sample = collector.sample(.5)
    assert sample.values == {'fps':30,'app_cpu':30}
    assert sample.held_keys == {'fps','app_cpu'}
    assert sample_points(sample) == {}
    assert len(collector._fps_history) == 1


def test_ios_delayed_sample_uses_per_field_observation_time(stream):
    collector,clock,_ = stream
    collector._enabled = frozenset({'fps','gpu'})
    clock[0] = 101
    collector._accept_stream_event({'type':'graphics','data':{'framesPerSecond':30}})
    clock[0] = 102
    collector._accept_stream_event({'type':'graphics','data':{'deviceUtilization':40}})
    clock[0] = 103
    sample = collector.sample(3)
    assert sample.observed_elapsed == {'fps':1,'gpu':2}
    assert sample_points(sample)['fps']['x'] == 1


def test_ios_repeated_cache_cannot_satisfy_three_new_fps_observations(stream):
    collector,clock,send = stream
    collector._enabled = frozenset({'fps'})
    for second in (1,2,3): send(second,graphics={'framesPerSecond':30})
    assert send(4,graphics={'framesPerSecond':60})['fps'] == 30
    for tick in (4.1,4.2,4.3):
        clock[0] = 100+tick
        assert collector.sample(tick).values['fps'] == 30
    assert len(collector._fps_high_candidates) == 1
    assert send(5,graphics={'framesPerSecond':60})['fps'] == 30
    assert send(6,graphics={'framesPerSecond':60})['fps'] == 60


def test_ios_pid_change_invalidates_old_graphics_until_a_new_event(stream):
    collector,_,send = stream
    collector._enabled = frozenset({'fps','app_cpu'})
    assert send(1,process={'pid':1,'cpuUsage':30},graphics={'framesPerSecond':30})['fps'] == 30
    assert send(2,process={'pid':2,'cpuUsage':30})['fps'] is None
    assert send(3,process={'pid':2,'cpuUsage':30},graphics={'framesPerSecond':60})['fps'] == 60


def test_ios_cache_channel_updates_only_emit_the_changed_metric(stream):
    collector,clock,send = stream
    collector._enabled = frozenset({'fps','gpu'})
    send(1,graphics={'framesPerSecond':30,'deviceUtilization':40})
    clock[0] = 102
    collector._accept_stream_event({'type':'graphics','data':{'deviceUtilization':50}})
    sample = collector.sample(2)
    assert sample.held_keys == {'fps'}
    assert sample_points(sample) == {'gpu':{'x':2,'y':50}}


def test_valid_held_reading_is_not_counted_as_missing_or_as_new_observation():
    from perfzy.domain.health import SamplingHealth
    health = SamplingHealth()
    health.observe(1,{'fps':{'x':1,'y':30}},requested=['fps'])
    health.observe(2,{},requested=['fps'],held={'fps'})
    status = health.snapshot(2)['metrics']['fps']
    assert status['received'] == status['held'] == 1
    assert status['missing'] == 0 and status['last_elapsed'] == 1


def test_gui_does_not_clear_held_legend_or_append_cached_point(controller):
    controller._collecting = True
    controller._paused = True
    controller._active_collection_token = controller._sample_inflight_token = 9
    batches = []
    controller.sampleBatchAppended.connect(batches.append)
    try:
        controller._accept_sample({'token':9, 'requested':['fps'],
            'sample':Sample(1,{'fps':30},held_keys=frozenset({'fps'})), 'elapsed_ms':1})
        assert not controller._samples.get('fps')
        assert not any('fps' in batch.get('__missingKeys',[]) for batch in batches)
        assert controller._metric_status['fps']['state'] == 'available'
    finally:
        controller._collecting = False


def test_ios_configuration_overtaking_snapshot_cannot_publish_old_observations(stream,monkeypatch):
    collector,clock,_ = stream
    collector._enabled = frozenset({'fps'})
    collector._accept_stream_event({'type':'graphics','data':{'framesPerSecond':30}})
    original = collector._field_stamp
    changed = []
    def interrupt(*args):
        if not changed:
            changed.append(True)
            collector._accept_stream_event({'type':'configuration','sysmon':True,'interval':1000})
        return original(*args)
    monkeypatch.setattr(collector,'_field_stamp',interrupt)
    assert collector.sample(1).values == {'fps':None}
    assert not collector._last_observation and not collector._fps_history
