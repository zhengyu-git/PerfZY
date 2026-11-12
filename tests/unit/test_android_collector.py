"""Guards on the Android dumpsys/procfs parsers.

Every number the Android collector reports comes out of a regex over shell text,
so these tests pin the parsing against realistic device output. They also cover
frame statistics being switched on after collection has already started.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from perfzy.collectors.android import FRAME_METRICS, AndroidCollector


PACKAGE = "com.example.game"
PID = "1234"

MEMINFO = """Applications Memory Usage (in Kilobytes):
** MEMINFO in pid 1234 [com.example.game] **
                   Pss  Private  Private     Swap      Rss
                 Total    Dirty    Clean    Dirty    Total
                ------   ------   ------   ------   ------
  Native Heap   204800   204800        0        0   210000
  Dalvik Heap    51200    51200        0        0    52000
     Graphics   307200   307200        0        0   307200
        TOTAL   614400   563200        0        0   620000
 TOTAL SWAP PSS:    12288
"""

TOP = """Tasks: 412 total,   1 running
Mem:  7654321K total,  5432109K used
400%cpu 120%user   0%nice  60%sys 200%idle   0%iow   0%irq   0%sirq
  PID USER         PR  NI VIRT  RES  SHR S[%CPU] %MEM     TIME+ ARGS
 1234 u0_a321      10 -10  20G 1.2G 500M S 87.5  15.3   5:12.34 com.example.game
 5678 u0_a999      20   0  10G 300M 100M S  3.1   2.0   0:11.00 com.other.app
"""

STATUS = """Name:\tcom.example.game
Threads:\t143
VmRSS:\t  1258291 kB
"""

BATTERY = """Current Battery Service state:
  AC powered: false
  level: 87
  temperature: 352
  voltage: 4321
"""

PROC_STAT = """cpu  100 0 100 800 0 0 0
cpu0 100 0 100 800 0 0 0
cpu1 200 0 200 600 0 0 0
"""

NET_DEV = """Inter-|   Receive                        |  Transmit
 face |bytes packets errs drop fifo frame compressed multicast|bytes packets
    lo:  100 1 0 0 0 0 0 0  100 1 0 0 0 0 0 0
  wlan0: 1024000 100 0 0 0 0 0 0 2048000 200 0 0 0 0 0 0
"""

GPU_SYSFS = """busy= 5000 10000
cur=800000000
max=1000000000
"""

DISPLAY = """Display Devices: size=1
  mActiveSfDisplayMode=DisplayMode{id=2, width=1440, height=3200, fps=120.000}
  renderFrameRate 120.0
