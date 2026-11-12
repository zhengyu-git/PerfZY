from __future__ import annotations

from .models import MetricDefinition, Platform
from .jank import JANK_DESCRIPTION, JANK_METRICS


ALL = frozenset(Platform)
WINDOWS = frozenset({Platform.WINDOWS})
ANDROID = frozenset({Platform.ANDROID})
IOS = frozenset({Platform.IOS})
MOBILE = ANDROID | IOS
IMPLEMENTED = WINDOWS | ANDROID | IOS

# Metric families are derived from the local PerfZY UI/protocol resources. Only
# platforms with a genuine collector path expose a metric as selectable.
_CORE_COLORS = ("#66a3ff", "#58c7d9", "#55c98b", "#a7cf55", "#e3c34f", "#ef9b52", "#e66c75", "#bd75df")

METRICS: tuple[MetricDefinition, ...] = (
    MetricDefinition("fps", "FPS", "FPS", "frame", "#d84ab8", IMPLEMENTED, "Frames rendered per second"),
    MetricDefinition("fps_1_low", "1% Low FPS", "FPS", "frame", "#f07acb", WINDOWS, "Rolling 60-second average FPS of the slowest one percent of presented frames"),
    MetricDefinition("fps_0_1_low", "0.1% Low FPS", "FPS", "frame", "#b96be3", WINDOWS, "Rolling 60-second average FPS of the slowest 0.1 percent of presented frames"),
    MetricDefinition("frame_time", "Frame Time", "ms", "frame", "#20c8d8", IMPLEMENTED, "Time interval between consecutive presented frames"),
    MetricDefinition("frame_jank", "Jank", "count", "jank_events", "#ff944d", IMPLEMENTED, JANK_DESCRIPTION),
    MetricDefinition("frame_big_jank", "Big Jank", "count", "jank_events", "#f25573", IMPLEMENTED, JANK_DESCRIPTION),
    MetricDefinition("jank", "Slow Frames (33.34–50.01 ms)", "count", "frame", "#ff944d", WINDOWS | ANDROID, "Fixed-threshold frame count in (33.34,50.01] ms; not ordered-frame Jank"),
    MetricDefinition("big_jank", "Slow Frames (>50.01 ms)", "count", "frame", "#f25573", WINDOWS | ANDROID, "Fixed-threshold frame count above 50.01 ms; not ordered-frame Big Jank"),
    MetricDefinition("small_jank", "Slow Frames (16.67–33.34 ms)", "count", "frame", "#9d6ce8", WINDOWS | ANDROID, "Fixed-threshold frame count in (16.67,33.34] ms"),
    MetricDefinition("frame_time_p95", "Frame Time P95", "ms", "frame_latency", "#31b9d4", WINDOWS | ANDROID, "95th percentile frame time in the current sampling window"),
    MetricDefinition("frame_time_p99", "Frame Time P99", "ms", "frame_latency", "#796ee6", WINDOWS | ANDROID, "99th percentile frame time in the current sampling window"),
    MetricDefinition("jank_rate", "Jank Rate", "%", "frame_quality", "#ef8b42", WINDOWS | ANDROID, "Percentage of frames over the jank threshold"),
    MetricDefinition("dropped_frames", "Dropped Frames", "count", "frame_quality", "#e3566f", WINDOWS | ANDROID, "Frames that were presented but never displayed"),
    MetricDefinition("dropped_frame_rate", "Dropped Frame Rate", "%", "frame_quality", "#ba5b75", WINDOWS, "Percentage of presented frames that were not displayed in the current sampling window"),
    MetricDefinition("cpu_frame_busy", "CPU Busy", "ms", "frame_pipeline", "#55b9e6", WINDOWS, "CPU work time per presented frame"),
    MetricDefinition("cpu_frame_wait", "CPU Wait", "ms", "frame_pipeline", "#e0a83d", WINDOWS, "CPU wait time between presented frames"),
    MetricDefinition("gpu_time", "GPU Time", "ms", "gpu_frame", "#9b6ce3", WINDOWS, "Total GPU processing span per presented frame"),
    MetricDefinition("gpu_busy", "GPU Busy", "ms", "gpu_frame", "#b35ee4", WINDOWS, "Time the GPU actively worked on each presented frame"),
    MetricDefinition("gpu_wait", "GPU Wait", "ms", "gpu_frame", "#d978b7", WINDOWS, "Time the GPU was idle within each presented frame's GPU span"),
    MetricDefinition("gpu_latency", "GPU Latency", "ms", "gpu_frame", "#6f82e8", WINDOWS, "Time from CPU frame start until GPU work began"),
    MetricDefinition("app_cpu", "App CPU Usage", "%", "cpu", "#4d91ff", IMPLEMENTED, "Target-process CPU normalized to whole-device capacity (100% means all logical cores)"),
    MetricDefinition("app_user_cpu", "App User CPU", "%", "cpu", "#69a9ff", WINDOWS, "Target-process CPU time spent in user mode, normalized to whole-PC capacity"),
    MetricDefinition("app_kernel_cpu", "App Kernel CPU", "%", "cpu", "#8579e8", WINDOWS, "Target-process CPU time spent in kernel mode, normalized to whole-PC capacity"),
    MetricDefinition("total_cpu", "Total CPU Usage", "%", "cpu", "#42c477", IMPLEMENTED, "Overall device CPU utilization"),
    MetricDefinition("system_cpu_user", "CPU 用户态", "%", "system_cpu", "#6fa4ee", WINDOWS, "整机在用户态执行的 CPU 时间占全逻辑核容量的比例。"),
    MetricDefinition("system_cpu_kernel", "CPU 内核态", "%", "system_cpu", "#a683dd", WINDOWS, "整机在内核态执行的 CPU 时间占比，含中断与延迟过程调用（DPC）。"),
    MetricDefinition("cpu_frequency", "CPU Clock", "MHz", "cpu_core_clock", "#f6c445", ANDROID, "Average clock frequency of online CPU cores"),
    MetricDefinition("cpu_online_cores", "CPU Online Cores", "count", "cpu_health", "#5fc98b", ANDROID, "Number of CPU cores currently online"),
    MetricDefinition("cpu_throttling", "CPU Throttling", "%", "cpu_health", "#e56c62", ANDROID, "Average frequency reduction relative to each core maximum"),
    MetricDefinition("memory", "PSS Memory", "MB", "memory", "#77c043", WINDOWS | ANDROID, "Android PSS or Windows process working set"),
    MetricDefinition("ios_physical_footprint", "Physical Footprint", "MB", "memory", "#77c043", IOS, "Physical memory footprint attributed to the iOS target app"),
    MetricDefinition("private_memory", "Private Memory", "MB", "memory", "#36b8a5", WINDOWS, "Memory private to the target process"),
    MetricDefinition("virtual_memory", "Commit Size", "MB", "memory", "#5f8ee4", WINDOWS, "Committed private virtual memory (Windows PagefileUsage)"),
    MetricDefinition("available_memory", "Available Memory", "MB", "memory", "#e0a83d", WINDOWS, "Available system physical memory"),
    MetricDefinition("system_memory_used", "已用物理内存", "MB", "system_memory", "#32a59c", WINDOWS, "整机物理内存总量减去 Windows 报告的可用内存。"),
    MetricDefinition("system_commit_memory", "已提交内存", "MB", "system_memory_commit", "#657de2", WINDOWS, "Windows 当前的系统提交量，包含需由内存或分页文件支持的分配。"),
    MetricDefinition("system_commit_limit", "提交上限", "MB", "system_memory_commit", "#e29647", WINDOWS, "Windows 当前的提交内存上限，可能随分页文件大小变化。"),
    MetricDefinition("java_heap", "Java Heap PSS", "MB", "memory", "#df6cc8", ANDROID, "Android Java heap proportional set size"),
    MetricDefinition("native_heap", "Native Heap PSS", "MB", "memory", "#52b7d8", ANDROID, "Android native heap proportional set size"),
    MetricDefinition("graphics_memory", "Graphics PSS", "MB", "memory", "#ef865d", ANDROID, "Android graphics proportional set size"),
    MetricDefinition("swap_memory", "Swap PSS", "MB", "memory", "#8d82de", ANDROID, "Swap proportional set size of the target app"),
    MetricDefinition("app_rss", "App RSS", "MB", "memory", "#4cb5d5", MOBILE, "Resident physical memory of the target process"),
    MetricDefinition("ios_private_memory", "Private Resident Memory", "MB", "memory", "#36b8a5", IOS, "Private resident memory of the iOS target process"),
    MetricDefinition("ios_compressed_memory", "Compressed Memory", "MB", "memory", "#796ee6", IOS, "Compressed memory attributed to the iOS target process"),
    MetricDefinition("ios_virtual_memory", "Virtual Memory", "MB", "memory", "#5f8ee4", IOS, "Virtual address space of the iOS target process"),
    MetricDefinition("app_uss", "App USS", "MB", "memory", "#d978b7", ANDROID, "Memory private to the target process"),
    MetricDefinition("memory_growth", "Memory Growth", "MB", "memory", "#e4aa48", ANDROID, "PSS growth relative to the beginning of this collection"),
    MetricDefinition("gpu", "GPU Utilization", "%", "gpu", "#b35ee4", MOBILE, "GPU utilization exposed by the device graphics service", "Availability depends on accessible device GPU counters"),
    MetricDefinition("app_gpu_usage", "App GPU Usage", "%", "gpu", "#bd75df", WINDOWS, "Target process GPU-engine utilization reported by Windows performance counters", "Requires Windows GPU Engine performance counters"),
    MetricDefinition("system_gpu_usage", "GPU 利用率", "%", "system_gpu", "#b375de", WINDOWS, "全部可读显卡中最忙引擎的负载，包含整机各进程的活动。", "需要 Windows GPU Engine 计数器"),
    MetricDefinition("system_gpu_3d", "GPU 3D 利用率", "%", "system_gpu", "#6d96ed", WINDOWS, "全部可读显卡中最忙 3D 引擎的负载。", "需要可读的 Windows 3D 引擎计数器"),
    MetricDefinition("ios_gpu_renderer", "GPU Renderer Utilization", "%", "gpu", "#d36ee8", IOS, "iOS graphics renderer utilization"),
    MetricDefinition("ios_gpu_tiler", "GPU Tiler Utilization", "%", "gpu", "#8a72e8", IOS, "iOS graphics tiler utilization"),
    MetricDefinition("ios_gpu_allocated_memory", "GPU Allocated Memory", "MB", "gpu", "#43bfd0", IOS, "System memory allocated by the iOS graphics driver"),
    MetricDefinition("ios_gpu_in_use_memory", "GPU In-use Memory", "MB", "gpu", "#53a9db", IOS, "System graphics memory currently in use"),
    MetricDefinition("gpu_frequency", "GPU Clock", "MHz", "gpu_clock", "#e968a7", ANDROID, "Current GPU clock frequency", "Requires access to device sysfs counters"),
    MetricDefinition("gpu_max_frequency", "GPU Max Clock", "MHz", "gpu_clock", "#f2aa55", ANDROID, "Maximum GPU clock exposed by the device"),
    MetricDefinition("gpu_clock_load", "GPU Clock Load", "%", "gpu", "#776ee8", ANDROID, "Current GPU clock as a percentage of maximum clock"),
    MetricDefinition("gpu_busy_time", "GPU Busy Time", "ms", "gpu_busy", "#43bfd0", ANDROID, "Estimated GPU busy time during the current sampling interval"),
    MetricDefinition("gpu_temperature", "GPU Temperature", "°C", "thermal", "#ef7154", ANDROID, "GPU thermal-zone temperature", "Requires access to GPU thermal nodes"),
    MetricDefinition("app_gpu_memory", "App GPU Memory", "MB", "memory", "#53a9db", ANDROID, "Graphics PSS attributed to the target application"),
    MetricDefinition("gpu_memory", "App Dedicated GPU Memory", "MB", "gpu_memory", "#41b8d5", WINDOWS, "Dedicated GPU memory used by the target process", "Requires Windows GPU Process Memory performance counters"),
    MetricDefinition("shared_gpu_memory", "App Shared GPU Memory", "MB", "gpu_memory", "#53a9db", WINDOWS, "Shared system memory used by the target process for graphics", "Requires Windows GPU Process Memory performance counters"),
    MetricDefinition("system_gpu_dedicated_memory", "专用显存用量", "MB", "system_gpu_memory", "#459ad5", WINDOWS, "各可读显卡报告的专用显存用量之和。", "需要 Windows GPU Adapter Memory 计数器"),
    MetricDefinition("system_gpu_shared_memory", "共享显存用量", "MB", "system_gpu_memory", "#58ad9d", WINDOWS, "各可读显卡占用的共享系统内存之和。", "需要 Windows GPU Adapter Memory 计数器"),
    MetricDefinition("temperature", "Battery Temperature", "°C", "thermal", "#ffb52e", MOBILE, "Device battery temperature"),
    MetricDefinition("cpu_temperature", "CPU Temperature", "°C", "thermal", "#ff684f", ANDROID, "CPU thermal-zone temperature", "Requires access to device thermal nodes"),
    MetricDefinition("power", "Battery Power", "W", "power", "#e65c4f", MOBILE, "Instantaneous battery power estimated from current and voltage"),
    MetricDefinition("battery_level", "Battery Level", "%", "power", "#63c766", MOBILE, "Remaining battery percentage"),
    MetricDefinition("battery_current", "Battery Current", "mA", "power", "#45a7e8", MOBILE, "Instantaneous battery current"),
    MetricDefinition("battery_voltage", "Battery Voltage", "V", "power", "#d2c349", MOBILE, "Instantaneous battery voltage"),
    MetricDefinition("network_send", "Network Send", "KB/s", "network", "#38c598", IMPLEMENTED, "System or device network transmit throughput"),
    MetricDefinition("network_recv", "Network Receive", "KB/s", "network", "#8874e8", IMPLEMENTED, "System or device network receive throughput"),
    MetricDefinition("disk_read", "Disk Read", "KB/s", "io", "#55b9e6", WINDOWS | IOS, "Disk read throughput of the target process"),
    MetricDefinition("disk_write", "Disk Write", "KB/s", "io", "#ee8b55", WINDOWS | IOS, "Disk write throughput of the target process"),
    MetricDefinition("disk_read_iops", "Read IOPS", "ops/s", "io_ops", "#64c5ec", WINDOWS, "Read operations issued by the target process per second"),
    MetricDefinition("disk_write_iops", "Write IOPS", "ops/s", "io_ops", "#f0a16d", WINDOWS, "Write operations issued by the target process per second"),
    MetricDefinition("page_faults", "Page Faults", "faults/s", "memory_pressure", "#ef7154", WINDOWS, "Target-process page-fault rate"),
    MetricDefinition("thread_count", "Thread Count", "count", "process", "#8ccf56", WINDOWS, "Number of threads in the target process"),
    MetricDefinition("handle_count", "Handle Count", "count", "process", "#d66ad2", WINDOWS, "Number of Windows handles opened by the target process"),
    MetricDefinition("app_threads", "App Threads", "count", "process", "#79c657", MOBILE, "Thread count of the target mobile process"),
    MetricDefinition("ios_mach_ports", "Mach Ports", "count", "process", "#d66ad2", IOS, "Mach ports opened by the iOS target process"),
    MetricDefinition("ios_context_switches", "Context Switches", "count/s", "process", "#e0a83d", IOS, "Context-switch rate of the iOS target process"),
    MetricDefinition("ios_interrupt_wakeups", "Interrupt Wakeups", "count/s", "process", "#ef865d", IOS, "Interrupt wakeup rate of the iOS target process"),
) + tuple(
    MetricDefinition(f"cpu_core_{core}_usage", f"CPU {core} Usage", "%", "cpu_core_usage", _CORE_COLORS[core % len(_CORE_COLORS)],
                     IMPLEMENTED, f"Utilization of logical CPU core {core}")
    for core in range(32)
) + tuple(
    MetricDefinition(f"cpu_core_{core}_clock", f"CPU {core} Clock", "MHz", "cpu_core_clock", _CORE_COLORS[(core + 3) % len(_CORE_COLORS)],
                     ANDROID, f"Current clock frequency of Android CPU core {core}", "Requires access to CPU frequency sysfs nodes")
    for core in range(32)
)

