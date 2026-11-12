"""Conservative comparisons of recorded values, never inferred missing data."""
import math
from statistics import median
import re

from ..domain.validation import metric_value_is_plausible
from ..domain.provenance import canonical_platform
from .storage import number

MEMORY_KEYS = ('memory', 'ios_physical_footprint', 'private_memory', 'app_rss',
               'java_heap', 'native_heap', 'graphics_memory', 'gpu_memory', 'shared_gpu_memory')


def points(session, metric, start=0., end=math.inf):
    result = []
    for row in (session.get('samples') or {}).get(metric, []):
        if not isinstance(row, dict):
            continue
        x, y = number(row.get('x')), number(row.get('y'))
        if x is not None and y is not None and start <= x <= end and metric_value_is_plausible(metric, y):
            result.append(dict(x=x, y=y))
    return sorted(result, key=lambda row: row['x'])


def memory_analysis(session, enter, exit_time, window=10.):
    enter, exit_time, window = float(enter), float(exit_time), float(window)
    if not all(math.isfinite(v) for v in (enter, exit_time, window)) or not 0 <= enter < exit_time or window <= 0:
        raise ValueError('请设置有效的进入/退出时间与观察窗口')
    rows = []
    for key in MEMORY_KEYS:
        before = points(session, key, max(0, enter-window), enter)
        during = points(session, key, enter, exit_time)
        after = points(session, key, exit_time, exit_time+window)
        # Boundary points belong to only one phase.
        before = [p for p in before if p['x'] < enter]
        during = [p for p in during if p['x'] < exit_time]
        if not before and not during and not after:
            continue
        means = [sum(p['y'] for p in group)/len(group) if group else None for group in (before, during, after)]
        slope = None
        if len(during) >= 3 and during[-1]['x'] > during[0]['x']:
            center = sum(p['x'] for p in during)/len(during)
            denom = sum((p['x']-center)**2 for p in during)
            slope = (sum((p['x']-center)*(p['y']-means[1]) for p in during)/denom*60) if denom else None
        recovery = means[2]-means[0] if means[0] is not None and means[2] is not None else None
        intervals=[group[i]['x']-group[i-1]['x'] for group in (before,during,after)
                   for i in range(1,len(group)) if group[i]['x']>group[i-1]['x']]
        cadence=median(intervals) if intervals else None
        gaps=bool(cadence and any(delta>cadence*3 for delta in intervals))
        enough = min(len(before), len(during), len(after)) >= 3 and not gaps
        verdict = '数据不足，无法判断'
        if enough:
            tolerance = max(5., abs(means[0])*.02)
            verdict = ('退出后已回落至基线附近' if recovery <= tolerance else
                       '退出后仍高于基线；可能是缓存或持续占用，不等同内存泄漏')
            if slope is not None and slope > 1 and during[-1]['y']-during[0]['y'] > tolerance:
                verdict = '疑似持续增长；' + verdict
        rows.append(dict(metric=key, before=means[0], during=means[1], after=means[2],
            counts=[len(before), len(during), len(after)], peak=max((p['y'] for p in during), default=None),
            growth_mb_min=slope, residual_mb=recovery, verdict=verdict,large_gaps=gaps))
    return dict(rows=rows, enter=enter, exit=exit_time, window=window,
        notes=['统计基于有效采样点，不插值；各内存口径可能重叠，不能相加。',
               'Java/Native/Graphics 仅在原始采集中存在时展示；Windows 不伪造安卓内存分类。'])


def identity(session):
    device, target = session.get('device') or {}, session.get('target') or {}
    platform = canonical_platform(device)
    # Windows numeric PIDs are not stable application identities.
    app = (target.get('id') if platform != 'windows' else target.get('name')) or ''
    app = re.sub(r'\s*\[PID \d+\]\s*$', '', str(app))
    return platform, str(device.get('id') or ''), app




def overlay(sessions, metric, scene_name='', start=0., end=math.inf):
    curves, notes = [], []
    reference = identity(sessions[0]) if sessions else None
    if reference is not None and not all(reference):
        return dict(curves=[],metric=metric,scene=scene_name,notes=['缺少平台、设备或应用身份，未做数值叠加。'])
    for session in sessions:
        if identity(session) != reference:
            notes.append(f"跳过不同平台/设备/应用的记录：{session.get('name','')}")
            continue
        origin, finish = 0., float(session.get('duration') or 0)
        if scene_name:
            scenes = [s for s in session.get('scenes', []) if s.get('name') == scene_name]
            if len(scenes) != 1:
                notes.append(f"跳过缺失或重名场景：{session.get('name','')}")
                continue
            origin, finish = float(scenes[0]['start']), float(scenes[0]['end'])
        raw = [dict(x=p['x']-origin, y=p['y']) for p in points(session, metric, origin, finish)
               if start <= p['x']-origin <= end]
        intervals = [raw[i]['x']-raw[i-1]['x'] for i in range(1,len(raw)) if raw[i]['x'] > raw[i-1]['x']]
        gap_threshold=median(intervals)*3 if intervals else 0
        segment=0
        for index,point in enumerate(raw):
            if index and gap_threshold and point['x']-raw[index-1]['x']>gap_threshold:segment+=1
            point['segment']=segment
        # Min/max per bucket retains narrow spikes. No interpolated values.
        reduced = raw
        if len(raw) > 2000:
            size = math.ceil(len(raw)/900)
            reduced = []
            for i in range(0, len(raw), size):
                bucket = raw[i:i+size]
                indices = sorted({0, min(range(len(bucket)), key=lambda j: bucket[j]['y']),
                                  max(range(len(bucket)), key=lambda j: bucket[j]['y']), len(bucket)-1})
                reduced.extend(bucket[j] for j in indices)
        curves.append(dict(id=session.get('id'), name=session.get('name'), metric=metric,
            origin=origin, points=reduced, original_count=len(raw), reduced=len(raw)>len(reduced),
            gap_threshold=gap_threshold))
    return dict(curves=curves, metric=metric, scene=scene_name, notes=notes +
                ['横轴为各记录/同名场景起点后的秒数；不拉伸时间、不跨缺口插值。'])
