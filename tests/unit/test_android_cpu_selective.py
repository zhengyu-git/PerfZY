from perfzy.collectors.android import AndroidCollector
from test_android_collector import make_collector


def test_online_count_does_not_read_frequencies():
    command = AndroidCollector._cpu_hardware_query({"cpu_online_cores"})
    assert "/online" in command
    assert "cpufreq" not in command


def test_selected_cores_only_and_no_maximum():
    command = AndroidCollector._cpu_hardware_query({"cpu_core_2_clock", "cpu_core_7_clock"})
    assert "cpu2/cpufreq" in command and "cpu7/cpufreq" in command
    assert "cpu*" not in command and "cpuinfo_max_freq" not in command and "/online" not in command


def test_average_reads_all_current_but_not_maximum():
    command = AndroidCollector._cpu_hardware_query({"cpu_frequency"})
    assert "cpu*/cpufreq/scaling_cur_freq" in command
    assert "cpuinfo_max_freq" not in command and "/online" not in command


def test_throttling_retains_both_frequency_dependencies():
    command = AndroidCollector._cpu_hardware_query({"cpu_throttling", "cpu_core_2_clock"})
    assert "cpu*/cpufreq/scaling_cur_freq" in command
    assert "cpuinfo_max_freq" in command
    assert "cpu2/cpufreq" not in command


def test_selected_core_values_and_dynamic_switch():
    responses = {"cpufreq": "/sys/devices/system/cpu/cpu2/cpufreq/scaling_cur_freq:1200000:\n"}
    c = make_collector(responses, {"cpu_core_2_clock"})
    assert c.sample(1).values == {"cpu_core_2_clock": 1200}
    c.set_enabled_metrics(frozenset({"cpu_online_cores"}))
    responses["ONLINE="] = "ONLINE=0-3,6-7\n"
    assert c.sample(2).values == {"cpu_online_cores": 6}
    assert "cpufreq" not in c.calls[-1]


def test_missing_core_not_zero_and_unselected_cores_not_emitted():
    c = make_collector({"cpufreq": "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq:1500000:\n"}, {"cpu_core_2_clock"})
    assert c.sample(1).values == {"cpu_core_2_clock": None}
