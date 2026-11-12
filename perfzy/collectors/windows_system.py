"""Windows system counters and local adapter identity, independent of the target PID."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache
from itertools import count
import math
import re
import uuid

try:
    import win32pdh
except ImportError:  # Optional outside the Windows runtime.
    win32pdh = None


_MIB = 1024 ** 2
_PDH_MORE_DATA = 0x800007D2
_PDH_VALID_STATUSES = frozenset({0, 1})  # PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA
_GPU_ENGINE_METRICS = frozenset({"system_gpu_usage", "system_gpu_3d"})
_GPU_MEMORY_COUNTERS = {
    "system_gpu_dedicated_memory": r"\GPU Adapter Memory(*)\Dedicated Usage",
    "system_gpu_shared_memory": r"\GPU Adapter Memory(*)\Shared Usage",
}
_PDH_PATHS = {
    "system_gpu_usage": r"\GPU Engine(*)\Utilization Percentage",
    **_GPU_MEMORY_COUNTERS,
    "system_commit_memory": r"\Memory\Committed Bytes",
    "system_commit_limit": r"\Memory\Commit Limit",
}
_ADAPTER_INSTANCE = r"luid_0x[0-9a-f]+_0x[0-9a-f]+_phys_\d+"
_ENGINE_INSTANCE = re.compile(rf"pid_\d+_({_ADAPTER_INSTANCE}_eng_\d+)_engtype_(.+)", re.IGNORECASE)
_MEMORY_INSTANCE = re.compile(_ADAPTER_INSTANCE, re.IGNORECASE)


class _FormattedValueData(ctypes.Union):
    _fields_ = [("double_value", ctypes.c_double), ("large_value", ctypes.c_longlong)]


class _FormattedValue(ctypes.Structure):
    _fields_ = [("status", wintypes.DWORD), ("data", _FormattedValueData)]


class _FormattedItem(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("value", _FormattedValue)]


@lru_cache(maxsize=1)
def _formatted_array_api():
    get_array = ctypes.WinDLL("pdh.dll").PdhGetFormattedCounterArrayW
    get_array.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
    get_array.restype = wintypes.LONG
    return get_array


def _read_array(handle, fmt: int) -> dict[str, float]:
    # pywin32's array wrapper drops the per-instance status. Check it before
    # accepting a value, especially for new GPU engine instances.
    read = _formatted_array_api()
    size, item_count = wintypes.DWORD(), wintypes.DWORD()
    status = read(int(handle), fmt, ctypes.byref(size), ctypes.byref(item_count), None)
    for _ in range(2):
        if (status & 0xFFFFFFFF) != _PDH_MORE_DATA or not size.value:
            break
        buffer = ctypes.create_string_buffer(size.value)
        status = read(int(handle), fmt, ctypes.byref(size), ctypes.byref(item_count), buffer)
        if status == 0:
            items = ctypes.cast(buffer, ctypes.POINTER(_FormattedItem))
            values = {}
            for index in range(item_count.value):
                item = items[index]
                if item.value.status not in _PDH_VALID_STATUSES:
                    continue
                data = item.value.data
                value = data.double_value if fmt == win32pdh.PDH_FMT_DOUBLE else data.large_value
                name = item.name
                if name and math.isfinite(value) and value >= 0:
                    values[name] = value
            return values
    return {}


def _engine_usage(instances: dict[str, float]) -> dict[str, float]:
    # Sum process slices on the same physical engine, then take the busiest
    # engine. Independent engines can run concurrently.
    engines: dict[tuple[str, str], float] = {}
    for instance, usage in instances.items():
        match = _ENGINE_INSTANCE.fullmatch(instance)
        if match:
            engine = (match[1].casefold(), match[2].casefold())
            engines[engine] = engines.get(engine, 0.0) + usage
    if not engines:
        return {}
    values = {"system_gpu_usage": min(100.0, max(engines.values()))}
    engines_3d = [usage for (_, kind), usage in engines.items() if kind == "3d"]
    if engines_3d:
        values["system_gpu_3d"] = min(100.0, max(engines_3d))
    return values


class WindowsSystemCounters:
    """Keep one local PDH query for enabled system metrics; reuse it across samples."""

    RETRY_SECONDS = 5.0

    def __init__(self) -> None:
        self._query = None
        self._handles: dict[str, object] = {}
        self._requested: frozenset[str] = frozenset()
        self._retry_at = 0.0
        self._engine_ready = False

    def close(self) -> None:
        if self._query is not None and win32pdh is not None:
            try:
                win32pdh.CloseQuery(self._query)
            except win32pdh.error:
                pass
        self._query = None
        self._handles.clear()
        self._requested = frozenset()
        self._retry_at = 0.0
        self._engine_ready = False

    def _add_missing(self, requested: frozenset[str], now: float) -> None:
        self._retry_at = now + self.RETRY_SECONDS
        if self._query is None:
            try:
                self._query = win32pdh.OpenQuery()
            except win32pdh.error:
                return
        for key in sorted(requested - self._handles.keys()):
            try:
                self._handles[key] = win32pdh.AddEnglishCounter(self._query, _PDH_PATHS[key])
            except win32pdh.error:
                continue

    def sample(self, enabled: frozenset[str], now: float) -> dict[str, float]:
        requested = frozenset(enabled & _PDH_PATHS.keys())
        if enabled & _GPU_ENGINE_METRICS:
            requested |= {"system_gpu_usage"}
        if requested != self._requested:
            self.close()
            self._requested = requested
        if not requested or win32pdh is None:
            return {}
        if now >= self._retry_at and requested - self._handles.keys():
            self._add_missing(requested, now)
        if not self._handles:
            return {}
        try:
            win32pdh.CollectQueryData(self._query)
        except win32pdh.error:
            self.close()
            self._requested = requested
            self._retry_at = now + self.RETRY_SECONDS
            return {}

        values: dict[str, float] = {}
        for key, handle in self._handles.items():
            try:
                if key == "system_gpu_usage":
                    if self._engine_ready:
                        values.update(_engine_usage(_read_array(handle, win32pdh.PDH_FMT_DOUBLE)))
                    self._engine_ready = True
                elif key in _GPU_MEMORY_COUNTERS:
                    instances = _read_array(handle, win32pdh.PDH_FMT_LARGE)
                    adapter_values = [value for name, value in instances.items() if _MEMORY_INSTANCE.fullmatch(name)]
                    if adapter_values:
                        values[key] = sum(adapter_values) / _MIB
                else:
                    value = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_LARGE)[1]
                    if math.isfinite(value) and value > 0:
                        values[key] = value / _MIB
            except (win32pdh.error, OSError):
                continue
        return {key: value for key, value in values.items() if key in enabled}


_DXGI_ADAPTER_FLAG_SOFTWARE = 2
_DISPLAYCONFIG_GET_ADAPTER_NAME = 4
_ROOT_DISPLAY_PATH_PREFIX = r"\\?\root#display#"


class _AdapterDesc(ctypes.Structure):
    _fields_ = [
        ("name", wintypes.WCHAR * 128),
        ("vendor_id", wintypes.UINT), ("device_id", wintypes.UINT),
        ("subsys_id", wintypes.UINT), ("revision", wintypes.UINT),
        ("dedicated_video_memory", ctypes.c_size_t),
        ("dedicated_system_memory", ctypes.c_size_t),
        ("shared_system_memory", ctypes.c_size_t),
        ("luid_low", wintypes.DWORD), ("luid_high", wintypes.LONG),
        ("flags", wintypes.UINT),
    ]


class _DisplayConfigAdapterName(ctypes.Structure):
    # DISPLAYCONFIG_DEVICE_INFO_HEADER followed by adapterDevicePath[128].
    _fields_ = [
        ("request_type", wintypes.UINT), ("size", wintypes.UINT),
        ("luid_low", wintypes.DWORD), ("luid_high", wintypes.LONG),
        ("source_id", wintypes.UINT), ("device_path", wintypes.WCHAR * 128),
    ]


def _display_adapter_path(desc: _AdapterDesc) -> str:
    request = _DisplayConfigAdapterName(
        _DISPLAYCONFIG_GET_ADAPTER_NAME, ctypes.sizeof(_DisplayConfigAdapterName),
        desc.luid_low, desc.luid_high, 0,
    )
    try:
        status = ctypes.windll.user32.DisplayConfigGetDeviceInfo(ctypes.byref(request))
    except (AttributeError, OSError):
        return ""
    return request.device_path.casefold() if status == 0 else ""


def _com_method(interface, slot: int, result_type, *argument_types):
    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argument_types)(vtable[slot])


def windows_gpu_description() -> str:
    """Enumerate DXGI hardware adapters, including ones without a display output."""
    factory = ctypes.c_void_p()
    iid = (ctypes.c_ubyte * 16).from_buffer_copy(
        uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
    try:
        create = ctypes.WinDLL("dxgi.dll").CreateDXGIFactory1
        create.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        create.restype = wintypes.LONG
        if create(ctypes.byref(iid), ctypes.byref(factory)) < 0 or not factory:
            return ""
    except (AttributeError, OSError):
        return ""

    adapters = []
    seen_luids = set()
    seen_paths = set()
    release_slot = 2  # IUnknown::Release
    try:
        enum_adapters = _com_method(factory, 12, wintypes.LONG, wintypes.UINT,
                                    ctypes.POINTER(ctypes.c_void_p))  # IDXGIFactory1::EnumAdapters1
        for index in count():
            adapter = ctypes.c_void_p()
            if enum_adapters(factory, index, ctypes.byref(adapter)) < 0:
                break
            try:
                desc = _AdapterDesc()
                get_desc = _com_method(adapter, 10, wintypes.LONG,
                                       ctypes.POINTER(_AdapterDesc))  # IDXGIAdapter1::GetDesc1
                if get_desc(adapter, ctypes.byref(desc)) < 0 or desc.flags & _DXGI_ADAPTER_FLAG_SOFTWARE:
                    continue
                name = desc.name.strip()
                luid = (desc.luid_high, desc.luid_low)
                if not name or luid in seen_luids:
                    continue
                device_path = _display_adapter_path(desc)
                # An indirect display can mirror its renderer's name and VRAM,
                # with its own LUID and no DXGI software flag.
                if device_path.startswith(_ROOT_DISPLAY_PATH_PREFIX) or device_path in seen_paths:
                    continue
                seen_luids.add(luid)
                if device_path:
                    seen_paths.add(device_path)
                memory = desc.dedicated_video_memory
                capacity = f"{memory / (1024 ** 3):.1f} GB" if memory >= 1024 ** 3 else f"{memory / _MIB:.0f} MB"
                adapters.append(f"{name}（专用显存 {capacity}）")
            finally:
                _com_method(adapter, release_slot, wintypes.ULONG)(adapter)
    finally:
        _com_method(factory, release_slot, wintypes.ULONG)(factory)
    return " / ".join(adapters)
