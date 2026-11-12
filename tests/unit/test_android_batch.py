from concurrent.futures import Future
from unittest.mock import Mock
from perfzy.collectors.android import AndroidCollector
from test_android_collector import make_collector


def test_batch_preserves_payload_and_single_shell():
    c = AndroidCollector()
    c._shell = Mock(return_value="\n__PERFZY_status__\nVmRSS: 1024 kB\n\n__PERFZY_current__\n-10000\n")
    result = c._light_batch({"status": ("cat", "/proc/1/status"), "current": ("cat", "/sys/current")})
    assert "VmRSS: 1024 kB" in result["status"]
    assert result["current"].strip() == "-10000"
    assert c._shell.call_count == 1


def test_missing_batch_section_is_not_replaced_by_another_metric():
    c = AndroidCollector();c._shell = Mock(return_value="\n__PERFZY_status__\nhello\n")
    assert "current" not in c._light_batch({"current": ("cat", "/sys/current")})


def test_battery_voltage_does_not_match_charger_voltage():
    c = make_collector({"dumpsys battery": "Max charging voltage: 5000000\n  voltage: 4058\n"}, {"battery_voltage"})
    assert c.sample(1).values["battery_voltage"] == 4.058


def test_missing_network_does_not_reset_counter_baseline():
    c = make_collector({}, {"network_send", "network_recv"})
    c._last_network = (100, 200)
    assert c.sample(1).values == {"network_recv": None, "network_send": None}
    assert c._last_network == (100, 200)


def test_background_result_is_emitted_only_once():
    c = AndroidCollector(); c._sample_executor = Mock()
    complete = Future(); complete.set_result("cpu:40000")
    c._background_futures["thermal"] = complete
    import time
    c._background_started["thermal"] = time.monotonic()
    assert c._background_output("thermal", "true", refresh_seconds=5, timeout=8) == "cpu:40000"
    assert c._background_output("thermal", "true", refresh_seconds=5, timeout=8) == ""
