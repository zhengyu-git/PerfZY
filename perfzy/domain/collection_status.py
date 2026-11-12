"""Reader-facing collector health from accepted observations only; no I/O."""
import math


GROUPS = (
    ('fps', 'FPS', ('fps',)),
    ('cpu', 'CPU', ('app_cpu', 'total_cpu', 'app_user_cpu', 'app_kernel_cpu')),
    ('memory', 'Memory', ('ios_physical_footprint', 'memory', 'app_rss', 'private_memory',
                     'ios_private_memory', 'virtual_memory', 'java_heap', 'native_heap',
                     'graphics_memory', 'app_uss', 'ios_compressed_memory')),
    ('gpu', 'GPU', ('app_gpu_usage', 'gpu', 'ios_gpu_renderer', 'ios_gpu_tiler',
                   'gpu_memory', 'shared_gpu_memory', 'gpu_busy_time')),
)
SLOW_METRICS = frozenset(GROUPS[2][2])


def _finite(value, fallback):
    try:
        number = float(value)
        return number if math.isfinite(number) else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def collection_metric_status(requested, samples, statuses, fields, elapsed=0.0,
                             interval=1.0, collecting=False, paused=False, starting=False):
    """Preserve last-observation age, including when an iOS cache is held."""
    requested = set(requested)
    now = max(0.0, _finite(elapsed, 0.0))
    rows = []
    for group, title, keys in GROUPS:
        key = next((key for key in keys if key in requested), None)
        if key is None:
            rows.append(dict(group=group, title=title, key='', state='disabled',
                             label='未启用', age_s=None, age_text='未启用',
                             reason='在指标选择中启用本类指标后才会检测。'))
            continue
        state = dict(statuses.get(key) or {})
        stamp = (fields.get(key) or {}).get('last_elapsed')
        if stamp is None:
            points = samples.get(key) or []
            stamp = points[-1].get('x') if points else None
        if not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            stamp = None
        age = max(0.0, now-stamp) if stamp is not None else None
        grace = max(15.0 if key in SLOW_METRICS else 5.0, _finite(interval, 1.0)*3)
        reason = str(state.get('reason') or '')
        if state.get('support') == 'unsupported':
            label, status = '不支持', 'unsupported'
        elif not collecting and not starting:
            status, label = ('recorded', '已记录') if stamp is not None else ('missing', '未记录')
            reason = reason or '这是已保存/已停止记录的状态，不会重新查询设备。'
        elif paused:
            status, label = 'paused', '已暂停'
            reason = '采集已暂停；最后有效观测时间保持不变。'
        elif stamp is None:
            status, label = ('waiting', '等待数据') if starting or now < grace else ('missing', '未收到')
            reason = reason or '尚未收到首个有效观测。请确认目标应用正在运行和渲染、设备连接及采集权限正常。'
        elif age > grace:
            status, label = 'stale', '数据中断'
            reason = '最后一次有效观测已超时；请检查设备连接、目标进程和采集通道。' + (f'\n采集器说明：{reason}' if reason else '')
        else:
            status, label = 'available', '正常'
            if state.get('label') == '可用·有丢失':
                label = '正常·有丢失'
            reason = reason or '已接收有效观测；实测 0 也属于有效数据。'
        age_text = ('尚无有效观测' if age is None else
                    f'最后有效 {age:.1f} 秒前' if collecting or starting else
                    f'最后观测 {stamp:.2f} 秒')
        rows.append(dict(group=group, title=title, key=key, state=status, label=label,
                         age_s=age, age_text=age_text, last_elapsed=stamp, reason=reason))
    return rows
