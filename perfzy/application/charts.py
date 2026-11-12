from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.metrics import METRIC_BY_KEY, METRICS, platform_metric_title, platform_metric_note
from ..domain.models import Platform


CORE_INDEX_FIELD = "{index}"


@dataclass(frozen=True, slots=True)
class ChartGroup:
    """One stacked chart in the workspace and the metric series it draws.

    ``per_core_metric`` expands to one series per mobile CPU core, so the same
    group definition works on a 4-core and a 10-core device.
    """

    key: str
    title: str
    group_title: str
    metrics: tuple[str, ...] = ()
    per_core_metric: str = ""

    def metric_keys(self, core_count: int) -> tuple[str, ...]:
        if not self.per_core_metric:
            return self.metrics
        return self.metrics + tuple(
            self.per_core_metric.format(index=index) for index in range(max(0, core_count)))


# Single source of truth for chart composition. Both the live workspace and the
# saved-session viewer read this, so a metric can never be collectable while
# belonging to no chart.
CHART_GROUPS: tuple[ChartGroup, ...] = (
    ChartGroup("fps", "FPS", "帧率", ("fps", "fps_1_low", "fps_0_1_low")),
    ChartGroup("frame", "Frame Performance", "帧性能",
               ("frame_time", "jank", "big_jank", "small_jank")),
    ChartGroup("jank_events", "Jank / Big Jank (Frame Events)", "逐帧卡顿",
               ("frame_jank", "frame_big_jank")),
    ChartGroup("frame_latency", "Frame Time Percentiles", "帧延迟分位数",
               ("frame_time_p95", "frame_time_p99")),
    ChartGroup("frame_quality", "Frame Quality", "帧质量",
               ("jank_rate", "dropped_frames", "dropped_frame_rate")),
    ChartGroup("frame_pipeline", "CPU Frame Timing", "CPU 帧阶段",
               ("cpu_frame_busy", "cpu_frame_wait")),
    ChartGroup("cpu", "CPU Usage", "CPU",
               ("app_cpu", "app_user_cpu", "app_kernel_cpu", "total_cpu")),
    ChartGroup("cpu_core_usage", "CPU Core Usage", "CPU 单核利用率",
               (), "cpu_core_{index}_usage"),
    ChartGroup("cpu_core_clock", "CPU Clock", "CPU 单核频率",
               ("cpu_frequency",), "cpu_core_{index}_clock"),
    ChartGroup("cpu_health", "CPU Health", "CPU 状态",
               ("cpu_online_cores", "cpu_throttling")),
    ChartGroup("memory", "Memory Usage", "内存",
               ("memory", "ios_physical_footprint", "private_memory", "ios_private_memory",
                "ios_compressed_memory", "ios_virtual_memory", "virtual_memory",
                "available_memory", "java_heap", "native_heap", "graphics_memory",
                "app_gpu_memory", "swap_memory", "app_rss", "app_uss", "memory_growth")),
    ChartGroup("memory_pressure", "Memory Faults", "内存缺页", ("page_faults",)),
    ChartGroup("gpu", "GPU Usage", "GPU",
               ("gpu", "app_gpu_usage", "ios_gpu_renderer", "ios_gpu_tiler",
                "gpu_clock_load")),
    ChartGroup("gpu_frame", "GPU Frame Timing", "GPU 帧耗时",
               ("gpu_time", "gpu_busy", "gpu_wait", "gpu_latency")),
    ChartGroup("gpu_memory", "GPU Memory", "GPU 显存",
               ("gpu_memory", "shared_gpu_memory", "ios_gpu_allocated_memory",
                "ios_gpu_in_use_memory")),
    ChartGroup("gpu_clock", "GPU Clock", "GPU 频率",
               ("gpu_frequency", "gpu_max_frequency")),
    ChartGroup("gpu_busy", "GPU Busy Time", "GPU 忙碌时间", ("gpu_busy_time",)),
    ChartGroup("thermal", "Temperature", "温度",
               ("temperature", "cpu_temperature", "gpu_temperature")),
    ChartGroup("power", "Power", "功耗与电池",
               ("power", "battery_level", "battery_current", "battery_voltage")),
    ChartGroup("network", "Network", "网络", ("network_send", "network_recv")),
    ChartGroup("io", "Disk I/O", "磁盘吞吐", ("disk_read", "disk_write")),
    ChartGroup("io_ops", "Disk Operations", "磁盘 IOPS",
               ("disk_read_iops", "disk_write_iops")),
    ChartGroup("process", "Process", "进程",
               ("thread_count", "handle_count", "app_threads", "ios_mach_ports",
                "ios_context_switches", "ios_interrupt_wakeups")),
    # Windows-only compositions below keep mobile chart definitions unchanged.
    ChartGroup("fps_low", "低帧率统计", "低帧率统计"),
    ChartGroup("stutter", "卡顿次数", "卡顿次数"),
    ChartGroup("display_drops", "显示丢帧次数", "显示丢帧次数"),
    ChartGroup("cpu_detail", "应用 CPU 明细", "应用 CPU 明细"),
    ChartGroup("system_cpu", "整机 CPU", "整机 CPU"),
    ChartGroup("memory_commit", "应用提交内存", "应用提交内存"),
    ChartGroup("system_memory", "整机物理内存", "整机物理内存"),
    ChartGroup("system_memory_commit", "整机提交内存", "整机提交内存"),
    ChartGroup("system_gpu", "整机 GPU", "整机 GPU"),
    ChartGroup("system_gpu_memory", "整机显存", "整机显存"),
    ChartGroup("gpu_shared_memory", "应用共享显存", "应用共享显存"),
    ChartGroup("memory_detail", "应用内存明细", "应用内存明细"),
    ChartGroup("virtual_memory", "虚拟地址空间", "虚拟地址空间"),
    ChartGroup("ios_graphics_detail", "设备 Renderer / Tiler", "设备 Renderer / Tiler"),
    ChartGroup("process_rates", "应用切换与唤醒", "应用切换与唤醒"),
    ChartGroup("battery_level", "设备电量", "设备电量"),
    ChartGroup("battery_current", "电池电流", "电池电流"),
    ChartGroup("battery_voltage", "电池电压", "电池电压"),
    ChartGroup("memory_resident", "应用 RSS / USS", "应用 RSS / USS"),
    ChartGroup("memory_growth", "PSS 相对增长", "PSS 相对增长"),
    ChartGroup("android_graphics_memory", "应用图形内存（可回退）", "应用图形内存（可回退）"),
    ChartGroup("gpu_clock_ratio", "GPU 频率比例", "GPU 频率比例"),
    ChartGroup("cpu_frequency_ratio", "CPU 频率降低比例", "CPU 频率降低比例"),
)

