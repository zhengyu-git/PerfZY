"""Explicit metric scope: observations, derived windows and estimates."""
from __future__ import annotations

from .metrics import METRIC_BY_KEY, platform_metric_title, platform_metric_note
from .models import Platform
from .jank import JANK_METRICS, JANK_CONTRACT, JANK_DESCRIPTION


def canonical_platform(device) -> str:
    aliases = {'安卓设备': 'android', 'iOS 设备': 'ios', 'Windows 本机': 'windows'}
    raw = device.get('platform', '')
    value = aliases.get(raw, raw)
    if value in {'android', 'ios', 'windows'}:
        return str(value)
    prefix = str(device.get('id', '')).partition(':')[0]
    return prefix if prefix in {'android', 'ios', 'windows'} else ''


def metric_provenance(platform: str | Platform, keys, process_scope=None, collector_details=None) -> dict:
    platform = canonical_platform({'platform': platform})
    try:
        platform = Platform(platform)
    except ValueError:
        return {}
    result = {}
    windows_tree = (process_scope or {}).get('resource_scope') == 'tree'
    windows_resource_scope = ('所选目标 PID 及经身份与父子链验证的子进程' if windows_tree
                              else '所选目标 PID（不自动合计子进程）')
    for key in sorted(set(keys)):
        definition = METRIC_BY_KEY.get(key)
        if not definition:
            continue
        source, scope, method, note = '平台采集器', '依指标而定', '实测或窗口派生', definition.description
        if key in {'network_send', 'network_recv'}:
            source = {'android': '/proc/<pid>/net/dev', 'windows': '系统网络计数器',
                      'ios': 'Instruments Sysmontap system.netBytesOut/netBytesIn'}[platform.value]
            scope = '设备/系统（非目标应用独占）'
            method = '累计字节按实际采样间隔差分'
            note = 'Android 进程网络命名空间通常与设备共享；不能把该值归因为目标应用流量。' if platform is Platform.ANDROID else note
        elif key == 'fps':
            source = {'android': 'SurfaceFlinger TimeStats', 'windows': 'PresentMon',
                      'ios': 'Instruments CoreAnimation'}[platform.value]
            scope = '目标进程/图层' if platform is not Platform.IOS else '设备级计数，经目标活动状态筛选'
            method = '窗口计数派生，带既有尖峰过滤'
            note = 'iOS 并非 PID 隔离的逐帧计数。' if platform is Platform.IOS else '有效零帧与读取缺失分开；该指标带平滑，不是逐帧原始记录。'
            if platform is Platform.WINDOWS:
                scope = '目标进程/相关渲染流，实际 PID 与选择规则见采集诊断'
                method = '1000 / 窗口内应用 FrameTime 毫秒均值，带既有尖峰过滤'
                note = '应用呈现 FPS，不是屏幕实际显示 FPS；无有效帧时保持缺失。完整显示链路无数据时可回退呈现模式。'
        elif platform is Platform.WINDOWS and key in {'dropped_frames','dropped_frame_rate'}:
            source, scope, method = 'PresentMon 显示跟踪', '目标 PID 及其子进程', '已知显示状态的未显示帧计数/占比'
            note = '仅应用呈现模式没有显示丢帧结果；缺失不是零，也不能认定没有丢帧。'
        elif key in {'frame_time', 'frame_time_p95', 'frame_time_p99', 'jank', 'big_jank', 'small_jank', 'jank_rate', 'dropped_frames'}:
            source = {'android': 'gfxinfo 完整帧 / SurfaceFlinger 直方图',
                      'windows': 'PresentMon 帧数据', 'ios': 'CoreAnimation'}[platform.value]
            scope = '目标进程/图层' if platform is not Platform.IOS else '设备级计数，经目标活动状态筛选'
            method = '采样窗口派生'
            note = 'Android 直方图是桶值近似；缺少分布时平均帧间隔可由 1000/FPS 估计。Android 掉帧为刷新预算推算，不等同 Windows 未显示帧事件。' if platform is Platform.ANDROID else '报告再次计算的分位数表示窗口采样值分布，不是原始逐帧分布。'
        elif key in {'memory', 'memory_growth', 'java_heap', 'native_heap', 'graphics_memory', 'swap_memory', 'app_uss'}:
            source = 'dumpsys meminfo --local' if platform is Platform.ANDROID else '操作系统进程内存计数器'
            scope, method = '目标进程', '实测快照' if key != 'memory_growth' else '相对起始 PSS 差值'
            note = 'PSS 与 RSS 不互相替代；异步 PSS 使用自身采集完成时间。' if platform is Platform.ANDROID else definition.description
        elif key in {'power', 'gpu_busy_time', 'gpu_clock_load', 'cpu_throttling'}:
            source, method = '平台计数器组合', '估算/比率'
            scope = '设备/硬件'
            note = definition.description
        elif key in {'app_cpu', 'total_cpu'}:
            scope = '设备整体' if key == 'total_cpu' else '目标进程'
            source = {'android': '/proc 差分，失败回退 top', 'windows': '操作系统 CPU 时间',
                      'ios': 'Instruments'}[platform.value]
            method = '按整台设备逻辑核总容量归一化，100% 为全核满载'
        if platform is Platform.ANDROID:
            note = platform_metric_note(key, platform)
            if key == 'gpu':
                source, scope, method = 'Qualcomm KGSL gpubusy（需要节点可读）', '设备 GPU（非应用独占）', 'busy / total'
            elif key == 'app_gpu_memory':
                source, scope, method = 'dumpsys gpu PID Proc total / Graphics PSS 回退', '目标主进程', '不同口径快照，可回退；实际来源见采集诊断'
            elif key in {'app_rss', 'app_threads'}:
                source, scope, method = '/proc/<pid>/status', '目标主进程', '进程快照'
            elif key in {'temperature', 'power', 'battery_level', 'battery_current', 'battery_voltage'}:
                source, scope = 'dumpsys battery / battery sysfs', '设备电池（非应用独占）'
            elif key in {'gpu_frequency', 'gpu_max_frequency', 'gpu_temperature', 'cpu_temperature', 'cpu_frequency', 'cpu_online_cores'} or key.startswith('cpu_core_'):
                source, scope = '设备 procfs / sysfs（需要节点可读）', '设备硬件（非应用独占）'
            if key == 'dropped_frames':
                method = '刷新预算推算，非显示丢帧实测'
        if platform is Platform.WINDOWS and key in {'system_cpu_user', 'system_cpu_kernel'}:
            source, scope, method = 'Windows 逻辑核 CPU 时间', '整机', '各逻辑核时间差分，以总容量归一化'
        if platform is Platform.WINDOWS and key in {'system_memory_used', 'available_memory'}:
            source, scope, method = 'Windows 物理内存状态', '整机', '物理内存快照'
        if platform is Platform.WINDOWS and key in {'system_commit_memory', 'system_commit_limit'}:
            source, scope, method = 'Windows PDH Memory', '整机', '系统提交量 / 提交上限快照'
        if platform is Platform.WINDOWS and key in {'system_gpu_usage', 'system_gpu_3d'}:
            source, scope = 'Windows PDH GPU Engine', '整机可读显卡及进程'
            method = '同一物理引擎合计进程使用率，再取最忙的引擎'
        if platform is Platform.WINDOWS and key in {'system_gpu_dedicated_memory', 'system_gpu_shared_memory'}:
            source, scope, method = 'Windows PDH GPU Adapter Memory', '整机可读显卡', '适配器用量快照求和'
        if platform is Platform.WINDOWS and key in {'app_gpu_usage','gpu_memory','shared_gpu_memory'}:
            source, scope, method = 'Windows PDH GPU 进程计数器', windows_resource_scope, '进程 GPU Engine / Process Memory 计数，非逐帧 GPU 时序'
            note = '仅汇总该范围内已返回的 PDH 实例；无实例不等于零，不保证全部进程 GPU 计数完整。'
        if platform is Platform.WINDOWS and key in {'app_cpu','app_user_cpu','app_kernel_cpu','memory','private_memory','virtual_memory','disk_read','disk_write','disk_read_iops','disk_write_iops','thread_count','handle_count','page_faults'}:
            scope = windows_resource_scope
            if windows_tree:
                note += ' 逐进程读取后求和；任一已发现进程缺失时聚合指标保持缺失；新进程先建立增量基线。动态快照无法证明捕获全部短命子进程。'
                if key == 'memory':
                    note += ' 工作集为 RSS 相加，共享页可能重复计数，不是唯一物理内存。'
        if platform is Platform.IOS:
            note = platform_metric_note(key, platform)
            if key == 'frame_time':
                method = '估算：1000 / 平滑后的 FPS；非逐帧观测'
            elif key in {'gpu', 'ios_gpu_renderer', 'ios_gpu_tiler', 'ios_gpu_allocated_memory', 'ios_gpu_in_use_memory'}:
                source, scope, method = 'Instruments Graphics（未绑定 PID）', '设备图形系统（非应用独占）', '设备计数器快照'
            elif key in {'ios_physical_footprint','app_rss','ios_private_memory','ios_compressed_memory','ios_virtual_memory','app_threads','ios_mach_ports'}:
                source, scope, method = 'Instruments Sysmontap', '目标进程', '进程计数器快照；各内存口径不可相加'
            elif key in {'ios_context_switches','ios_interrupt_wakeups','disk_read','disk_write'}:
                source, scope, method = 'Instruments Sysmontap', '目标进程', '累计值按实际采样间隔差分；首次仅建立基线'
            elif key in {'power','temperature','battery_level','battery_current','battery_voltage'}:
                source, scope = 'AppleSmartBattery', '设备电池（非应用独占）'
                method = '电流绝对值 × 电压估算' if key == 'power' else '电池诊断快照'
            elif key.startswith('cpu_core_'):
                source, scope, method = 'Instruments Sysmontap PerCPUUsage', '整机逻辑核', '各逻辑核使用率'
        if key in JANK_METRICS:
            source = {'windows': 'PresentMon ordered frames', 'android': 'SurfaceFlinger --latency actual presents',
                      'ios': 'CoreProfileSessionTap CoreAnimation event 0x31800318'}[platform.value]
            scope = ('设备 CoreAnimation，经目标活动状态筛选，非 PID 隔离' if platform is Platform.IOS
                     else '目标 SurfaceFlinger 图层' if platform is Platform.ANDROID else '选中目标的主呈现流')
            method, note = JANK_CONTRACT, JANK_DESCRIPTION
        if platform is Platform.IOS and (collector_details or {}).get('fps_method') == 'ios-coreprofile-interval-fps-v1':
            if key == 'fps':
                source = 'CoreProfile 实际帧间隔 / CoreAnimation 计数器回退'
                method = 'ios-coreprofile-interval-fps-v1：1000 × 间隔数量 / 实际间隔总和；带既有尖峰过滤'
                note = platform_metric_note(key, platform)
            elif key == 'frame_time':
                source = '采用的 FPS：优先 CoreProfile，回退 CoreAnimation 计数器'
                method = '估算：1000 / 采用的 FPS；非逐帧曲线'
        if platform is Platform.WINDOWS and (collector_details or {}).get('mode') == 'dxgi_present':
            if key in {'fps', 'fps_1_low', 'fps_0_1_low', 'frame_time', 'frame_time_p95',
                       'frame_time_p99', 'jank', 'big_jank', 'small_jank', 'jank_rate'} | JANK_METRICS:
                source = 'DXGI ETW 兼容采集（成功 Present 调用）'
                note += ' 兼容通道按成功 Present 的起始时间间隔采集；不识别生成/重复帧类型，不是屏幕显示帧率，不提供逐帧 GPU 或显示丢帧结果。'
        result[key] = {'title': platform_metric_title(key, platform), 'unit': definition.unit,
                       'source': source, 'scope': scope, 'method': method, 'note': note}
    return result