"""

SURFACE_TEMPLATE = """layerName = SurfaceView[com.example.game/com.example.MainActivity]#0
totalFrames = {frames}
averageFPS = {average}
present2present histogram is as below:
16ms={smooth} 33ms=12 50ms=3
"""

FRAMESTATS_WITH_SENTINEL = """---PROFILEDATA---
Flags,IntendedVsync,Vsync,OldestInputEvent,NewestInputEvent,HandleInputStart,AnimationStart,PerformTraversalsStart,DrawStart,SyncQueued,SyncStart,IssueDrawCommandsStart,SwapBuffers,FrameCompleted,DequeueBufferDuration,QueueBufferDuration,
0,0,0,0,0,0,0,0,0,0,0,0,0,298652166801421000,0,0,
0,1000000000,1000000000,0,0,0,0,0,0,0,0,0,0,1032000000,0,0,
---PROFILEDATA---
"""


def make_collector(responses, enabled):
    collector = AndroidCollector()
    collector._adb = "adb"
    collector._serial = "SERIAL"
    collector._package = PACKAGE
    collector._pid = PID
    collector._enabled = frozenset(enabled)
    collector.calls = []

    def shell(*arguments, timeout=4.0):
        command = " ".join(arguments)
        collector.calls.append(command)
        for needle, text in responses.items():
            if needle in command:
                return text
        return ""

    collector._shell = shell
    return collector


def test_pss_and_heap_breakdown():
    collector = make_collector({"dumpsys meminfo": MEMINFO},
                               {"memory", "java_heap", "native_heap", "graphics_memory",
                                "swap_memory", "app_uss", "memory_growth"})
    values = collector.sample(1.0).values
    assert values["memory"] == pytest.approx(600.0)
    assert values["native_heap"] == pytest.approx(200.0)
    assert values["java_heap"] == pytest.approx(50.0)
    assert values["graphics_memory"] == pytest.approx(300.0)
    assert values["swap_memory"] == pytest.approx(12.0)
    assert values["app_uss"] == pytest.approx(550.0)


def test_memory_growth_is_relative_to_the_first_sample():
    responses = {"dumpsys meminfo": MEMINFO}
    collector = make_collector(responses, {"memory_growth"})
    assert collector.sample(1.0).values["memory_growth"] == pytest.approx(0.0)

    responses["dumpsys meminfo"] = MEMINFO.replace("TOTAL   614400", "TOTAL   716800")
    assert collector.sample(2.0).values["memory_growth"] == pytest.approx(100.0)
    responses["dumpsys meminfo"] = MEMINFO.replace("TOTAL   614400", "TOTAL   512000")
    assert collector.sample(3.0).values["memory_growth"] == pytest.approx(-100.0)


def test_missing_meminfo_yields_no_data_instead_of_zero():
    collector = make_collector({}, {"memory", "java_heap"})
    values = collector.sample(1.0).values
    assert values["memory"] is None
    assert values["java_heap"] is None


def test_app_and_total_cpu_from_top():
    collector = make_collector({"top": TOP}, {"app_cpu", "total_cpu"})
    values = collector.sample(1.0).values
    # top reports process CPU as a multi-core sum while the chart uses total
    # device capacity (the same convention as Windows).
    assert values["app_cpu"] == pytest.approx(21.875)
    assert values["total_cpu"] == pytest.approx(50.0)


def test_app_cpu_is_normalized_to_total_device_capacity():
    busy = TOP.replace("S 87.5", "S 350.0")
    collector = make_collector({"top": busy}, {"app_cpu"})
    assert collector.sample(1.0).values["app_cpu"] == pytest.approx(87.5)


def test_total_cpu_is_none_without_an_idle_reading():
    collector = make_collector({"top": TOP.replace("200%idle", "")}, {"total_cpu"})
    assert collector.sample(1.0).values["total_cpu"] is None


def test_rss_and_thread_count():
    collector = make_collector({f"cat /proc/{PID}/status": STATUS}, {"app_rss", "app_threads"})
    values = collector.sample(1.0).values
    assert values["app_rss"] == pytest.approx(1228.8, abs=0.1)
    assert values["app_threads"] == 143.0


def test_battery_readings_are_converted_to_display_units():
    collector = make_collector(
        {"dumpsys battery": BATTERY, "current_now": "-523000\n"},
        {"temperature", "battery_level", "battery_voltage", "battery_current", "power"})
    values = collector.sample(1.0).values
    assert values["temperature"] == pytest.approx(35.2)
    assert values["battery_level"] == 87.0
    assert values["battery_voltage"] == pytest.approx(4.321)
    assert values["battery_current"] == pytest.approx(523.0)
    assert values["power"] == pytest.approx(0.523 * 4.321)


def test_per_core_usage_needs_two_samples():
    responses = {"cat /proc/stat": PROC_STAT}
    collector = make_collector(responses, {"cpu_core_0_usage", "cpu_core_1_usage"})
    assert collector.sample(1.0).values == {}

    responses["cat /proc/stat"] = PROC_STAT.replace(
        "cpu0 100 0 100 800", "cpu0 200 0 200 800")
    values = collector.sample(2.0).values
    # cpu0 gained 200 busy ticks and no idle ticks.
    assert values["cpu_core_0_usage"] == pytest.approx(100.0)
    assert values["cpu_core_1_usage"] is None


def test_network_throughput_is_a_per_second_delta():
    responses = {f"cat /proc/{PID}/net/dev": NET_DEV}
    collector = make_collector(responses, {"network_send", "network_recv"})
    assert collector.sample(1.0).values == {}

    responses[f"cat /proc/{PID}/net/dev"] = NET_DEV.replace(
        "1024000 100 0 0 0 0 0 0 2048000", "1026048 100 0 0 0 0 0 0 2052096")
    values = collector.sample(2.0).values
    assert values["network_recv"] == pytest.approx(2.0)
    assert values["network_send"] == pytest.approx(4.0)


def test_loopback_traffic_is_excluded():
    responses = {f"cat /proc/{PID}/net/dev": NET_DEV}
    collector = make_collector(responses, {"network_recv"})
    collector.sample(1.0)
    assert collector._last_network == (1024000, 2048000)


def test_gpu_utilisation_and_clocks():
    collector = make_collector({"kgsl": GPU_SYSFS},
                               {"gpu", "gpu_frequency", "gpu_max_frequency",
                                "gpu_clock_load", "gpu_busy_time"})
    values = collector.sample(1.0).values
    assert values["gpu"] == pytest.approx(50.0)
    assert values["gpu_frequency"] == pytest.approx(800.0)
    assert values["gpu_max_frequency"] == pytest.approx(1000.0)
    assert values["gpu_clock_load"] == pytest.approx(80.0)
    assert values["gpu_busy_time"] == pytest.approx(500.0)


def test_thermal_zones_are_normalised_and_filtered():
    thermal = "cpu-0-usr:48500\ncpu-1-usr:51200\ngpuss-0-usr:44000\nbattery:9999999\n"
    collector = make_collector({"thermal_zone": thermal},
                               {"cpu_temperature", "gpu_temperature"})
    values = collector.sample(1.0).values
    assert values["cpu_temperature"] == pytest.approx(51.2)
    assert values["gpu_temperature"] == pytest.approx(44.0)


def test_slow_thermal_refresh_does_not_block_regular_samples():
    collector = make_collector({}, {"cpu_temperature"})
    pending = []

    class PendingFuture:
        def done(self):
            return False

    class NonBlockingExecutor:
        def submit(self, *args, **kwargs):
            pending.append((args, kwargs))
            return PendingFuture()

    collector._sample_executor = NonBlockingExecutor()
    values = collector.sample(1.0).values

    assert values["cpu_temperature"] is None
    assert len(pending) == 1


def test_cpu_clocks_online_state_and_throttling_share_one_adb_query():
    hardware = """ONLINE=0-1