CHART_GROUP_BY_KEY: dict[str, ChartGroup] = {group.key: group for group in CHART_GROUPS}
DEFAULT_CHART_ORDER: tuple[str, ...] = tuple(group.key for group in CHART_GROUPS)
DEFAULT_VISIBLE_CHARTS: frozenset[str] = frozenset({"fps", "cpu", "network"})
WINDOWS_VISIBLE_CHARTS = frozenset({"fps", "frame", "cpu", "memory", "gpu", "gpu_memory"})
WINDOWS_CHART_METRICS = {
    "fps": ("fps",), "fps_low": ("fps_1_low", "fps_0_1_low"),
    "frame": ("frame_time",), "stutter": ("jank", "big_jank", "small_jank"),
    "display_drops": ("dropped_frames",),
    "frame_quality": ("jank_rate", "dropped_frame_rate"),
    "cpu": ("app_cpu",), "cpu_detail": ("app_user_cpu", "app_kernel_cpu"),
    "system_cpu": ("total_cpu", "system_cpu_user", "system_cpu_kernel"),
    "memory": ("memory", "private_memory"), "memory_commit": ("virtual_memory",),
    "system_memory": ("system_memory_used", "available_memory"),
    "system_memory_commit": ("system_commit_memory", "system_commit_limit"),
    "gpu_memory": ("gpu_memory",), "gpu_shared_memory": ("shared_gpu_memory",),
    "system_gpu": ("system_gpu_usage", "system_gpu_3d"),
    "system_gpu_memory": ("system_gpu_dedicated_memory", "system_gpu_shared_memory"),
}
WINDOWS_GROUP_TITLES = {
    "fps": "FPS", "frame": "Frame Time", "cpu": "App CPU", "memory": "App Memory",
    "system_cpu": "整机 CPU", "system_memory": "整机物理内存",
    "stutter": "Slow Frames (Fixed Threshold)",
    "gpu": "App GPU", "gpu_memory": "App Dedicated GPU Memory", "frame_latency": "Frame Time P95 / P99",
    "frame_quality": "Jank / Dropped Frame Rate", "network": "System Network (Shared)",
    "cpu_core_usage": "System CPU Cores", "io": "App Disk I/O", "io_ops": "App IOPS",
}
WINDOWS_SYSTEM_CHARTS = frozenset({"system_cpu", "system_memory", "system_memory_commit",
                                   "system_gpu", "system_gpu_memory", "cpu_core_usage", "network"})