METRIC_BY_KEY = {metric.key: metric for metric in METRICS}

# The same counter carries a different vendor name on each platform. Charts and
# reports must show the name the platform itself uses, not a generic one.
_PLATFORM_TITLES: dict[Platform, dict[str, str]] = {
    Platform.WINDOWS: {
        "total_cpu": "整机 CPU 使用率",
        "available_memory": "可用物理内存",
        "memory": "Working Set",
        "private_memory": "Private Bytes",
        "virtual_memory": "Commit Size",
        "network_send": "System Network Send",
        "network_recv": "System Network Receive",
    },
    Platform.ANDROID: {
        "memory": "PSS Memory",
        "network_send": "Device Network Send",
        "network_recv": "Device Network Receive",
        "fps": "App Layer FPS", "frame_time": "Frame Time (Sampled / Fallback)",
        "frame_time_p95": "Frame Time P95 (Sampled)", "frame_time_p99": "Frame Time P99 (Sampled)",
        "jank": "Jank (33.34, 50.01] ms", "big_jank": "Big Jank >50.01 ms",
        "small_jank": "Small Jank (16.67, 33.34] ms", "jank_rate": "Jank Rate (>33.34 ms)",
        "dropped_frames": "Dropped Frames (Budget Estimate)", "app_cpu": "App CPU (Normalized)", "total_cpu": "System CPU",
        "gpu": "Device GPU", "app_gpu_memory": "App Graphics Memory (Fallback)",
        "cpu_throttling": "CPU Frequency Reduction", "cpu_online_cores": "Online CPU Cores",
        "gpu_clock_load": "GPU Clock Ratio", "gpu_busy_time": "GPU Busy Time (Estimated)",
        "memory_growth": "PSS Growth", "app_threads": "App Threads",
        "power": "Battery Power (Estimated)", "battery_current": "Battery Current (Absolute)",
    },
    Platform.IOS: {
        "fps": "Device FPS", "frame_time": "Frame Time (Estimated, 1000/FPS)",
        "app_cpu": "App CPU (Normalized)", "total_cpu": "System CPU",
        "ios_physical_footprint": "Physical Footprint", "app_rss": "App RSS",
        "ios_private_memory": "Private Resident Memory", "ios_compressed_memory": "Compressed Memory",
        "ios_virtual_memory": "Virtual Address Space", "gpu": "Device GPU",
        "ios_gpu_renderer": "Device GPU Renderer", "ios_gpu_tiler": "Device GPU Tiler",
        "ios_gpu_allocated_memory": "Device Graphics Allocated Memory", "ios_gpu_in_use_memory": "Device Graphics In-use Memory",
        "network_send": "System Network Send", "network_recv": "System Network Receive",
        "disk_read": "App Disk Read", "disk_write": "App Disk Write",
        "power": "Battery Power (Estimated)", "temperature": "Battery Temperature",
        "battery_level": "Battery Level", "battery_current": "Battery Current (Absolute)",
        "battery_voltage": "Battery Voltage", "app_threads": "App Threads",
        "ios_mach_ports": "Mach Ports", "ios_context_switches": "App Context Switches",
        "ios_interrupt_wakeups": "App Interrupt Wakeups",
    },
}

