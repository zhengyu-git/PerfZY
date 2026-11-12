import time

from PySide6.QtCore import QCoreApplication
import pytest

from test_controller import controller
from test_session_merging import record
from perfzy.storage.sessions import load_session


def wait_for_merge(instance):
    app = QCoreApplication.instance() or QCoreApplication([])
    deadline = time.monotonic() + 10
    while instance.sessionsMerging and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert not instance.sessionsMerging


def test_background_merge_refreshes_history_and_opens_with_cumulative_duration(controller, tmp_path):
    record(tmp_path, "a", duration=5)
    record(tmp_path, "b", duration=7)
    completed = []
    controller.sessionsMerged.connect(completed.append)
    assert controller.mergeSessions(["b", "a"], "我的合并记录")
    wait_for_merge(controller)
    assert len(completed) == 1
    assert len(controller.sessionRecords) == 3
    merged = load_session(tmp_path, completed[0])
    assert [part["id"] for part in merged["merged_from"]] == ["b", "a"]
    controller.openSession(completed[0])
    assert controller.viewingSession
    assert controller.duration == controller.chartDuration == 12
    assert controller.canGenerateReport


@pytest.mark.parametrize("state", ["_collecting", "_starting_collection", "_saving_session", "_report_working", "_merging_sessions"])
def test_merge_is_not_started_while_an_incompatible_job_is_running(controller, state):
    setattr(controller, state, True)
    assert not controller.mergeSessions(["a", "b"], "合并")
    setattr(controller, state, False)


def test_merge_locks_source_deletion_capture_and_report_generation(controller, tmp_path):
    path = record(tmp_path, "a")
    controller._merging_sessions = True
    try:
        assert controller.busy
        assert not controller.deleteSession("a")
        controller.generateReportForSession("a")
        assert not controller.reportGenerating
        controller.toggleCollection()
        assert not controller.collecting and not controller.collectionStarting
        assert path.is_file()
    finally:
        controller._merging_sessions = False


def test_background_failure_releases_busy_state_and_preserves_history(controller, tmp_path):
    record(tmp_path, "a")
    messages = []
    controller.notificationRequested.connect(lambda *args: messages.append(args))
    assert controller.mergeSessions(["a", "missing"], "合并")
    wait_for_merge(controller)
    assert messages[-1][0] == "记录合并失败"
    assert not controller.busy
    controller.refreshSessionRecords()
    assert len(controller.sessionRecords) == 1