/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq:1000000:2000000
/sys/devices/system/cpu/cpu1/cpufreq/scaling_cur_freq:1500000:2000000
"""
    collector = make_collector({"echo ONLINE=": "\n__PERFZY_cpu_hardware__\n" + hardware}, {
        "cpu_frequency", "cpu_online_cores", "cpu_throttling",
        "cpu_core_0_clock", "cpu_core_1_clock",
    })
    collector._sample_executor = ThreadPoolExecutor(max_workers=2)
    try:
        values = collector.sample(1.0).values
    finally:
        collector._sample_executor.shutdown(wait=True)

    assert values["cpu_frequency"] == pytest.approx(1250.0)
    assert values["cpu_online_cores"] == pytest.approx(2.0)
    assert values["cpu_throttling"] == pytest.approx(37.5)
    assert values["cpu_core_0_clock"] == pytest.approx(1000.0)
    assert values["cpu_core_1_clock"] == pytest.approx(1500.0)
    assert sum("echo ONLINE=" in call for call in collector.calls) == 1


def test_surface_frame_delta_becomes_fps():
    responses = {"--timestats -dump": SURFACE_TEMPLATE.format(frames=600, average="59.85",
                                                              smooth=580),
                 "dumpsys display": DISPLAY}
    collector = make_collector(responses, {"fps"})
    collector.activate_pending_sources()
    assert collector._display_refresh_rate == pytest.approx(120.0)

    assert collector.sample(1.0).values["fps"] is None

    responses["--timestats -dump"] = SURFACE_TEMPLATE.format(frames=720, average="59.85",
                                                             smooth=700)
    assert collector.sample(2.0).values["fps"] == pytest.approx(120.0)


def test_framestats_uses_the_header_and_rejects_incomplete_timestamp_sentinels():
    frames = AndroidCollector._parse_framestats(FRAMESTATS_WITH_SENTINEL, 0)

    assert frames == [(1_032_000_000, 32.0)]


def test_absurd_framestats_duration_cannot_flatten_frame_charts():
    invalid = FRAMESTATS_WITH_SENTINEL.replace(
        "0,0,0,0,0,0,0,0,0,0,0,0,0,298652166801421000,0,0,",
        "0,1000000000,1000000000,0,0,0,0,0,0,0,0,0,0,298653166801421000,0,0,")

    frames = AndroidCollector._parse_framestats(invalid, 0)

    assert frames == [(1_032_000_000, 32.0)]


def test_surface_switch_uses_the_layer_that_is_actively_presenting():
    old_layer = SURFACE_TEMPLATE.replace("#0", "#old")
    new_layer = SURFACE_TEMPLATE.replace("#0", "#new")
    responses = {"--timestats -dump": (
        old_layer.format(frames=1000, average="30", smooth=900) +
        new_layer.format(frames=10, average="30", smooth=8))}
    collector = make_collector(responses, {"fps"})

    assert collector.sample(1.0).values["fps"] is None
    responses["--timestats -dump"] = (
        old_layer.format(frames=1000, average="30", smooth=900) +
        new_layer.format(frames=40, average="30", smooth=38))

    assert collector.sample(2.0).values["fps"] == pytest.approx(30.0)
    assert collector._last_surface_name.endswith("#new")


def test_missing_old_pid_is_refreshed_after_an_app_restart():
    responses = {"top": TOP.replace("1234", "5678"), "pidof": "5678\n"}
    collector = make_collector(responses, {"app_cpu"})

    assert collector.sample(6.0).values["app_cpu"] is None
    assert collector._pid == "5678"


def test_frames_from_another_package_are_ignored():
    other = SURFACE_TEMPLATE.replace(PACKAGE, "com.other.app")
    collector = make_collector({"--timestats -dump": other.format(frames=600, average="59.9",
                                                                  smooth=580)}, {"fps"})
    assert collector.sample(1.0).values["fps"] is None


def test_frame_statistics_are_enabled_when_a_frame_metric_is_selected_later():
    collector = make_collector({}, {"app_cpu"})
    collector.activate_pending_sources()
    assert not any("timestats" in call for call in collector.calls)

    collector.set_enabled_metrics(frozenset({"app_cpu", "fps"}))
    collector.activate_pending_sources()
    assert any("--timestats -enable" in call for call in collector.calls)
    assert any("gfxinfo com.example.game reset" in call for call in collector.calls)


def test_frame_statistics_are_only_enabled_once():
    collector = make_collector({}, FRAME_METRICS)
    collector.activate_pending_sources()
    collector.activate_pending_sources()
    collector.activate_pending_sources()
    assert sum("--timestats -enable" in call for call in collector.calls) == 1


def test_stop_only_disables_frame_statistics_it_turned_on():
    collector = make_collector({}, {"app_cpu"})
    collector.stop()
    assert not any("timestats" in call for call in collector.calls)


def test_stop_disables_frame_statistics_after_use():
    collector = make_collector({}, {"fps"})
    collector.activate_pending_sources()
    collector.stop()
    assert any("--timestats -disable" in call for call in collector.calls)


def test_disabled_metrics_are_never_queried():
    collector = make_collector({}, {"app_cpu"})
    collector.sample(1.0)
    assert not any("meminfo" in call for call in collector.calls)
    assert not any("battery" in call for call in collector.calls)
    assert not any("timestats" in call for call in collector.calls)
