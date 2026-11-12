"""User-started, local repeat capture plans. Never launches or controls a game."""
import math
import threading
from datetime import datetime
from uuid import uuid4
from ..domain.metrics import METRIC_BY_KEY

from ..automation import PerfZY
from .storage import write_json


def validate_plan(value):
    if not isinstance(value, dict):
        raise ValueError('方案格式无效')
    result = dict(value)
    for key, minimum, maximum in (('duration', 2, 86400), ('rounds',1,100),
                                ('interval',.5,5),('rest',0,3600)):
        number = float(result.get(key, dict(duration=60,rounds=1,interval=1,rest=0)[key]))
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError(f'{key} 超出允许范围')
        if key == 'rounds' and not number.is_integer():
            raise ValueError('轮次必须是整数')
        result[key] = int(number) if key == 'rounds' else number
    result['name'] = str(result.get('name') or '性能回归').strip()[:60] or '性能回归'
    result['version'] = str(result.get('version') or '').strip()[:80]
    result['metrics'] = [str(key) for key in result.get('metrics',[])][:100]
    if not result['metrics']:
        raise ValueError('方案至少需要一个指标')
    if set(result['metrics'])-METRIC_BY_KEY.keys():
        raise ValueError('方案包含未知指标')
    result['screenshots'] = bool(result.get('screenshots', True))
    result['report'] = bool(result.get('report', True))
    return result


def run_plan(root, device, target, plan, cancel: threading.Event, progress,
             api_factory=PerfZY, screenshot_window=None):
    """Cancel saves the current round, prevents every subsequent round."""
    plan = validate_plan(plan)
    results = []
    run_id=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:4]
    with api_factory(root=root) as api:
        # Pin the immutable GUI-selected Device/Target (including exact PID),
        # rather than selecting an arbitrary first same-name executable.
        api.device, api.game = device, target
        api.screenshot_window = screenshot_window
        for index in range(plan['rounds']):
            if cancel.is_set():
                break
            name = f"{plan['name'][:25]} {plan['version'][:20]} {run_id} R{index+1:02d}".strip()
            progress(dict(state='running', round=index+1,total=plan['rounds'],name=name))
            api.start(metrics=plan['metrics'], sample_interval=plan['interval'],screenshots=plan['screenshots'])
            error = ''
            try:
                while api.elapsed < plan['duration'] and not cancel.wait(.2):
                    api.check_health()
            except Exception as exc:
                error = str(exc)
            finally:
                result = api.stop(name)
            write_json(result.session_path.parent / 'studio-metadata.json',dict(version=plan['version'],
                plan=plan['name'], run=run_id,round=index+1,cancelled=cancel.is_set(),error=error))
            row = dict(id=result.session_id,path=str(result.session_path),name=name,error=error)
            if plan['report']:
                try:
                    row['report'] = str(api.generate_html(result))
                except Exception as exc:
                    row['report_error'] = str(exc)
            results.append(row)
            progress(dict(state='saved', round=index+1,total=plan['rounds'],results=list(results)))
            if error:
                raise RuntimeError('本轮已保存，任务因采样错误停止：'+error)
            if cancel.is_set() or (index+1 < plan['rounds'] and cancel.wait(plan['rest'])):
                break
    return results