IOS_VISIBLE_CHARTS = frozenset({"fps", "cpu", "memory", "gpu"})
IOS_CHART_METRICS = {
    "fps": ("fps",), "frame": ("frame_time",), "cpu": ("app_cpu",),
    "system_cpu": ("total_cpu",), "memory": ("ios_physical_footprint",),
    "memory_detail": ("app_rss", "ios_private_memory", "ios_compressed_memory"),
    "virtual_memory": ("ios_virtual_memory",), "gpu": ("gpu",),
    "ios_graphics_detail": ("ios_gpu_renderer", "ios_gpu_tiler"),
    "gpu_memory": ("ios_gpu_allocated_memory", "ios_gpu_in_use_memory"),
    "process": ("app_threads", "ios_mach_ports"),
    "process_rates": ("ios_context_switches", "ios_interrupt_wakeups"),
    "power": ("power",), "battery_level": ("battery_level",),
    "battery_current": ("battery_current",), "battery_voltage": ("battery_voltage",),
}
IOS_GROUP_TITLES = {
    "fps": "Device FPS", "frame": "Frame Time (Estimated)",
    "cpu": "App CPU", "memory": "Physical Footprint",
    "gpu": "Device GPU (Shared)", "gpu_memory": "Device Graphics Memory (Shared)",
    "network": "System Network (Shared)", "io": "App Disk I/O",
    "cpu_core_usage": "System CPU Cores", "thermal": "Battery Temperature",
    "power": "Battery Power (Estimated)", "process": "App Threads / Mach Ports",
}
IOS_SYSTEM_CHARTS = frozenset({"system_cpu", "cpu_core_usage", "network", "gpu_memory",
                               "power", "thermal", "battery_level", "battery_current", "battery_voltage"})
ANDROID_VISIBLE_CHARTS = frozenset({"fps", "cpu", "memory", "gpu"})
ANDROID_BASIC_CHARTS = ANDROID_VISIBLE_CHARTS | {'stutter'}
ANDROID_CHART_METRICS = {
    "fps": ("fps",), "frame": ("frame_time",),
    "stutter": ("jank", "big_jank", "small_jank"),
    "display_drops": ("dropped_frames",), "frame_quality": ("jank_rate",),
    "cpu": ("app_cpu",), "system_cpu": ("total_cpu",),
    "cpu_health": ("cpu_online_cores",), "cpu_frequency_ratio": ("cpu_throttling",),
    "memory": ("memory",),
    "memory_detail": ("java_heap", "native_heap", "graphics_memory", "swap_memory"),
    "memory_resident": ("app_rss", "app_uss"), "memory_growth": ("memory_growth",),
    "android_graphics_memory": ("app_gpu_memory",), "gpu": ("gpu",),
    "gpu_clock_ratio": ("gpu_clock_load",),
    "power": ("power",), "battery_level": ("battery_level",),
    "battery_current": ("battery_current",), "battery_voltage": ("battery_voltage",),
    "process": ("app_threads",),
}
ANDROID_GROUP_TITLES = {
    "fps": "App Layer FPS", "cpu": "App CPU", "memory": "App PSS",
    "gpu": "Device GPU (Shared)", "frame": "Frame Time (Sampled / Fallback)",
    "frame_latency": "Frame Time P95 / P99 (Sampled)", "stutter": "Slow Frames (Fixed Threshold)",
    "display_drops": "Dropped Frames (Budget Estimate)", "frame_quality": "Jank Rate (Fixed Threshold)",
    "memory_detail": "App PSS Categories / Swap", "cpu_health": "Online CPU Cores",
    "cpu_frequency_ratio": "CPU Frequency Reduction",
    "network": "Device Network (Shared)", "process": "App Threads",
    "power": "Battery Power (Estimated)", "gpu_busy": "GPU Busy Time (Estimated)",
    "gpu_clock": "Device GPU Clock", "cpu_core_clock": "System CPU Clock",
    "cpu_core_usage": "System CPU Cores", "thermal": "Device Temperature",
}
ANDROID_SYSTEM_CHARTS = frozenset({"system_cpu", "cpu_core_usage", "cpu_core_clock", "cpu_health",
    "cpu_frequency_ratio", "gpu_clock", "gpu_clock_ratio", "gpu_busy", "network", "thermal",
    "power", "battery_level", "battery_current", "battery_voltage"})


