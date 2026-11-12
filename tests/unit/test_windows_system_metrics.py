from collections import namedtuple
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from perfzy.application.preferences import Preferences, WINDOWS_NEW_DEFAULT_METRICS
from perfzy.collectors import windows, windows_system
from perfzy.domain.models import Platform
from test_windows_collector import make_collector


def test_system_gpu_combines_processes_on_each_engine_only():
    adapter_a = "luid_0x0_0x1_phys_0"
    adapter_b = "luid_0x0_0x2_phys_0"
    usage = windows_system._engine_usage({
        f"pid_4_{adapter_a}_eng_0_engtype_3D": 23.0,
        f"pid_5_{adapter_a}_eng_0_engtype_3D": 30.0,
        f"pid_4_{adapter_a}_eng_1_engtype_Copy": 60.0,
        f"pid_7_{adapter_b}_eng_0_engtype_3D": 40.0,
        "_Total": 99.0,
    })
    assert usage == {"system_gpu_usage": 60.0, "system_gpu_3d": 53.0}
    assert windows_system._engine_usage({}) == {}
    assert windows_system._engine_usage({f"pid_4_{adapter_a}_eng_0_engtype_3D": 0.0}) == {
        "system_gpu_usage": 0.0, "system_gpu_3d": 0.0}


def test_pdh_array_omits_invalid_instance_values(monkeypatch):
    import ctypes
    from ctypes import wintypes

    items = (windows_system._FormattedItem * 3)()
    for index, status in enumerate((0, 1, 0xC0000BC6)):
        items[index].name = f"row_{index}"
        items[index].value.status = status
        items[index].value.data.double_value = (0.0, 20.0, 91.0)[index]

    def fake_api(handle, fmt, size, count, buffer):
        ctypes.cast(size, ctypes.POINTER(wintypes.DWORD))[0] = ctypes.sizeof(items)
        if buffer is None:
            return windows_system._PDH_MORE_DATA
        ctypes.cast(count, ctypes.POINTER(wintypes.DWORD))[0] = len(items)
        ctypes.memmove(buffer, items, ctypes.sizeof(items))
        return 0

    monkeypatch.setattr(windows_system, "_formatted_array_api", lambda: fake_api)
    monkeypatch.setattr(windows_system, "win32pdh", SimpleNamespace(PDH_FMT_DOUBLE=512))
    assert windows_system._read_array(10, 512) == {"row_0": 0.0, "row_1": 20.0}


def test_pdh_queries_only_enabled_families_and_reuses_query(monkeypatch):
    calls = []
    pdh = SimpleNamespace(
        error=OSError, PDH_FMT_DOUBLE=512, PDH_FMT_LARGE=1024,
        OpenQuery=lambda: calls.append("open") or "query",
        AddEnglishCounter=lambda query, path: calls.append(path) or path,
        CollectQueryData=lambda query: calls.append("collect"),
        CloseQuery=lambda query: calls.append("close"),
    )
    monkeypatch.setattr(windows_system, "win32pdh", pdh)
    monkeypatch.setattr(windows_system, "_read_array", lambda handle, fmt: {
        "luid_0x0_0x1_phys_0": 1024 ** 2, "luid_0x0_0x2_phys_0": 2 * 1024 ** 2, "_Total": 3 * 1024 ** 2,
    })
    counters = windows_system.WindowsSystemCounters()
    enabled = frozenset({"system_gpu_dedicated_memory"})
    assert counters.sample(enabled, 0.0) == {"system_gpu_dedicated_memory": 3.0}
    assert counters.sample(enabled, 0.5) == {"system_gpu_dedicated_memory": 3.0}
    assert calls == ["open", r"\GPU Adapter Memory(*)\Dedicated Usage", "collect", "collect"]
    assert counters.sample(frozenset(), 1.0) == {}
    assert calls[-1] == "close"


