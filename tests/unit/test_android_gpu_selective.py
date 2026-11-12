import pytest
from perfzy.collectors.android import AndroidCollector
from test_android_collector import make_collector, GPU_SYSFS

@pytest.mark.parametrize("metrics,expected", [
    ({"gpu_frequency"}, {"cur_freq"}),
    ({"gpu_max_frequency"}, {"max_freq"}),
    ({"gpu"}, {"gpubusy"}),
    ({"gpu_busy_time"}, {"gpubusy"}),
    ({"gpu_clock_load"}, {"cur_freq", "max_freq"}),
    ({"gpu", "gpu_frequency"}, {"gpubusy", "cur_freq"}),
])
def test_only_required_gpu_nodes_are_read(metrics, expected):
    command = AndroidCollector._gpu_query(metrics)
    assert {node for node in {"cur_freq", "max_freq", "gpubusy"} if node in command} == expected


def test_live_selection_rebuilds_query_and_keeps_values():
    c = make_collector({"kgsl": GPU_SYSFS}, {"gpu_frequency"})
    assert c.sample(1).values == {"gpu_frequency": 800}
    assert "gpubusy" not in c.calls[-1] and "max_freq" not in c.calls[-1]
    c.set_enabled_metrics(frozenset({"gpu_clock_load"}))
    assert c.sample(2).values == {"gpu_clock_load": 80}
    assert "cur_freq" in c.calls[-1] and "max_freq" in c.calls[-1]
    assert "gpubusy" not in c.calls[-1]


def test_missing_frequency_remains_missing():
    c = make_collector({}, {"gpu_frequency"})
    assert c.sample(1).values["gpu_frequency"] is None