ANDROID_METRIC_NOTES = {
    "fps": "SurfaceFlinger 目标包名匹配图层的 totalFrames 增量 / 采样时间，带既有平滑；非所有应用子进程汇总。缺失不以刷新率或 gfxinfo 代替。",
    "frame_time": "优先 gfxinfo FrameCompleted−IntendedVsync 的窗口均值；否则使用 SurfaceFlinger present2present 增量直方图桶近似；仍缺失时仅均值回退 1000/FPS。三者不是同一口径，不能视作完整逐帧耗时。",
    "frame_time_p95": "当前采样窗口内 gfxinfo 帧耗时或 SurfaceFlinger 增量直方图桶分位数；后者为近似。不是整场逐帧 P95，不能再对各窗口 P95 求 P95 来替代。",
    "frame_time_p99": "当前采样窗口内 gfxinfo 帧耗时或 SurfaceFlinger 增量直方图桶分位数；后者为近似。不是整场逐帧 P99。",
    "jank": "窗口内 (33.34,50.01] ms 帧数；固定阈值，可能来自直方图桶近似，不等同 PerfDog Jank 算法。",
    "big_jank": "窗口内 >50.01 ms 帧数；固定阈值，可能来自直方图桶近似，不等同 PerfDog BigJank 算法。",
    "small_jank": "窗口内 (16.67,33.34] ms 帧数；固定阈值，可能来自直方图桶近似，不等同 PerfDog SmallJank 算法。",
    "jank_rate": "窗口内 >33.34 ms 帧数 / 窗口分布帧数；固定阈值，可能为桶近似，不是 PerfDog Stutter。",
    "dropped_frames": "按刷新预算累加 max(0,int(帧时间/预算)-1)；是预算掉帧估算，不是系统实测 presented-but-not-displayed 事件。",
    "app_cpu": "目标主进程 /proc CPU 时间差分，按全机核总容量归一化；不可读时回退 top，不含全部子进程。",
    "total_cpu": "整机 /proc/stat CPU 差分，含系统及其他应用；与应用 CPU 分开展示。",
    "memory": "dumpsys meminfo --local 的目标进程 PSS，异步单飞采样并保留完成时间；与 RSS、USS、GPU 内存不可相加。",
    "java_heap": "目标进程 Java Heap PSS，非 Java 已分配堆大小；不与 PSS 总量相加。",
    "native_heap": "目标进程 Native Heap PSS，非 malloc 全部已分配字节；不与 PSS 总量相加。",
    "graphics_memory": "目标进程 Graphics PSS；不是独立专用显存，可能与应用图形内存回退值重复。",
    "swap_memory": "目标进程 Swap PSS，与物理常驻 PSS 口径不同。",
    "app_rss": "目标 PID /proc/status 的 VmRSS；与 PSS、USS 不同，不能相加。",
    "app_uss": "meminfo Private Dirty + Private Clean，属于目标进程私有驻留页；与 RSS/PSS 不相加。",
    "memory_growth": "PSS 相对本次采集首个有效 PSS 的变化；增长不代表已确认泄漏。",
    "app_gpu_memory": "优先 dumpsys gpu 目标 PID Proc total；不可用时回退 Graphics PSS。两者口径不同，不能默认为专用 GPU 显存。",
    "gpu": "可读时使用 Qualcomm KGSL gpubusy 的 busy/total；设备级而非应用独占。Mali/PVR 深度计数尚未实现，不可读时留空。",
    "gpu_clock_load": "当前频率 / 最大频率 ×100%，不是 GPU 执行利用率。",
    "gpu_busy_time": "设备 GPU 利用率 × 本机采样区间，估算区间忙碌时长；不是逐帧 GPU 耗时。",
    "gpu_frequency": "设备 KGSL/devfreq 可读节点的当前 GPU 频率；缺失留空。",
    "gpu_max_frequency": "设备 KGSL/devfreq 可读节点的最高 GPU 频率。",
    "cpu_frequency": "可读 CPU 核当前频率的平均值，非应用线程时钟。",
    "cpu_online_cores": "设备 sysfs online 核数量，不是应用占用核数量。",
    "cpu_throttling": "各核当前频率相对最大频率的降低比例均值；包含正常 DVFS，不能认定为温控降频。",
    "network_send": "目标 PID 网络命名空间 /proc/PID/net/dev 非回环接口累计字节差分；通常是设备共享网络，非应用独占流量。",
    "network_recv": "目标 PID 网络命名空间 /proc/PID/net/dev 非回环接口累计字节差分；通常是设备共享网络，非应用独占流量。",
    "power": "电池电流绝对值 × 电压估算，非应用功耗，亦非外部仪表功耗；USB 充电会影响解释。",
    "battery_current": "电池 current_now 绝对值，不保留充放电方向，非应用电流。",
    "battery_voltage": "设备电池电压，非应用指标。", "battery_level": "设备电池剩余电量百分比。",
    "temperature": "dumpsys battery 电池温度，不是芯片温度。",
    "cpu_temperature": "可读且名称匹配 CPU 的 thermal zone 温度最大值；厂商传感器含义可能不同。",
    "gpu_temperature": "可读且名称匹配 GPU 的 thermal zone 温度最大值；厂商传感器含义可能不同。",
    "app_threads": "目标主进程线程数量，不提供每线程 CPU、调度或调用栈。",
}