def _compositions(platform):
    return {Platform.WINDOWS: WINDOWS_CHART_METRICS, Platform.IOS: IOS_CHART_METRICS,
            Platform.ANDROID: ANDROID_CHART_METRICS}.get(platform, {})


def chart_title(group: ChartGroup, platform: Platform | None) -> str:
    overrides = {Platform.WINDOWS: WINDOWS_GROUP_TITLES, Platform.IOS: IOS_GROUP_TITLES,
                 Platform.ANDROID: ANDROID_GROUP_TITLES}.get(platform)
    common = {
        'fps_low': 'Low FPS', 'stutter': 'Jank Count', 'display_drops': 'Dropped Frames',
        'cpu_detail': 'App CPU Details', 'system_cpu': 'System CPU',
        'memory_commit': 'App Commit Size', 'system_memory': 'Available System Memory',
        'gpu_shared_memory': 'App Shared GPU Memory', 'memory_detail': 'App Memory Details',
        'virtual_memory': 'Virtual Address Space', 'ios_graphics_detail': 'Device Renderer / Tiler',
        'process_rates': 'App Context Switches / Wakeups', 'battery_level': 'Battery Level',
        'battery_current': 'Battery Current', 'battery_voltage': 'Battery Voltage',
        'memory_resident': 'App RSS / USS', 'memory_growth': 'PSS Growth',
        'android_graphics_memory': 'App Graphics Memory (Fallback)',
        'gpu_clock_ratio': 'GPU Clock Ratio', 'cpu_frequency_ratio': 'CPU Frequency Reduction',
    }
    default = common.get(group.key, group.title)
    return overrides.get(group.key, default) if overrides is not None else default


def chart_metric_keys(group: ChartGroup, platform: Platform, core_count: int) -> tuple[str, ...]:
    if group.key in _compositions(platform):
        return _compositions(platform)[group.key]
    return group.metric_keys(core_count)

_STATIC_GROUP_BY_METRIC: dict[str, str] = {
    metric: group.key for group in CHART_GROUPS for metric in group.metrics}


def _per_core_pattern(template: str) -> re.Pattern[str]:
    prefix, _, suffix = template.partition(CORE_INDEX_FIELD)
    return re.compile(f"^{re.escape(prefix)}(\\d+){re.escape(suffix)}$")


_PER_CORE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (_per_core_pattern(group.per_core_metric), group.key)
    for group in CHART_GROUPS if group.per_core_metric)


def core_index(metric_key: str) -> int | None:
    """CPU core a per-core metric belongs to, or None for ordinary metrics."""
    for pattern, _group_key in _PER_CORE_PATTERNS:
        match = pattern.match(metric_key)
        if match:
            return int(match.group(1))
    return None


def group_key_for_metric(metric_key: str, platform: Platform | None = None) -> str | None:
    """Chart group that draws the given metric."""
    if _compositions(platform):
        for group_key, keys in _compositions(platform).items():
            if metric_key in keys:
                return group_key
    for pattern, group_key in _PER_CORE_PATTERNS:
        if pattern.match(metric_key):
            return group_key
    return _STATIC_GROUP_BY_METRIC.get(metric_key)


def metric_available(metric_key: str, core_count: int) -> bool:
    """Whether the device exposes this metric at all.

    Per-core metrics only exist for cores the selected device actually has.
    """
    index = core_index(metric_key)
    return index is None or index < core_count


def supported_chart_keys(platform: Platform, chart_order: list[str] | tuple[str, ...],
                         core_count: int) -> list[str]:
    """Chart groups that have at least one metric collectable on this platform."""
    return [key for key in chart_order
            if key in CHART_GROUP_BY_KEY
            and any(platform in METRIC_BY_KEY[metric].platforms
                    for metric in chart_metric_keys(CHART_GROUP_BY_KEY[key], platform, core_count))]


