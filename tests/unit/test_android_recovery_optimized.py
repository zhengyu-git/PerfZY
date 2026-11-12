"""Process recovery and CPU capability retries use mocked shell output only."""

import pytest

from test_android_collector import MEMINFO, NET_DEV, TOP, make_collector
from test_android_proc_cpu import snapshot


@pytest.mark.parametrize("metric", ["app_rss", "app_threads"])
def test_process_restart_recovers_without_cpu_selected(metric):
    responses = {
        "cat /proc/1234/status": "",
        "pidof": "5678\n",
        "cat /proc/5678/status": "VmRSS: 2048 kB\nThreads: 12\n",
    }
    collector = make_collector(responses, {metric})

    # The completed old-PID round stays missing; the next uses the new PID.
    assert collector.sample(1).values[metric] is None
    assert collector._pid == "5678"
    expected = 2.0 if metric == "app_rss" else 12.0
    assert collector.sample(2).values[metric] == expected
    assert sum(call.startswith("pidof ") for call in collector.calls) == 1
    assert not any(call.startswith("top ") for call in collector.calls)


def test_pid_change_resets_process_baselines_and_reenables_proc():
    collector = make_collector({"pidof": "5678\n"}, {"app_threads"})
    collector._cpu_snapshot = (1000, 500, 200, (1234, 100))
    collector._cpu_proc_available = False
    collector._cpu_proc_retry_elapsed = 100
    collector._cpu_proc_retry_delay = 60
    collector._last_network = (1_000_000, 2_000_000)
    collector._last_network_elapsed = 4
    collector._baseline_pss_mb = 900

    collector.sample(6)

    assert collector._pid == "5678"
    assert collector._cpu_snapshot is None
    assert collector._cpu_proc_available is None
    assert collector._cpu_proc_retry_elapsed == 0
    assert collector._cpu_proc_retry_delay == 5
    assert collector._last_network is None
    assert collector._last_network_elapsed == 6
    assert collector._baseline_pss_mb is None


def test_absent_app_preserves_pid_and_throttles_recovery_checks():
    collector = make_collector({}, {"app_threads"})
    for elapsed in (1, 2, 3, 4, 5, 6):
        assert collector.sample(elapsed).values["app_threads"] is None
    assert collector._pid == "1234"
    assert sum(call.startswith("pidof ") for call in collector.calls) == 2


def test_transient_cpu_failure_recovers_after_retry_and_preserves_baseline():
    responses = {"head -n 1 /proc/stat": snapshot(100, 900, 10, 10), "top": TOP}
    collector = make_collector(responses, {"app_cpu", "total_cpu"})
    collector.sample(1)
    baseline = collector._cpu_snapshot
    responses["head -n 1 /proc/stat"] = ""

    collector.sample(2)
    assert collector._cpu_snapshot == baseline
    assert collector._cpu_proc_available is False
    responses["head -n 1 /proc/stat"] = snapshot(140, 960, 20, 15)
    for elapsed in (3, 4, 5, 6):
        collector.sample(elapsed)
    assert sum("head -n 1 /proc/stat" in call for call in collector.calls) == 2
    top_calls = sum(call.startswith("top ") for call in collector.calls)

    assert collector.sample(7).values == {"app_cpu": 15, "total_cpu": 40}
    assert collector._cpu_proc_available is True
    assert sum(call.startswith("top ") for call in collector.calls) == top_calls
    collector.sample(8)
    assert sum(call.startswith("top ") for call in collector.calls) == top_calls


def test_restricted_proc_retries_with_bounded_backoff_not_every_tick():
    collector = make_collector({"top": TOP}, {"app_cpu"})
    probe_times = []
    for elapsed in range(1, 140):
        before = sum("head -n 1 /proc/stat" in call for call in collector.calls)
        collector.sample(elapsed)
        after = sum("head -n 1 /proc/stat" in call for call in collector.calls)
        if after > before:
            probe_times.append(elapsed)

    assert probe_times == [1, 6, 16, 36, 76, 136]
    assert collector._cpu_proc_retry_delay == 60


def test_memory_growth_baseline_restarts_with_new_process():
    responses = {"dumpsys meminfo": MEMINFO, "pidof": "5678\n"}
    collector = make_collector(responses, {"memory_growth"})
    assert collector.sample(1).values["memory_growth"] == 0
    assert collector._baseline_pss_mb is None
    responses["dumpsys meminfo"] = MEMINFO.replace("TOTAL   614400", "TOTAL   716800")
    assert collector.sample(2).values["memory_growth"] == 0


def test_replacement_cpu_process_starts_a_new_delta_window():
    responses = {"head -n 1 /proc/stat": snapshot(100, 900, 10, 10), "pidof": "5678\n"}
    collector = make_collector(responses, {"app_cpu", "total_cpu"})
    collector.sample(1)
    responses["head -n 1 /proc/stat"] = snapshot(140, 960, 200, 150, started=200).replace("1234 (", "5678 (")
    assert collector.sample(2).values == {"app_cpu": None, "total_cpu": None}
    responses["head -n 1 /proc/stat"] = snapshot(180, 1020, 210, 155, started=200).replace("1234 (", "5678 (")
    assert collector.sample(3).values == {"app_cpu": 15, "total_cpu": 40}


def test_network_replacement_does_not_compare_different_process_baselines():
    responses = {"cat /proc/1234/net/dev": NET_DEV, "pidof": "5678\n"}
    collector = make_collector(responses, {"network_recv", "network_send"})
    collector._last_network = (1, 1)
    collector._last_network_elapsed = 0
    collector.sample(1)
    assert collector._last_network is None
    responses["cat /proc/5678/net/dev"] = NET_DEV
    assert collector.sample(2).values == {}
    responses["cat /proc/5678/net/dev"] = NET_DEV.replace(
        "1024000 100 0 0 0 0 0 0 2048000", "1026048 100 0 0 0 0 0 0 2052096")
    assert collector.sample(3).values == {"network_recv": 2, "network_send": 4}

