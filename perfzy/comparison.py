"""Descriptive comparisons with saved source/scope and workload context.

Numeric differences are not regression verdicts. Missing historical metadata is
reported as unknown, never filled using a currently running collector.
"""
import math

from .domain.provenance import metric_provenance, canonical_platform
from .report_ui import report_theme, report_theme_assets


_PRIORITY = ('fps', 'fps_1_low', 'fps_0_1_low', 'frame_time', 'app_cpu',
             'memory', 'ios_physical_footprint', 'private_memory')
_WINDOWS_RESOURCES = {'app_cpu', 'app_user_cpu', 'app_kernel_cpu', 'memory',
                     'private_memory', 'virtual_memory', 'disk_read', 'disk_write',
                     'disk_read_iops', 'disk_write_iops', 'thread_count',
                     'handle_count', 'page_faults', 'app_gpu_usage', 'gpu_memory',
                     'shared_gpu_memory'}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _text(*values):
    for value in values:
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            if str(value).strip():
                return str(value).strip()
    return ''


def _finite(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None




def _saved_collector(session):
    return _dict(_dict(session.get('telemetry')).get('collector'))


def _context(session):
    device, target = _dict(session.get('device')), _dict(session.get('target'))
    metadata = _dict(session.get('metadata'))
    conditions = _dict(session.get('test_conditions')) or _dict(metadata.get('test_conditions'))
    capture = _dict(session.get('capture_settings'))
    platform = canonical_platform(device)
    duration = _finite(session.get('duration')) or 0.0
    for points in _dict(session.get('samples')).values():
        for point in points if isinstance(points, list) else ():
            if isinstance(point, dict):
                duration = max(duration, _finite(point.get('x')) or 0.0)
    scenes = [dict(name=row.get('name') or row.get('title') or '',
                   start=row.get('start'), end=row.get('end'),
                   duration=(_finite(row.get('end')) or 0) - (_finite(row.get('start')) or 0))
              for row in (session.get('scenes') or []) if isinstance(row, dict)]
    target_id = (_text(target.get('process_name'), target.get('name')) if platform == 'windows'
                 else _text(target.get('id'), target.get('package'), target.get('bundle_id')))
    return {
        'name': _text(session.get('name'), session.get('id')),
        'session_id': _text(session.get('id')), 'platform': platform,
        'device_id': _text(device.get('id')),
        'device_name': _text(device.get('model'), device.get('name'), device.get('label'), device.get('id')),
        'os_version': _text(device.get('os_version'), device.get('os')),
        'target_id': target_id, 'target_name': _text(target.get('name'), target.get('label'), target_id),
        'app_version': _text(conditions.get('app_version'), capture.get('app_build_label'), capture.get('build_label'),
                             target.get('app_version'), target.get('version_name'), target.get('version'),
                             metadata.get('app_version'), session.get('app_version')),
        'graphics_quality': _text(conditions.get('graphics_quality'), metadata.get('graphics_quality'), session.get('graphics_quality'),
                                  capture.get('graphics_quality')),
        'conditions': _text(capture.get('conditions'), conditions.get('conditions'),
                            session.get('test_conditions') if isinstance(session.get('test_conditions'), str) else ''),
        'capture_settings': dict(capture),
        'range': {'start': 0.0, 'end': duration, 'duration': duration},
        'scenes': scenes,
    }


def _capture_issues(baseline, current, before_context, after_context):
    """Workload warnings used by the saved-record comparison report."""
    issues = []
    a, b = before_context['capture_settings'], after_context['capture_settings']
    if a or b:
        for field, title in (('sample_interval_ms', '采样间隔'), ('screenshots', '截图开关'),
                             ('screenshot_interval_ms', '截图间隔'), ('screenshot_max_width', '截图分辨率'),
                             ('collector_revision', '采集算法版本')):
            av, bv = a.get(field), b.get(field)
            if av is None or bv is None or av == '' or bv == '':
                issues.append(title + '未完整记录，无法确认条件一致。')
            elif av != bv:
                issues.append(title + '不同；差值不能直接归因于软件版本。')
        if 'ios' in (before_context['platform'], after_context['platform']):
            av, bv = a.get('ios_fps_limit'), b.get('ios_fps_limit')
            if (av is None) != (bv is None):
                issues.append('iOS FPS 上限校验规则未完整记录，无法确认条件一致。')
            elif av != bv:
                issues.append('iOS FPS 上限校验规则不同；FPS 差值不能直接归因于软件版本。')
    if a or b or before_context['conditions'] or after_context['conditions']:
        av, bv = before_context['conditions'], after_context['conditions']
        if not av or not bv:
            issues.append('测试条件未完整记录，无法确认条件一致。')
        elif av != bv:
            issues.append('测试条件不同；差值不能直接归因于软件版本。')
    for tag, session in (('基准', baseline), ('本次', current)):
        if session.get('capture_settings_changes'):
            issues.append(f'{tag}采集中途调整过设置，请先核对设置变更记录。')
    if baseline.get('quality') or current.get('quality'):
        for tag, session in (('基准', baseline), ('本次', current)):
            quality = _dict(session.get('quality'))
            if not quality:
                issues.append(f'{tag}记录没有数据完整度信息，无法判断缺失影响。')
            elif any(row.get('valid_percent', 100) < 90 or row.get('suspicious_long', 0)
                     or (row.get('coverage_estimate_percent') is not None and row['coverage_estimate_percent'] < 90)
                     for row in _dict(quality.get('metrics')).values() if isinstance(row, dict)):
                issues.append(f'{tag}记录存在缺失较多的指标或待复核长帧，请结合有效率查看。')
    return issues


def _saved_source(session, key, platform):
    telemetry = _dict(session.get('telemetry'))
    source = {}
    for candidate in (session.get('provenance'), telemetry.get('provenance')):
        item = _dict(_dict(candidate).get(key))
        if item:
            source = item
            break
    reference = metric_provenance(platform, [key]).get(key, {})
    collector = _saved_collector(session)
    process = _dict(collector.get('process_scope'))
    resource_scope = ''
    if platform == 'windows' and key in _WINDOWS_RESOURCES:
        resource_scope = _text(process.get('resource_scope'))
        if resource_scope not in {'selected', 'tree'}:
            old_scope = _text(process.get('gpu_counters') if key in {'app_gpu_usage', 'gpu_memory', 'shared_gpu_memory'}
                              else process.get('cpu_memory_io_threads'))
            resource_scope = 'selected' if old_scope == 'selected PID only' else ''
    coverage = _dict(_dict(process.get('resource_metric_coverage')).get(key))
    return {
        'title': _text(source.get('title'), reference.get('title'), key),
        'unit': _text(source.get('unit'), reference.get('unit')),
        'source': _text(source.get('source')), 'scope': _text(source.get('scope')),
        'method': _text(source.get('method')), 'recorded': bool(source),
        'metric_source': _text(_dict(collector.get('metric_sources')).get(key)),
        'resource_scope': resource_scope,
        'resource_coverage_complete': coverage.get('complete'),
        'resource_discovery_failed': bool(process.get('resource_discovery_failed')),
        'gpu_coverage_state': _text(process.get('gpu_completeness'))
            if key in {'app_gpu_usage', 'gpu_memory', 'shared_gpu_memory'} else '',
    }


def _source_issues(before, after):
    issues = []
    if not before['recorded'] or not after['recorded']:
        issues.append('至少一份记录未保存完整来源说明，无法确认采集口径一致。')
    for field, title in (('source', '采集来源'), ('scope', '指标范围'), ('method', '计算方法'), ('unit', '单位')):
        a, b = before[field], after[field]
        if a and b and a.casefold() != b.casefold():
            issues.append(title + '不同，不宜直接判定退化。')
        elif field != 'unit' and (not a or not b) and before['recorded'] and after['recorded']:
            issues.append(title + '未完整记录，无法确认一致。')
    for field, title in (('metric_source', '实际采集/回退通道'), ('resource_scope', '进程统计范围')):
        if before[field] != after[field] and (before[field] or after[field]):
            issues.append(title + '不同或一侧未知，不宜直接判定退化。')
    if (before['resource_coverage_complete'] is False or after['resource_coverage_complete'] is False
            or before['resource_discovery_failed'] or after['resource_discovery_failed']):
        issues.append('进程资源采集覆盖不完整，不能把缺失计数当成完整资源占用。')
    if before['gpu_coverage_state'] == 'not_proven' or after['gpu_coverage_state'] == 'not_proven':
        issues.append('GPU 进程计数器未证明覆盖完整，数值差异仅供描述性参考。')
    return issues




import base64
import csv
import html
import io
import math
import statistics
from datetime import datetime
from pathlib import Path

from .domain.metrics import METRIC_BY_KEY
from .domain.validation import metric_value_is_plausible


def comparison_model(baseline: dict, candidate: dict) -> dict:
    if not baseline or not candidate:
        raise ValueError('两份会话都必须存在并包含有效数据。')
    warnings = []
    def capture(session):
        device = session.get('device') or {}
        target = session.get('target') or {}
        settings = session.get('capture_settings') or {}
        return dict(device=device, target=target, settings=settings,
                    name=session.get('name') or
                    f"{target.get('name') or '采集记录'} · {session.get('started_at') or '时间未记录'}")
    a, b = capture(baseline), capture(candidate)
    before_context, after_context = _context(baseline), _context(candidate)
    condition_issues = _capture_issues(baseline, candidate, before_context, after_context)
    for field, title in (('platform', '平台'), ('device_id', '设备'), ('target_id', '目标应用'),
                         ('os_version', '系统版本'), ('app_version', '应用版本'), ('graphics_quality', '画质')):
        before, after = before_context[field], after_context[field]
        if not before or not after:
            condition_issues.append(title + '至少一侧未记录，比较条件待确认。')
        elif before != after:
            condition_issues.append(title + '不同，不宜直接判定退化。')
    trust = {'baseline_context': before_context, 'current_context': after_context,
             'condition_status': 'review_required' if condition_issues else 'matched'}
    warnings.extend(condition_issues)
    if a['device'].get('platform') in ('android', 'ios'):
        left_id, right_id = a['target'].get('id'), b['target'].get('id')
        if not left_id or not right_id:
            warnings.append('应用包标识未完整记录，无法仅凭应用名称确认目标一致。')
        elif str(left_id).rsplit(':', 1)[-1] != str(right_id).rsplit(':', 1)[-1]:
            warnings.append('应用包标识不同；请确认比较的是同一款应用。')
    for title, av, bv in (
        ('设备', a['device'].get('id'), b['device'].get('id')),
        ('系统', a['device'].get('os'), b['device'].get('os')),
        ('应用', a['target'].get('name'), b['target'].get('name')),
        ('采样间隔', a['settings'].get('sample_interval_ms'), b['settings'].get('sample_interval_ms')),
        ('截图开关', a['settings'].get('screenshots'), b['settings'].get('screenshots')),
        ('截图间隔', a['settings'].get('screenshot_interval_ms'), b['settings'].get('screenshot_interval_ms')),
        ('截图分辨率', a['settings'].get('screenshot_max_width'), b['settings'].get('screenshot_max_width')),
        ('测试条件', a['settings'].get('conditions'), b['settings'].get('conditions')),
        ('采集算法版本', a['settings'].get('collector_revision'), b['settings'].get('collector_revision')),
    ):
        if av is None or bv is None or av == '' or bv == '':
            warnings.append(f'{title}未完整记录，无法确认条件一致。')
        elif av != bv:
            warnings.append(f'{title}不同；差值不能直接归因于软件版本。')
    for tag, session in (('基准', baseline), ('本次', candidate)):
        if session.get('capture_settings_changes'):
            warnings.append(f'{tag}采集中途调整过设置，请先核对设置变更记录。')
        quality = session.get('quality') or {}
        if not quality:
            warnings.append(f'{tag}记录没有数据完整度信息，无法判断缺失影响。')
        elif any(row.get('valid_percent', 100) < 90 or row.get('suspicious_long', 0)
                 or (row.get('coverage_estimate_percent') is not None and row['coverage_estimate_percent'] < 90)
                 for row in quality.get('metrics', {}).values()):
            warnings.append(f'{tag}记录存在缺失较多的指标或待复核长帧，请结合有效率查看。')

    def scope_stats(session, ranges=None):
        result = {}
        for key, points in (session.get('samples') or {}).items():
            values = []
            for point in points if isinstance(points, list) else ():
                try:
                    stamp, value = float(point['x']), float(point['y'])
                except (KeyError, ValueError, TypeError):
                    continue
                if (not math.isfinite(stamp) or stamp < 0 or not metric_value_is_plausible(key, value)
                        or (ranges is not None and not any(start <= stamp <= end for start, end in ranges))):
                    continue
                values.append(value)
            if values:
                ordered = sorted(values)
                position = (len(ordered) - 1) * .95
                lo = int(position)
                p95 = ordered[lo] + (ordered[min(lo + 1, len(ordered)-1)] - ordered[lo]) * (position-lo)
                result[key] = dict(count=len(values), avg=statistics.fmean(values),
                                   min=min(values), max=max(values), p95=p95)
        return result

    def scope_rows(name, ranges_a=None, ranges_b=None):
        av, bv = scope_stats(baseline, ranges_a), scope_stats(candidate, ranges_b)
        rows = []
        for key in sorted(set(av) | set(bv)):
            left, right = av.get(key), bv.get(key)
            metric = METRIC_BY_KEY.get(key)
            delta = right['avg'] - left['avg'] if left and right else None
            percent = delta / abs(left['avg']) * 100 if delta is not None and left['avg'] != 0 else None
            before_source = _saved_source(baseline, key, trust['baseline_context']['platform'])
            after_source = _saved_source(candidate, key, trust['current_context']['platform'])
            source_issues = _source_issues(before_source, after_source)
            platforms_differ = (trust['baseline_context']['platform'] and trust['current_context']['platform']
                                and trust['baseline_context']['platform'] != trust['current_context']['platform'])
            if before_source['unit'].casefold() != after_source['unit'].casefold() or platforms_differ:
                delta = percent = None
            warnings.extend(f'{key}：{issue}' for issue in source_issues)
            rows.append(dict(key=key, title=metric.title if metric else key,
                             unit=metric.unit if metric else '', baseline=left, candidate=right,
                             delta=delta, percent=percent,
                             baseline_source=before_source, candidate_source=after_source,
                             comparison_issues=source_issues,
                             comparable=trust['condition_status'] == 'matched' and not source_issues))
        return dict(name=name, rows=rows)

    def scenes(session):
        groups = {}
        for row in session.get('scenes') or []:
            try:
                start, end = float(row['start']), float(row['end'])
                name = str(row.get('name') or row.get('title') or '').strip()
                if name and math.isfinite(start) and math.isfinite(end) and end > start >= 0:
                    groups.setdefault(name, []).append((start, end))
            except (ValueError, TypeError, KeyError):
                continue
        return groups
    sa, sb = scenes(baseline), scenes(candidate)
    shared = sorted(set(sa) & set(sb))
    scopes = [scope_rows('整次采集（请确认流程一致）')]
    scopes.extend(scope_rows(name, sa[name], sb[name]) for name in shared)
    if not shared:
        warnings.append('没有同名场景；当前只能比较整次采集。建议在两份记录中标记同名场景。')
    return dict(baseline=a, candidate=b, warnings=list(dict.fromkeys(warnings)), scopes=scopes,
                baseline_context=trust['baseline_context'], candidate_context=trust['current_context'],
                methodology='逐采样读数的算术平均，非逐帧统计或时间加权均值；P95 是该指标采样读数的分位数。差值 = 本次 − 基准；基准为零时变化比例不计算。同名场景按时间集合取样，重叠点只算一次。')


def generate_comparison_report(baseline: dict, candidate: dict, destination: Path, *, theme: str | None = None) -> Path:
    model = comparison_model(baseline, candidate)
    esc = lambda value: html.escape(str(value))
    fmt = lambda value: '—' if value is None else f'{value:,.2f}'
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    def csv_text(value):
        text = str(value)
        return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
    writer.writerow(['场景', '指标', '单位', '基准均值', '本次均值', '差值', '变化百分比', '基准样本', '本次样本'])
    tables = []
    for scope in model['scopes']:
        rows = []
        for row in scope['rows']:
            a, b = row['baseline'] or {}, row['candidate'] or {}
            writer.writerow([csv_text(scope['name']), csv_text(row['title']), csv_text(row['unit']), a.get('avg'), b.get('avg'),
                             row['delta'], row['percent'], a.get('count', 0), b.get('count', 0)])
            rows.append(f'<tr><td>{esc(row["title"])}<small>{esc(row["unit"])}</small></td>'
                        f'<td>{fmt(a.get("avg"))}</td><td>{fmt(b.get("avg"))}</td>'
                        f'<td>{fmt(row["delta"])}</td><td>{fmt(row["percent"]) + chr(37) if row["percent"] is not None else chr(8212)}</td>'
                        f'<td>{a.get("count", 0)} / {b.get("count", 0)}</td>'
                        f'<td>{fmt(a.get("p95"))} / {fmt(b.get("p95"))}</td>'
                        f'<td>{fmt(a.get("max"))} / {fmt(b.get("max"))}</td></tr>')
        tables.append(f'<section><h2>{esc(scope["name"])}</h2><div class="scroll"><table><thead><tr>'
                      '<th>指标</th><th>基准均值</th><th>本次均值</th><th>差值</th><th>变化</th>'
                      '<th>样本数 A / B</th><th>P95 A / B</th><th>最大值 A / B</th></tr></thead><tbody>'
                      + ''.join(rows) + '</tbody></table></div></section>')
    cards = []
    for key, label, session in (('baseline', 'A · 基准', baseline), ('candidate', 'B · 本次', candidate)):
        data = model[key]
        settings = data['settings']
        quality_rows = (session.get('quality') or {}).get('metrics', {})
        valid = '；'.join(f'{name}: {row.get("valid_percent", 0):.1f}%' for name, row in quality_rows.items()) or '未记录'
        cards.append(f'<article><small>{label}</small><h2>{esc(data["name"])}</h2>'
                     f'<p>版本：{esc(settings.get("build_label") or "未记录")}</p>'
                     f'<p>设备：{esc(data["device"].get("name") or "未记录")} · {esc(data["device"].get("os") or "未记录")}</p>'
                     f'<p>测试条件：{esc(settings.get("conditions") or "未记录")}</p>'
                     f'<details class="comparison-quality"><summary>数据完整度<span>查看 {len(quality_rows)} 项指标</span></summary>'
                     f'<p>有效读数占比：{esc(valid)}</p></details></article>')
    encoded = base64.b64encode(output.getvalue().encode('utf-8-sig')).decode('ascii')
    warning = ''.join(f'<li>{esc(text)}</li>' for text in model['warnings']) or '<li>已记录条件一致；仍需人工确认路线、温度和后台负载。</li>'
    theme = report_theme(destination.parent / 'data' / 'preferences.ini', theme)
    theme_css, theme_script = report_theme_assets()
    content = ('<!doctype html><html lang="zh-CN" data-theme="' + theme + '"><head>'
               '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
               '<meta name="color-scheme" content="light dark"><title>PerfZY · 两次测试对比</title>'
               '<style id="report-shared-theme">' + theme_css + '</style>'
               '<script id="report-shared-appearance">' + theme_script + '</script></head>'
               '<body class="report-comparison"><header class="report-top"><div class="report-brand">PerfZY</div>'
               '<div class="report-kind">记录对比</div><div class="report-actions">'
               '<button type="button" data-report-theme-toggle aria-label="切换主题"></button>'
               '<button onclick="window.print()">打印 / PDF</button></div></header><main>')
    content += '<div class="report-heading"><h1>两次测试对比</h1><p>查看基准与本次采集的读数变化，结合相同测试条件进行比较。</p></div>'
    content += '<div class="cards">' + ''.join(cards) + '</div><section class="notice"><h2>可比性检查</h2><ul>' + warning + '</ul></section>'
    content += f'<a class="download-button" download="PerfZY-comparison.csv" href="data:text/csv;base64,{encoded}">导出对比 CSV</a>'
    content += ''.join(tables) + f'<section><h2>统计口径</h2><p>{esc(model["methodology"])}</p></section><div class="report-foot">PerfZY · 本地采集记录对比</div></main></body></html>'
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f'PerfZY-对比-{datetime.now():%Y%m%d-%H%M%S-%f}.html'
    temporary = path.with_suffix('.html.tmp')
    try:
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