def build_chart_catalog(platform: Platform, chart_order: list[str] | tuple[str, ...],
                        visible_charts: set[str] | frozenset[str],
                        enabled_metrics: set[str] | frozenset[str],
                        core_count: int, availability: dict | None = None) -> list[dict]:
    """Chart descriptors for QML, in the user's configured order."""
    supported = supported_chart_keys(platform, chart_order, core_count)
    catalog: list[dict] = []
    for order, group_key in enumerate(supported):
        group = CHART_GROUP_BY_KEY[group_key]
        keys = [metric for metric in chart_metric_keys(group, platform, core_count)
                if platform in METRIC_BY_KEY[metric].platforms]
        states = [(availability or {}).get(metric, {}) for metric in keys]
        move_order = ([key for key in supported if key in visible_charts]
                      if _compositions(platform) and group_key in visible_charts else supported)
        move_index = move_order.index(group_key)
        title = chart_title(group, platform)
        tier = ("basic" if group.key in WINDOWS_VISIBLE_CHARTS else
                "system" if group.key in WINDOWS_SYSTEM_CHARTS else "advanced") if platform is Platform.WINDOWS else "basic"
        if platform is Platform.IOS:
            tier = "basic" if group.key in IOS_VISIBLE_CHARTS else "system" if group.key in IOS_SYSTEM_CHARTS else "advanced"
        if platform is Platform.ANDROID:
            tier = "basic" if group.key in ANDROID_BASIC_CHARTS else "system" if group.key in ANDROID_SYSTEM_CHARTS else "advanced"
        if group.key == 'jank_events':
            tier = 'basic'
        pending = "待检测" if _compositions(platform) else ""
        show_notes = platform in {Platform.IOS, Platform.ANDROID} or (platform is Platform.WINDOWS and tier == "system")
        reasons = ["\n".join(text for text in (platform_metric_note(key, platform) if show_notes else "",
                                              state.get("reason", "开始采集后检测；没有数据不代表零。" if pending else "")) if text)
                   for key, state in zip(keys, states)]
        catalog.append({
            "key": group.key,
            "title": title,
            "axisUnit": METRIC_BY_KEY[keys[0]].unit if _compositions(platform) else "",
            "tier": tier,
            "capabilityNote": ("Frame Time 此曲线仍为 1000/FPS 估算。新采集的 FPS / Frame Time 会启用 CoreProfile 逐帧通道并保存 FrameTimes，也可单独启用 Jank / Big Jank；Small Jank 暂不可采集。"
                               if platform is Platform.IOS and group.key == 'frame' else
                               "FPS 优先使用实际逐帧时间间隔；通道未就绪或不可用时回退整数计数器。当前来源见采集诊断。"
                               if platform is Platform.IOS and group.key == 'fps' else ''),
            "metricKeys": keys,
            "seriesNames": [platform_metric_title(metric, platform) for metric in keys],
            "seriesColors": [METRIC_BY_KEY[metric].color for metric in keys],
            "seriesEnabled": [metric in enabled_metrics for metric in keys],
            "seriesSelectable": [state.get("state") != "unavailable" for state in states],
            "seriesStatus": [state.get("label", pending) for state in states],
            "seriesReasons": reasons,
            "metrics": [{"key": key, "title": platform_metric_title(key, platform),
                         "unit": METRIC_BY_KEY[key].unit, "enabled": key in enabled_metrics,
                         "selectable": state.get("state") != "unavailable",
                         "status": state.get("label", pending), "reason": reason}
                        for key, state, reason in zip(keys, states, reasons)],
            "visible": group.key in visible_charts,
            "canMoveUp": move_index > 0,
            "canMoveDown": move_index < len(move_order) - 1,
        })
    return catalog


def build_metric_catalog(platform: Platform, enabled_metrics: set[str] | frozenset[str],
                         core_count: int, availability: dict | None = None) -> list[dict]:
    """Selectable metrics for this platform, tagged with their chart group."""
    catalog: list[dict] = []
    for metric in METRICS:
        if platform not in metric.platforms or not metric_available(metric.key, core_count):
            continue
        group_key = group_key_for_metric(metric.key, platform) or metric.group
        group = CHART_GROUP_BY_KEY.get(group_key)
        catalog.append({
            "key": metric.key,
            "title": platform_metric_title(metric.key, platform),
            "unit": metric.unit,
            "group": group_key,
            "groupTitle": chart_title(group, platform) if group else group_key,
            "color": metric.color,
            "description": platform_metric_note(metric.key, platform),
            "requirement": metric.requirement,
            "enabled": metric.key in enabled_metrics,
            "selectable": (availability or {}).get(metric.key, {}).get("state") != "unavailable",
            "status": (availability or {}).get(metric.key, {}).get("label", ""),
            "reason": (availability or {}).get(metric.key, {}).get("reason", ""),
        })
    return catalog


def groups_with_data(samples: dict[str, list], platform: Platform | None = None) -> set[str]:
    """Chart groups that have at least one recorded sample in a saved session."""
    return {group_key for metric_key, rows in samples.items() if rows
            for group_key in (group_key_for_metric(metric_key, platform),) if group_key}