IOS_METRIC_NOTES = {
    "fps": "新采集优先使用 CoreProfile 实际帧间隔计算 FPS（1000×间隔数量/耗时总和），不将每秒整数帧数当成精确帧率；逐帧通道未就绪或不可用时回退 CoreAnimation 计数器，回退值可能仍为整数。经目标活动状态筛选，非 PID 隔离，保留既有尖峰过滤；实际来源见采集诊断。",
    "frame_time": "由平滑后的设备 FPS 换算：1000/FPS；不是实测逐帧耗时，不能据此计算真实 Jank、1% Low 或逐帧 P95/P99。",
    "app_cpu": "目标进程 CPU，按整机逻辑核总容量归一化；100% 表示全核满载。",
    "total_cpu": "整机 CPU，包含系统与其他应用，不是目标应用 CPU。",
    "ios_physical_footprint": "目标进程 physFootprint；与 RSS、私有常驻、压缩内存口径不同，不能相加。",
    "app_rss": "目标进程 memResidentSize；是常驻内存，不等同 Physical Footprint。",
    "ios_private_memory": "目标进程 memRPrvt 私有常驻内存；不等同 Windows Private Bytes，不与 RSS 或 Footprint 相加。",
    "ios_compressed_memory": "目标进程 memCompressed；不能与 Footprint、RSS 简单相加。",
    "ios_virtual_memory": "目标进程 memVirtualSize 虚拟地址空间，不代表实际占用的物理内存。",
    "gpu": "设备 Graphics 图形计数，未绑定目标 PID；不代表游戏独占 GPU 使用率；总 GPU 缺失时不以 Renderer 替代。",
    "ios_gpu_renderer": "设备 Graphics Renderer 利用率，未绑定目标 PID；不是独立于设备 GPU 的额外消耗。",
    "ios_gpu_tiler": "设备 Graphics Tiler 利用率，未绑定目标 PID；不可与 Renderer 相加成总 GPU。",
    "ios_gpu_allocated_memory": "Graphics 的 Alloc system memory：设备图形驱动分配的系统内存，非应用专用显存。",
    "ios_gpu_in_use_memory": "Graphics 的 In use system memory：设备图形驱动使用的系统内存，非应用专用显存；不能与分配量相加。",
    "network_send": "Sysmontap system.netBytesOut 差分，整机网络，非目标应用独占。",
    "network_recv": "Sysmontap system.netBytesIn 差分，整机网络，非目标应用独占。",
    "disk_read": "目标进程 diskBytesRead 累计字节差分；首次只建立基线，缺失不填零。",
    "disk_write": "目标进程 diskBytesWritten 累计字节差分；首次只建立基线，缺失不填零。",
    "ios_context_switches": "目标进程 ctxSwitch 累计值按实际采样间隔差分，单位 次/秒。",
    "ios_interrupt_wakeups": "目标进程 intWakeups 累计值按实际采样间隔差分，单位 次/秒。",
    "power": "AppleSmartBattery 电流绝对值 × 电压估算电池端功率；非目标应用功耗，也不是整机插座功率。",
    "temperature": "AppleSmartBattery 电池温度，不是 CPU 或 GPU 温度。",
    "battery_level": "设备电池电量，非应用指标。",
    "battery_current": "AppleSmartBattery 电流绝对值，不保留充放电方向；非应用电流。",
    "battery_voltage": "AppleSmartBattery 电池电压，非应用指标。",
    "app_threads": "目标进程线程数量，不是线程 CPU 使用率。",
    "ios_mach_ports": "目标进程 Mach 端口数量，不是 Windows 句柄数量。",
}


