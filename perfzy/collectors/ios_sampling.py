"""Device-side subscriptions, separate from desktop display/persistence gates."""
from __future__ import annotations
from .ios_frames import TRACE_METRICS

PROCESS_FIELDS = {
    "app_cpu": {"cpuUsage"},
    "ios_physical_footprint": {"physFootprint"},
    "app_rss": {"memResidentSize"},
    "ios_private_memory": {"memRPrvt"},
    "ios_compressed_memory": {"memCompressed"},
    "ios_virtual_memory": {"memVirtualSize"},
    "app_threads": {"threadCount"},
    "ios_mach_ports": {"machPortCount"},
    "disk_read": {"diskBytesRead"},
    "disk_write": {"diskBytesWritten"},
    "ios_context_switches": {"ctxSwitch"},
    "ios_interrupt_wakeups": {"intWakeups"},
}
SYSTEM_FIELDS = {"network_send": {"netBytesOut"}, "network_recv": {"netBytesIn"}}
GRAPHICS_METRICS = frozenset({"fps", "frame_time", "gpu", "ios_gpu_renderer", "ios_gpu_tiler",
                              "ios_gpu_allocated_memory", "ios_gpu_in_use_memory"})
BATTERY_METRICS = frozenset({"battery_level", "battery_current", "battery_voltage", "temperature", "power"})
DEFAULT_METRICS = frozenset(PROCESS_FIELDS) | frozenset(SYSTEM_FIELDS) | GRAPHICS_METRICS | BATTERY_METRICS | {"total_cpu"}


def sampling_plan(metrics, interval_ms=1000):
    enabled = frozenset(metrics)
    interval = max(500, int(interval_ms))
    cpu = bool(enabled & {"app_cpu", "total_cpu"}) or any(
        key.startswith("cpu_core_") and key.endswith("_usage") for key in enabled)
    process = set().union(*(PROCESS_FIELDS.get(key, set()) for key in enabled))
    system = set().union(*(SYSTEM_FIELDS.get(key, set()) for key in enabled))
    # The collector maintains paired cumulative baselines for these rates.
    if enabled & {"disk_read", "disk_write"}:
        process.update({"diskBytesRead", "diskBytesWritten"})
    if enabled & {"ios_context_switches", "ios_interrupt_wakeups"}:
        process.update({"ctxSwitch", "intWakeups"})
    if enabled & {"network_send", "network_recv"}:
        system.update({"netBytesOut", "netBytesIn"})
    sysmon = bool(process or system or cpu)
    if sysmon:
        process.update({"pid", "name", "comm"})
    return {"metrics": sorted(enabled), "interval": interval, "process_fields": sorted(process),
            "system_fields": sorted(system), "cpu": cpu, "footprint": "ios_physical_footprint" in enabled,
            "sysmon": sysmon, "graphics": bool(enabled & GRAPHICS_METRICS), "frames": bool(enabled & TRACE_METRICS),
            "battery": bool(enabled & BATTERY_METRICS), "guard_interval": max(interval, 2000)}
