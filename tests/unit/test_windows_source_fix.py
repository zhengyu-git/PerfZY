import io
import time
from unittest.mock import Mock

from perfzy.collectors.windows import WindowsCollector, FRAME_EVENT_GRACE_SECONDS
from test_windows_collector import make_collector, frame_row, FakePresentMon, FakeProcess


def test_csv_reader_accepts_bom_preamble_and_repeated_headers():
    collector = make_collector(enabled={'fps'})
    collector._read_presentmon(io.StringIO(
        'Starting capture...\n\ufeffApplication, ProcessID, FrameTime\n'
        'game.exe,4242,20\nApplication,ProcessID,FrameTime\n'
        'game.exe,4242,20\n'))
    assert collector.sample(1).values['fps'] == 50
    assert collector.diagnostics()['csv_rows'] == 2


def test_malformed_csv_rows_are_reported_without_losing_good_rows():
    collector = make_collector(enabled={'fps'})
    collector._read_presentmon(io.StringIO(
        'Application,ProcessID,FrameTime\ngame.exe,4242\ngame.exe,4242,20\n'))
    assert collector.sample(1).values['fps'] == 50
    assert collector.diagnostics()['csv_parse_errors'] == 1


def test_previous_source_reader_cannot_write_into_reconnected_stream():
    collector = make_collector(enabled={'fps'})
    collector._presentmon_generation = 2
    collector._read_presentmon(io.StringIO('Application,ProcessID,FrameTime\ngame.exe,4242,20\n'), 1)
    assert collector._present_rows.empty()
    collector._read_presentmon_errors(io.StringIO('old error\n'), 1)
    assert not collector._presentmon_stderr


def test_instances_have_distinct_etw_session_names():
    assert WindowsCollector()._presentmon_session != WindowsCollector()._presentmon_session


def test_starting_source_never_stops_other_perfzy_sessions(monkeypatch):
    from test_dxgi_etw import FakeDxgi
    monkeypatch.setattr('perfzy.collectors.windows_frame_source.DxgiPresentSource',FakeDxgi)
    collector = make_collector(enabled={'fps'})
    collector._presentmon = None
    broad_stop = Mock(side_effect=AssertionError('must not stop another instance'))
    monkeypatch.setattr(collector, '_stop_stale_etw_sessions', broad_stop)
    monkeypatch.setattr(collector, '_presentmon_path', lambda: 'PresentMon.exe')
    commands = []
    monkeypatch.setattr('perfzy.collectors.windows.subprocess.Popen',
                        lambda command, **kwargs: commands.append(command) or FakePresentMon())
    collector._ensure_presentmon()
    assert collector._presentmon_session in commands[0]
    assert not broad_stop.called


def test_silent_owned_source_retries_twice_and_keeps_missing_not_zero(monkeypatch):
    collector = make_collector(enabled={'fps'})
    monkeypatch.setattr(collector, '_begin_frame_candidate', lambda profile: False)
    collector._presentmon_owned = True
    restarts = []
    def start(force=False):
        restarts.append(force)
        collector._presentmon = FakePresentMon()
        collector._presentmon_started_at = time.monotonic()
    monkeypatch.setattr(collector, '_ensure_presentmon', start)
    for tick in range(3):
        collector._presentmon_started_at = time.monotonic() - FRAME_EVENT_GRACE_SECONDS - 1
        assert 'fps' not in collector.sample(tick + 1).values
    assert restarts == [True, True]
    assert '仍未收到有效帧' in collector.status
    assert collector.diagnostics()['automatic_restarts'] == 2


def test_source_recovers_after_retry_without_stale_error(monkeypatch):
    collector = make_collector(enabled={'fps'})
    collector._presentmon_error = 'Windows 帧源无有效数据，正在重连（1/2）'
    collector._present_rows.put(frame_row(20))
    assert collector.sample(1).values['fps'] == 50
    assert not collector.status


def test_status_never_reads_stderr_on_gui_thread():
    collector = make_collector(enabled={'fps'})
    collector._presentmon = FakePresentMon(returncode=1)
    collector._presentmon.stderr = Mock()
    collector._presentmon.stderr.read.side_effect = AssertionError('blocking stderr read')
    assert '已停止' in collector.status


def test_partial_etw_loss_is_not_reported_as_entire_source_unavailable():
    collector = make_collector([frame_row(20)], enabled={'fps'})
    collector.sample(1)
    collector._presentmon_stderr.append('warning: 100 events were lost')
    assert '已接收' in collector.status
    assert '不可用' not in collector.status


def test_windows_report_declares_presentation_fps_and_unknown_display_drops():
    from perfzy.domain.provenance import metric_provenance
    result=metric_provenance('windows',{'fps','dropped_frames','dropped_frame_rate'})
    assert 'FrameTime' in result['fps']['method']
    assert '不是屏幕实际显示' in result['fps']['note']
    assert '缺失不是零' in result['dropped_frame_rate']['note']
