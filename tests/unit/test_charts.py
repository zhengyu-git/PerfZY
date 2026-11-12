"""Guards on chart composition.

The workspace and the saved-session viewer used to carry separate copies of the
chart definitions, which drifted apart and left iOS-only metrics belonging to no
chart. These tests keep the single definition honest.
"""

import pytest

from perfzy.application.charts import (CHART_GROUPS, DEFAULT_CHART_ORDER,
                                                DEFAULT_VISIBLE_CHARTS, build_chart_catalog,
                                                build_metric_catalog, core_index,
                                                group_key_for_metric, groups_with_data,
                                                metric_available, supported_chart_keys)
from perfzy.domain.metrics import METRIC_BY_KEY, METRICS
from perfzy.domain.models import Platform


MANY_CORES = 8


def test_every_charted_metric_is_defined():
    unknown = [key for group in CHART_GROUPS for key in group.metrics
               if key not in METRIC_BY_KEY]
    assert unknown == []


def test_every_metric_belongs_to_exactly_one_chart():
    for metric in METRICS:
        owners = [group.key for group in CHART_GROUPS
                  if metric.key in group.metrics or group_key_for_metric(metric.key) == group.key]
        assert len(set(owners)) == 1, f"{metric.key} -> {owners}"


def test_chart_order_covers_all_groups():
    assert set(DEFAULT_CHART_ORDER) == {group.key for group in CHART_GROUPS}
    assert len(DEFAULT_CHART_ORDER) == len(CHART_GROUPS)
    assert DEFAULT_VISIBLE_CHARTS <= set(DEFAULT_CHART_ORDER)


@pytest.mark.parametrize("platform", list(Platform))
def test_every_collectable_metric_is_reachable_from_a_chart(platform):
    """A metric a platform can collect must land on a chart that platform shows."""
    supported = set(supported_chart_keys(platform, DEFAULT_CHART_ORDER, MANY_CORES))
    for metric in METRICS:
        if platform not in metric.platforms or not metric_available(metric.key, MANY_CORES):
            continue
        assert group_key_for_metric(metric.key) in supported, metric.key


def test_ios_only_memory_metric_reveals_the_memory_chart():
    assert groups_with_data({"ios_physical_footprint": [{"x": 1, "y": 512}]}) == {"memory"}


def test_ios_only_gpu_and_process_metrics_reveal_their_charts():
    samples = {"ios_gpu_renderer": [{"x": 1, "y": 40}],
               "ios_mach_ports": [{"x": 1, "y": 900}]}
    assert groups_with_data(samples) == {"gpu", "process"}


def test_empty_series_reveal_nothing():
    assert groups_with_data({"fps": [], "memory": []}) == set()


def test_core_index_only_matches_per_core_metrics():
    assert core_index("cpu_core_0_usage") == 0
    assert core_index("cpu_core_11_clock") == 11
    assert core_index("cpu_frequency") is None
    assert core_index("fps") is None


def test_per_core_metrics_are_hidden_beyond_the_device_core_count():
    assert metric_available("cpu_core_3_usage", 4)
    assert not metric_available("cpu_core_4_usage", 4)
    assert metric_available("fps", 0)


def test_windows_catalog_excludes_mobile_only_charts():
    keys = {chart["key"] for chart in
            build_chart_catalog(Platform.WINDOWS, DEFAULT_CHART_ORDER, {"fps"}, {"fps"}, 0)}
    assert "cpu_core_usage" not in keys
    assert "gpu_clock" not in keys
    assert {"fps", "cpu", "memory", "network", "io", "process"} <= keys


def test_windows_catalog_expands_logical_cpu_series_when_core_count_is_known():
    catalog = build_chart_catalog(Platform.WINDOWS, DEFAULT_CHART_ORDER,
                                  {"cpu_core_usage"}, set(), 8)
    cores = next(chart for chart in catalog if chart["key"] == "cpu_core_usage")
    assert cores["metricKeys"] == [f"cpu_core_{index}_usage" for index in range(8)]


def test_android_catalog_expands_one_series_per_core():
    catalog = build_chart_catalog(Platform.ANDROID, DEFAULT_CHART_ORDER, {"cpu_core_usage"},
                                 set(), 6)
    cores = next(chart for chart in catalog if chart["key"] == "cpu_core_usage")
    assert cores["metricKeys"] == [f"cpu_core_{index}_usage" for index in range(6)]
    assert len(cores["seriesNames"]) == 6
    assert len(cores["seriesColors"]) == 6


def test_catalog_move_flags_bound_the_visible_order():
    catalog = build_chart_catalog(Platform.ANDROID, DEFAULT_CHART_ORDER, set(), set(), 4)
    assert not catalog[0]["canMoveUp"]
    assert not catalog[-1]["canMoveDown"]
    assert all(chart["canMoveDown"] for chart in catalog[:-1])


def test_catalog_reports_selection_state():
    catalog = build_chart_catalog(Platform.WINDOWS, DEFAULT_CHART_ORDER, {"cpu"},
                                 {"app_cpu"}, 0)
    cpu = next(chart for chart in catalog if chart["key"] == "cpu")
    assert cpu["visible"]
    states = dict(zip(cpu["metricKeys"], cpu["seriesEnabled"]))
    assert states["app_cpu"] is True
    assert set(states) == {"app_cpu"}


def test_platform_specific_series_names():
    windows = build_chart_catalog(Platform.WINDOWS, DEFAULT_CHART_ORDER, {"memory"}, set(), 0)
    android = build_chart_catalog(Platform.ANDROID, DEFAULT_CHART_ORDER, {"memory"}, set(), 4)
    windows_memory = next(chart for chart in windows if chart["key"] == "memory")
    android_memory = next(chart for chart in android if chart["key"] == "memory")
    assert windows_memory["seriesNames"][0] == "Working Set"
    assert android_memory["seriesNames"][0] == "PSS Memory"


def test_metric_catalog_is_limited_to_the_platform():
    catalog = build_metric_catalog(Platform.WINDOWS, {"fps"}, 0)
    keys = {item["key"] for item in catalog}
    assert "fps" in keys
    assert "java_heap" not in keys
    assert "ios_mach_ports" not in keys
    assert next(item for item in catalog if item["key"] == "fps")["enabled"]


def test_metric_catalog_entries_carry_a_group_title():
    for item in build_metric_catalog(Platform.ANDROID, set(), 4):
        assert item["groupTitle"]
        assert item["title"]
