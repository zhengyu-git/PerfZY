"""Bounded, offline evidence/comparison packages with an auditable manifest."""
import csv
import hashlib
from html import escape
import io
import json
import math
from pathlib import Path
import re
import uuid
from urllib.parse import unquote, urlparse
import zipfile

from .analytics import overlay, points
from .storage import frame_window, number
from ..domain.metrics import METRIC_BY_KEY


def comparison_html(payload, title='PerfZY 本地对比'):
    curves = payload.get('curves', [])
    all_points = [p for curve in curves for p in curve['points']]
    xmax = max((p['x'] for p in all_points), default=1.) or 1.
    xmin = min((p['x'] for p in all_points), default=0.)
    xspan=max(xmax-xmin,.001)
    ymin = min((p['y'] for p in all_points), default=0.)
    ymax = max((p['y'] for p in all_points), default=1.)
    if ymax==ymin:
        padding=max(1.,abs(ymin)*.02)/2
        ymin-=padding;ymax+=padding
    span = max(ymax-ymin, 1e-9)
    definition=METRIC_BY_KEY.get(payload.get('metric'))
    unit=definition.unit if definition else '原指标单位'
    colors = ['#5ba6ff', '#f2a65a', '#72dbaf', '#db82e8', '#ed7581', '#a5cb53']
    paths, labels = [], []
    for i, curve in enumerate(curves):
        commands, previous,segment = [], None,None
        for point in curve['points']:
            split = (previous is None or point.get('segment')!=segment
                     or (not curve.get('reduced') and curve.get('gap_threshold') and point['x']-previous > curve['gap_threshold']))
            commands.append(f"{'M' if split else 'L'}{70+(point['x']-xmin)/xspan*880:.3f},{360-(point['y']-ymin)/span*300:.3f}")
            previous = point['x']
            segment=point.get('segment')
        if len(curve['points'])==1:
            point=curve['points'][0]
            paths.append(f'<circle id="curve{i}" cx="{70+(point["x"]-xmin)/xspan*880:.3f}" cy="{360-(point["y"]-ymin)/span*300:.3f}" r="3" fill="{colors[i%6]}"/>')
        else:
            paths.append(f'<path id="curve{i}" d="{" ".join(commands)}" fill="none" stroke="{colors[i%6]}" stroke-width="2"/>')
        labels.append(f'<label style="color:{colors[i%6]}"><input type="checkbox" checked data-curve="curve{i}">'
                      f'{escape(str(curve.get("name") or curve.get("id")))} ({curve["original_count"]} 点)</label>')
    details = escape(json.dumps(payload, ensure_ascii=False, indent=2))
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>{escape(title)}</title>
<style>body{{background:#192233;color:#dbe5f6;font:14px system-ui;margin:24px}}h1{{font-size:22px}}
svg{{width:100%;max-height:480px;background:#111a29}}label{{display:inline-block;margin:8px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}
text{{fill:#b9c8df;font-size:13px}}details{{margin-top:20px}}</style>
<h1>{escape(title)}</h1><p>指标：{escape(str(payload.get('metric','')))}；单位：{escape(unit)}；横轴为记录或同名场景起点后的秒数。</p>
<p>只展示实测点；缺口断线；长曲线按区间保留极值，不插值。</p><div>{''.join(labels)}</div>
<svg viewBox="0 0 1000 410" role="img" aria-label="同指标多记录叠加曲线">
<path d="M70 45 V360 H950" stroke="#5d708d" fill="none"/>
<text x="4" y="60">{ymax:.2f}</text><text x="4" y="360">{ymin:.2f}</text>
<text x="70" y="390">{xmin:.2f} s</text><text x="880" y="390">{xmax:.2f} s</text>{''.join(paths)}</svg>
<details><summary>数据、备注及来源</summary><pre>{details}</pre></details>
<script>document.querySelectorAll('[data-curve]').forEach(x=>x.addEventListener('change',()=>{{document.getElementById(x.dataset.curve).style.display=x.checked?'':'none'}}));</script></html>'''


def export_bundle(root, sessions, *, start, end, metric='fps', scene='', notes=None,
                  event=None, log_path='', title='性能证据包'):
    start, end = float(start), float(end)
    if not all(math.isfinite(v) for v in (start,end)) or not 0 <= start < end:
        raise ValueError('请选择有效导出时间范围')
    root = Path(root).resolve()
    output = root / 'exports' / 'studio'
    output.mkdir(parents=True, exist_ok=True)
    destination = output / ('evidence-' + uuid.uuid4().hex[:12] + '.zip')
    temporary = destination.with_suffix('.zip.tmp')
    manifest, warnings, total_bytes = [], [], 0
    comparison = overlay(sessions, metric, scene, start, end)
    comparison.update(notes=notes or [], event=event, range=dict(start=start,end=end))
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            def add(name, data):
                nonlocal total_bytes
                if isinstance(data, str):
                    data = data.encode('utf-8')
                if total_bytes+len(data) > 32*1024*1024:
                    warnings.append('证据包已达32MB上限，跳过 '+name)
                    return False
                bundle.writestr(name, data)
                total_bytes += len(data)
                manifest.append(dict(file=name, size=len(data), sha256=hashlib.sha256(data).hexdigest()))
                return True
            add('comparison.json', json.dumps(comparison, ensure_ascii=False, allow_nan=False, indent=2))
            add('comparison.html', comparison_html(comparison, title))
            for index, session in enumerate(sessions[:30]):
                ident = str(session.get('id') or '')
                if not re.fullmatch(r'[A-Za-z0-9_-]+', ident):
                    warnings.append('无效记录编号，跳过附件')
                    continue
                directory = (root / 'data' / 'sessions' / ident).resolve()
                sessions_root = (root / 'data' / 'sessions').resolve()
                if directory.parent != sessions_root:
                    raise ValueError('记录附件目录越界')
                origin = 0.
                if scene:
                    found = [s for s in session.get('scenes', []) if s.get('name') == scene]
                    if len(found) != 1:
                        continue
                    origin = float(found[0]['start'])
                a, b = start+origin, end+origin
                prefix = f'record-{index+1}'
                csv_output = io.StringIO(newline='')
                writer = csv.writer(csv_output)
                writer.writerow(['metric','session_seconds','aligned_seconds','value'])
                metrics_truncated=False
                for key in session.get('samples', {}):
                    if not re.fullmatch(r'[A-Za-z0-9_]+', str(key)):
                        continue
                    for point in points(session, key, a, b):
                        writer.writerow([key,point['x'],point['x']-origin,point['y']])
                        if csv_output.tell()>=4*1024*1024:
                            metrics_truncated=True;break
                    if metrics_truncated:break
                if metrics_truncated:warnings.append(prefix+'：指标CSV达到4MB上限，已截断')
                add(prefix+'/metrics.csv', csv_output.getvalue())
                add(prefix+'/frames.json', json.dumps(frame_window(directory,a,b,center=(event or {}).get('time')), ensure_ascii=False, allow_nan=False))
                shot_root = (directory / 'screenshots').resolve()
                count = 0
                for shot in session.get('screenshots', []):
                    stamp = number(shot.get('seconds'))
                    if stamp is None or not a <= stamp <= b or count >= 20:
                        continue
                    parsed = urlparse(str(shot.get('image') or ''))
                    # Resolve only session-local screenshot basenames. Never
                    # follow a foreign URI, remote URL or external symlink.
                    name = Path(unquote(parsed.path)).name
                    image = (shot_root/name).resolve()
                    if image.parent != shot_root or image.suffix.lower() not in {'.jpg','.jpeg','.png'}:
                        warnings.append('跳过非记录内截图')
                        continue
                    if image.is_file() and image.stat().st_size <= 4*1024*1024:
                        if add(f'{prefix}/screenshots/{count+1}{image.suffix}',image.read_bytes()):
                            count += 1
                add(prefix+'/context.json', json.dumps(dict(id=ident, name=session.get('name'),
                    device=session.get('device'),target=session.get('target'),studio=session.get('studio'),
                    range=dict(start=a,end=b),screenshots_exported=count,metrics_truncated=metrics_truncated), ensure_ascii=False, allow_nan=False))
            if log_path:
                path = Path(log_path)
                if path.suffix.lower() not in {'.log','.txt'} or not path.is_file():
                    raise ValueError('可选日志必须是明确选择的本地 .log/.txt 文件')
                with path.open('rb') as source:
                    source.seek(max(0,path.stat().st_size-65536))
                    tail = source.read(65536).decode('utf-8', errors='replace')
                tail = re.sub(r'(?im)^.*(?:token|authorization|password|secret|api[_-]?key)\s*[:=].*$',
                              '[REDACTED sensitive log line]',tail)
                add('logs/selected-log-tail.txt', tail)
                warnings.append('可选日志仅附最后64KB，未自动与采集时间对齐；基础脱敏不保证清除所有敏感信息，分享前请复核。')
            bundle.writestr('manifest.json', json.dumps(dict(files=manifest,warnings=warnings,
                local_only=True, metric=metric, range=dict(start=start,end=end)), ensure_ascii=False,indent=2))
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination
