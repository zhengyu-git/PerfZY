"""UI availability from completed samples, never from extra device queries.

Missing counters remain requested so a recovering source can become available.
"""
from ..collectors.windows import FRAME_METRICS, GPU_PROCESS_METRICS


def update_windows_status(previous, misses, requested, points, diagnostics):
    result = dict(previous)
    unsupported = set(diagnostics.get("unavailable_frame_metrics") or ())
    for key in set(requested) | unsupported:
        if key in unsupported:
            result[key] = {"state": "unavailable", "label": "不可用", "support": "unsupported",
                           "reason": "本轮已回退到应用 Present 兼容模式，不提供 GPU 逐帧/显示丢帧数据；重新采集后重新检测。"}
        elif key in points:
            misses[key] = 0
            result[key] = {"state": "available", "label": "可用", "reason": "本轮已收到有效数据；零值也是有效数据。"}
            if key in FRAME_METRICS:
                quality = diagnostics.get('integrity') or {}
                if quality.get('queue_overflow_rows') or quality.get('etw_loss_reported'):
                    result[key].update(label='可用·有丢失', reason='已接收数据，但本次记录已检测到队列溢出或 ETW 丢失；不能视作完整帧记录。')
        else:
            misses[key] = misses.get(key, 0) + 1
            if misses[key] < 5:
                result[key] = {"state": "checking", "label": "检测中", "reason": "等待有效样本，不使用零值代替缺失。"}
            else:
                reason = ("未收到有效帧事件，请确认目标持续渲染；采集器仍会继续检测。" if key in FRAME_METRICS else
                          "GPU 性能计数器未返回数据，可能与驱动、权限或目标 GPU 活动有关；仍会继续检测。" if key in GPU_PROCESS_METRICS else
                          "本指标连续至少 5 次采样未返回有效值；仍会继续检测。")
                result[key] = {"state": "unavailable", "label": "暂不可用", "reason": reason}
    return result


def update_ios_status(previous, misses, requested, points, diagnostics):
    from ..collectors.ios_sampling import BATTERY_METRICS, GRAPHICS_METRICS
    result = dict(previous)
    unsupported = diagnostics.get('unsupported_metrics') or {}
    interval = max(500, diagnostics.get('sample_interval_ms', 1000))
    for key in set(requested) | set(unsupported):
        if key in points:
            misses[key] = 0
            result[key] = {'state': 'available', 'label': '可用', 'reason': '本轮已收到有效数据；零值也是有效数据。'}
        elif key in unsupported:
            result[key] = {'state': 'unavailable', 'label': '本轮未支持', 'support': 'unsupported', 'reason': unsupported[key] + '；重新订阅或采集后重新检测。'}
        else:
            misses[key] = misses.get(key, 0) + 1
            limit = max(5, int(15000 / interval) + 1) if key in BATTERY_METRICS else 5
            if misses[key] < limit:
                result[key] = {'state': 'checking', 'label': '检测中', 'reason': '等待有效样本或累计计数基线，不使用零值代替缺失。'}
            else:
                reason = ('设备 Graphics 暂未返回可用数据，或目标不在活动状态；仍会继续检测。' if key in GRAPHICS_METRICS else
                          '电池诊断暂未返回有效数据；仍会继续检测。' if key in BATTERY_METRICS else
                          '本指标连续至少 5 次未返回有效值，可能与订阅、系统版本或目标状态有关；仍会继续检测。')
                result[key] = {'state': 'unavailable', 'label': '暂不可用', 'reason': reason}
    return result


ANDROID_SOURCE_LABELS = {
    "surfaceflinger_counter": ("可用·图层计数", "SurfaceFlinger 目标图层计数差分，带既有平滑。"),
    "gfxinfo_framestats": ("可用·gfxinfo", "本轮为 gfxinfo 帧时间窗口统计，非全部渲染链路记录。"),
    "surface_histogram": ("可用·桶近似", "本轮回退 SurfaceFlinger 增量直方图桶，统计为近似值，不是原始逐帧记录。"),
    "fps_estimate": ("可用·FPS估算", "本轮均值仅由 1000/FPS 估算，不支持逐帧长帧或真实分位数判断。"),
    "dumpsys_gpu_pid": ("可用·GPU进程计数", "本轮使用 dumpsys gpu 的目标 PID Proc total。"),
    "graphics_pss_fallback": ("可用·PSS回退", "本轮回退 Graphics PSS，不是专用 GPU 显存，不能与 Graphics PSS 相加。"),
}


def update_android_status(previous, misses, requested, points, diagnostics):
    """Only completed, already-selected reads; no ADB queries or permanent gates."""
    from ..collectors.android import FRAME_METRICS as ANDROID_FRAME_METRICS
    result = dict(previous)
    interval = max(500, diagnostics.get('sample_interval_ms', 1000))
    slow = {'memory', 'java_heap', 'native_heap', 'graphics_memory', 'app_uss',
            'swap_memory', 'memory_growth', 'app_gpu_memory'}
    for key in set(requested):
        if key in points:
            misses[key] = 0
            label, reason = ANDROID_SOURCE_LABELS.get((diagnostics.get('metric_sources') or {}).get(key),
                ('可用', '本轮已收到有效数据；零值也是有效数据。'))
            if key == 'dropped_frames':
                label = '可用·预算估算'
                reason += ' 这是刷新预算掉帧估算，不是系统实测显示丢帧。'
            result[key] = {'state': 'available', 'label': label, 'reason': reason}
        else:
            misses[key] = misses.get(key, 0) + 1
            limit = max(5, int(15000 / interval) + 1) if key in slow else 5
            if misses[key] < limit:
                result[key] = {'state': 'checking', 'label': '检测中', 'reason': '等待有效样本或计数基线，缺失不填零。'}
            else:
                reason = ('帧源未返回有效值；请确认目标持续渲染，图层计数或 gfxinfo 在该设备上可读取。' if key in ANDROID_FRAME_METRICS else
                          '设备 GPU 节点未返回有效值，可能是芯片类型或读取权限限制；不以频率比例代替 GPU 利用率。' if key in {'gpu','gpu_busy_time'} else
                          '所选指标未返回有效值，可能受系统版本、权限或采集通道限制。')
                result[key] = {'state': 'unavailable', 'label': '暂不可用', 'reason': reason + ' 仍会继续检测，恢复后自动更新。'}
    return result