def test_system_cpu_and_memory_are_independent_of_target(monkeypatch):
    Times = namedtuple("Times", "user system idle interrupt dpc")
    cpu_times = iter([
        [Times(10, 10, 20, 1, 1), Times(10, 10, 20, 1, 1)],
        [Times(12, 11, 27, 2, 2), Times(12, 11, 27, 2, 2)],
    ])
    monkeypatch.setattr(windows.psutil, "cpu_times", lambda *, percpu: next(cpu_times))
    monkeypatch.setattr(windows.psutil, "virtual_memory", lambda: SimpleNamespace(
        total=16 * 1024 ** 2, available=6 * 1024 ** 2))
    collector = make_collector(enabled={
        "total_cpu", "system_cpu_user", "system_cpu_kernel", "available_memory", "system_memory_used"})
    collector._cpus = 2
    sample = collector.sample(1)
    assert sample.values == pytest.approx({"total_cpu": 30, "system_cpu_user": 20, "system_cpu_kernel": 10,
                                          "available_memory": 6, "system_memory_used": 10})
    assert sample.sources["system_memory_used"] == "Windows physical memory snapshot"
    assert sample.sources["total_cpu"] == "Windows system CPU times"


def test_new_pc_metrics_are_added_once_to_existing_selection(tmp_path):
    path = tmp_path / "settings.ini"
    settings = QSettings(str(path), QSettings.IniFormat)
    settings.setValue("migrations/windows_layout_v3", True)
    settings.setValue("metrics/windows/compact_enabled", ["fps", "total_cpu"])
    settings.setValue("charts/windows_visible", ["fps"])
    settings.setValue("charts/order", ["memory", "fps"])
    settings.sync()

    preferences = Preferences(path)
    assert preferences.enabled_for(Platform.WINDOWS) == {"fps", "total_cpu"} | WINDOWS_NEW_DEFAULT_METRICS
    assert preferences.windows_visible_charts == {"fps", "system_cpu", "system_memory", "system_gpu", "system_gpu_memory"}
    assert preferences.chart_order[:2] == ["memory", "fps"]
    preferences.set_metric_enabled(Platform.WINDOWS, "system_gpu_usage", False)
    preferences.windows_visible_charts.discard("system_gpu")
    preferences.save()
    restored = Preferences(path)
    assert "system_gpu_usage" not in restored.enabled_for(Platform.WINDOWS)
    assert "system_gpu" not in restored.windows_visible_charts



def test_windows_gpu_inventory_ignores_virtual_display_and_retains_identical_physical_gpus(monkeypatch):
    # The same model and capacity can be reported by two cards, by a virtual
    # display using that renderer, or by a second view of the same PnP device.
    paths = {
        1: r"\\?\pci#ven_10de&dev_2d04#slot_1",
        2: r"\\?\root#display#0000",
        3: r"\\?\pci#ven_10de&dev_2d04#slot_1",
        4: r"\\?\pci#ven_10de&dev_2d04#slot_2",
    }

    def create_factory(_iid, output):
        output._obj.value = 1
        return 0

    def enum_adapters(_factory, index, output):
        if index >= len(paths):
            return -1
        output._obj.value = index + 1
        return 0

    def get_desc(adapter, output):
        output._obj.name = "NVIDIA GeForce RTX 5060 Ti"
        output._obj.luid_low = adapter.value
        output._obj.dedicated_video_memory = 16 * 1024 ** 3
        return 0

    methods = {2: lambda _adapter: 0, 10: get_desc, 12: enum_adapters}
    monkeypatch.setattr(windows_system.ctypes, "WinDLL",
                        lambda _name: SimpleNamespace(CreateDXGIFactory1=create_factory), raising=False)
    monkeypatch.setattr(windows_system, "_com_method", lambda _object, slot, *_: methods[slot])
    monkeypatch.setattr(windows_system, "_display_adapter_path", lambda desc: paths[desc.luid_low])
    expected = "NVIDIA GeForce RTX 5060 Ti（专用显存 16.0 GB）"
    assert windows_system.windows_gpu_description().split(" / ") == [expected, expected]
