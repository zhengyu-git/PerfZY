from perfzy.domain.capture_configuration import capture_request_summary, reusable_capture_configuration
from perfzy.domain.models import Platform
from perfzy.reporting import generate_local_report
from perfzy.storage.editing import save_scene_annotations
from perfzy.storage.record_cuts import remove_time_range
from perfzy.storage.record_overwrite import undo_last_overwrite
from perfzy.storage.reports import load_html_reports, rename_html_report
from perfzy.storage.sessions import load_session_records, rename_session, save_session
from test_controller import controller
from test_saved_record_editing import recording


def test_request_evidence_distinguishes_unused_missing_and_stopped():
    session = {
        "enabled_metrics": ["fps", "hidden", "memory"],
        "capture_settings": {"requested_metrics": ["fps", "gpu", "memory"], "sample_interval_ms": 1500},
        "capture_settings_changes": [{"elapsed": 4, "settings": {"requested_metrics": ["fps", "memory"]}}],
        "quality": {"samples": 6, "metrics": {"fps": {"requested": 6}, "gpu": {"requested": 3},
                                            "memory": {"requested": 6}}},
    }
    assert capture_request_summary(session, {"fps", "memory"}) == {
        "missing": ["gpu"], "not_requested": ["hidden"], "stopped": ["gpu"], "unverified": []}
    assert reusable_capture_configuration(session)[1] == {"fps", "gpu", "memory"}
    session.pop("capture_settings")
    assert reusable_capture_configuration(session)[1] == {"fps", "gpu", "memory"}
    session.pop("quality")
    assert capture_request_summary(session, {"fps"})["unverified"] == ["hidden", "memory"]
    assert capture_request_summary(session, {"fps"})["missing"] == []


def test_reuse_applies_recorded_requests_and_settings_without_starting(controller, tmp_path, monkeypatch):
    save_session(tmp_path, {"platform": "windows"}, {"name": "测试游戏"},
                 {"fps": [{"x": 1, "y": 60}]}, [], {"fps", "total_cpu"},
                 session_id="reusable", duration=4, capture_settings={
                     "requested_metrics": ["fps", "memory"], "sample_interval_ms": 1500,
                     "screenshots": False, "screenshot_interval_ms": 2500,
                     "screenshot_max_width": 854, "windows_resource_scope": "tree",
                 })
    monkeypatch.setattr(controller._executor, "submit", lambda fn: fn())
    controller._busy = False
    signals = []
    controller.captureConfigurationApplied.connect(lambda: signals.append(True))

    controller.reuseCaptureConfiguration("reusable")
    prefs = controller._preferences
    assert prefs.enabled_for(Platform.WINDOWS) == {"fps", "memory"}
    assert prefs.visible_for(Platform.WINDOWS) == {"fps", "memory"}
    assert prefs.sample_interval_ms == 1500 and prefs.screenshot_interval_ms == 2500
    assert not prefs.screenshot_enabled and prefs.screenshot_max_width == 854
    assert prefs.windows_resource_scope == "tree"
    assert signals == [True] and not controller.collecting
    assert controller._timer.interval() == 1500


def test_report_revision_tracks_source_edits_and_restoring(tmp_path, recording):
    original = generate_local_report(recording, tmp_path / "exports", "original")
    source = lambda: load_session_records(tmp_path)

    def stale_flags():
        return {row["id"]: row["sourceChanged"] for row in load_html_reports(tmp_path, sessions=source())[0]}

    assert stale_flags() == {original.name: False}
    rename_session(tmp_path, "source", "仅改名称")
    assert stale_flags() == {original.name: False}
    remove_time_range(tmp_path, "source", 3, 6, create_copy=False)
    assert stale_flags() == {original.name: True}
    renamed = rename_html_report(tmp_path, original.name, "改报告名")
    assert stale_flags() == {renamed.name: True}
    edited = generate_local_report(recording, tmp_path / "exports", "edited")
    assert stale_flags() == {renamed.name: True, edited.name: False}
    undo_last_overwrite(tmp_path, "source")
    assert stale_flags() == {renamed.name: False, edited.name: True}
    save_scene_annotations(tmp_path, "source", [
        {"id": 20, "name": "修改场景", "start": 1, "end": 2, "color": "#4269d4"}], 10)
    assert stale_flags() == {renamed.name: True, edited.name: True}