def platform_metric_note(key: str, platform: Platform) -> str:
    if key in JANK_METRICS:
        source = {Platform.WINDOWS: 'Windows 真实逐帧呈现间隔（PresentMon / DXGI ETW）',
                  Platform.ANDROID: '目标 SurfaceFlinger 图层的实际呈现间隔',
                  Platform.IOS: '设备 CoreAnimation CoreProfile 逐帧事件，经目标活动状态筛选，非 PID 隔离'}[platform]
        return source + '。' + JANK_DESCRIPTION + '界面数值为本次采样窗口内的次数；无有效逐帧数据时保持缺失。'
    if platform is Platform.ANDROID:
        if key.startswith("cpu_core_"):
            return "设备逻辑核利用率或 sysfs 频率，不是游戏线程；不可读时不填零。"
        if key in ANDROID_METRIC_NOTES:
            return ANDROID_METRIC_NOTES[key]
    if platform is Platform.IOS:
        if key.startswith("cpu_core_"):
            return "整机各逻辑核利用率，不是目标游戏线程。"
        if key in IOS_METRIC_NOTES:
            return IOS_METRIC_NOTES[key]
    definition = METRIC_BY_KEY.get(key)
    return definition.description if definition else ""


def platform_metric_title(key: str, platform: Platform) -> str:
    """Metric label as the given platform reports it."""
    if key in {'jank', 'big_jank', 'small_jank'}:
        return {'jank': 'Slow Frames (33.34–50.01 ms)', 'big_jank': 'Slow Frames (>50.01 ms)',
                'small_jank': 'Slow Frames (16.67–33.34 ms)'}[key]
    override = _PLATFORM_TITLES.get(platform, {}).get(key)
    if override:
        return override
    definition = METRIC_BY_KEY.get(key)
    return definition.title if definition else key
