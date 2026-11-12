from test_controller import controller
import pytest


def test_long_collector_status_is_preserved_in_logs_and_summarized_for_the_sidebar(controller):
    message = "Windows 应用呈现 FPS 已降级（非屏幕实际显示 FPS）；" + "完整采集诊断。" * 80
    controller._collecting = True
    controller._status_text = message
    controller.stateChanged.emit()
    assert controller.statusSummary == "采集中 · 有提示"
    assert message in controller.runtimeLogText
    assert "[警告]" in controller.runtimeLogText
    assert len(controller.statusSummary) <= 12
    controller._collecting = False


def test_paused_and_saved_record_statuses_do_not_expand_with_record_names(controller):
    controller._collecting = True
    controller._paused = True
    assert controller.statusSummary == "采集已暂停"
    controller._collecting = False
    controller._busy = False
    controller._status_text = "已保存：" + "很长的记录名称" * 20
    controller.stateChanged.emit()
    assert controller.statusSummary == "数据已保存"
    assert controller._status_text in controller.runtimeLogText


def test_unchanged_status_does_not_spam_the_runtime_log(controller):
    original = len(controller._runtime_log)
    for _ in range(100):
        controller.stateChanged.emit()
    assert len(controller._runtime_log) == original
    controller._status_text = "已暂停"
    controller.stateChanged.emit()
    assert len(controller._runtime_log) == original + 1


def test_log_preserves_full_notification_and_limits_history(controller):
    for index in range(600):
        controller._append_runtime_log(f"事件 {index}")
    assert len(controller._runtime_log) == 500
    assert "事件 0\n" not in controller.runtimeLogText
    message = "设备连接失败：\n" + "错误详情与处理建议。" * 100
    controller._notify("连接错误", message, "error")
    assert message in controller.runtimeLogText
    assert "[错误]" in controller.runtimeLogText
    assert len(controller._runtime_log) == 500


def test_connection_check_results_are_available_in_the_log(controller):
    controller._accept_diagnostics([dict(name="Windows 帧采集权限", status="需处理",
                                        detail="尚未获得管理员权限", action="以管理员身份启动")])
    assert "尚未获得管理员权限" in controller.runtimeLogText
    assert "以管理员身份启动" in controller.runtimeLogText


@pytest.mark.parametrize("state,expected", [
    ("_saving_session", "保存"), ("_merging_sessions", "合并"),
    ("_report_working", "报告"), ("_starting_collection", "连接"), ("_busy", "刷新")])
def test_busy_reasons_explain_the_operation_that_must_finish(controller, state, expected):
    setattr(controller, state, True)
    try:
        assert expected in controller.busyReason
        assert len(controller.statusSummary) <= 12
    finally:
        setattr(controller, state, False)


def test_compact_sampling_line_keeps_validity_details_in_the_full_status(controller):
    controller._collecting = True
    controller._sample_count = 261
    controller._sample_last_ms = 104
    assert controller.statusCompactText == "采样 261 次 · 104 ms"
    assert "指标有效率" in controller.samplingHealthText
    controller._collecting = False
