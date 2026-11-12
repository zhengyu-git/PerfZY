"""Guards on preference persistence.

Metric selection used to live in one flat set that was merely filtered per
platform on the way to disk, so turning FPS off while testing a PC build also
turned it off for Android and iOS.
"""

from perfzy.application.charts import DEFAULT_CHART_ORDER
from perfzy.application.preferences import (DEFAULT_ENABLED_METRICS, WINDOWS_DEFAULT_METRICS,
                                                      Preferences)
from perfzy.domain.models import Platform


def _preferences(tmp_path, name="preferences.ini"):
    return Preferences(tmp_path / name)


def test_report_mode_is_saved_and_invalid_mode_recovers(tmp_path):
    preferences = _preferences(tmp_path)
    assert preferences.report_screenshot_mode == "compressed"
    for mode in ("none", "original", "compressed"):
        preferences.report_screenshot_mode = mode
        preferences.save()
        assert _preferences(tmp_path).report_screenshot_mode == mode
    preferences._settings.setValue("report/screenshot_mode", "broken")
    preferences._settings.sync()
    assert _preferences(tmp_path).report_screenshot_mode == "compressed"


def test_defaults_are_filtered_to_supported_metrics(tmp_path):
    preferences = _preferences(tmp_path)
    assert "fps" in preferences.enabled_for(Platform.WINDOWS)
    assert "fps" in preferences.enabled_for(Platform.ANDROID)
    assert "fps" in preferences.enabled_for(Platform.IOS)
    assert preferences.enabled_for(Platform.WINDOWS) == (
        set(WINDOWS_DEFAULT_METRICS))


def test_new_windows_counters_are_not_auto_selected(tmp_path):
    preferences = _preferences(tmp_path)
    assert "gpu_busy" not in preferences.enabled_for(Platform.WINDOWS)
    preferences.enabled_for(Platform.WINDOWS).add("app_user_cpu")
    preferences.save()

    reloaded = _preferences(tmp_path)
    assert "gpu_busy" not in reloaded.enabled_for(Platform.WINDOWS)
    assert "app_user_cpu" in reloaded.enabled_for(Platform.WINDOWS)


def test_disabling_a_shared_metric_only_affects_one_platform(tmp_path):
    preferences = _preferences(tmp_path)
    preferences.set_metric_enabled(Platform.WINDOWS, "fps", False)
    assert "fps" not in preferences.enabled_for(Platform.WINDOWS)
    assert "fps" in preferences.enabled_for(Platform.ANDROID)
    assert "fps" in preferences.enabled_for(Platform.IOS)


def test_per_platform_selection_survives_a_restart(tmp_path):
    preferences = _preferences(tmp_path)
    preferences.set_metric_enabled(Platform.WINDOWS, "fps", False)
    preferences.set_metric_enabled(Platform.ANDROID, "java_heap", True)
    preferences.save()

    reloaded = _preferences(tmp_path)
    assert "fps" not in reloaded.enabled_for(Platform.WINDOWS)
    assert "fps" in reloaded.enabled_for(Platform.ANDROID)
    assert "java_heap" in reloaded.enabled_for(Platform.ANDROID)


def test_turning_every_metric_off_is_not_mistaken_for_a_fresh_install(tmp_path):
    preferences = _preferences(tmp_path)
    preferences.enabled_for(Platform.WINDOWS).clear()
    preferences.save()

    reloaded = _preferences(tmp_path)
    assert reloaded.enabled_for(Platform.WINDOWS) == set()
    assert reloaded.enabled_for(Platform.ANDROID)


def test_unknown_metric_keys_are_dropped_on_load(tmp_path):
    preferences = _preferences(tmp_path)
    preferences.enabled_for(Platform.WINDOWS).add("metric_removed_in_a_later_version")
    preferences.save()

    reloaded = _preferences(tmp_path)
    assert "metric_removed_in_a_later_version" not in reloaded.enabled_for(Platform.WINDOWS)


def test_view_mode_overrides_are_reversible(tmp_path):
    preferences = _preferences(tmp_path)
    snapshot = preferences.snapshot_selection()
    preferences.enabled_for(Platform.IOS).update({"ios_mach_ports", "ios_gpu_tiler"})
    assert "ios_mach_ports" in preferences.enabled_for(Platform.IOS)

    preferences.restore_selection(snapshot)
    assert "ios_mach_ports" not in preferences.enabled_for(Platform.IOS)
    assert "fps" in preferences.enabled_for(Platform.IOS)


def test_chart_order_is_repaired_and_extended(tmp_path):
    preferences = _preferences(tmp_path)
    preferences.chart_order = ["network", "chart_group_that_no_longer_exists", "fps"]
    preferences.save()

    reloaded = _preferences(tmp_path)
    assert reloaded.chart_order[:2] == ["network", "fps"]
    assert set(reloaded.chart_order) == set(DEFAULT_CHART_ORDER)


def test_sampling_intervals_have_a_floor(tmp_path):
    preferences = _preferences(tmp_path)
    assert preferences.set_sample_interval_ms(10) == 500
    assert preferences.set_screenshot_interval_ms(10) == 500
    assert preferences.set_sample_interval_ms(2500) == 2500


def test_corrupt_or_extreme_sampling_settings_are_repaired_on_load(tmp_path):
    preferences = _preferences(tmp_path)
    preferences._settings.setValue("sampling/metric_ms", -1)
    preferences._settings.setValue("sampling/screenshot_ms", 99999999)
    preferences._settings.setValue("sampling/screenshot_width", 123456)
    preferences._settings.sync()

    reloaded = _preferences(tmp_path)

    assert reloaded.sample_interval_ms == 500
    assert reloaded.screenshot_interval_ms == 10000
    assert reloaded.screenshot_max_width == 1280


def test_sampling_settings_survive_a_restart(tmp_path):
    preferences = _preferences(tmp_path)
    preferences.set_sample_interval_ms(2000)
    preferences.set_screenshot_interval_ms(5000)
    preferences.screenshot_enabled = False
    preferences.screenshot_max_width = 1920
    preferences.visible_charts = {"fps", "memory"}
    preferences.save()

    reloaded = _preferences(tmp_path)
    assert reloaded.sample_interval_ms == 2000
    assert reloaded.screenshot_interval_ms == 5000
    assert reloaded.screenshot_enabled is False
    assert reloaded.screenshot_max_width == 1920
    assert reloaded.visible_charts == {"fps", "memory"}


def test_last_target_is_remembered_per_device(tmp_path):
    preferences = _preferences(tmp_path)
    assert preferences.last_target("android:SERIAL") == ""
    preferences.remember_target("android:SERIAL", "android:SERIAL:com.example.game")
    preferences.remember_target("windows:local", "windows:4242")
    assert preferences.last_target("android:SERIAL") == "android:SERIAL:com.example.game"
    assert preferences.last_target("windows:local") == "windows:4242"
