"""Match screenshot evidence within its capture period and expected cadence."""
from dataclasses import dataclass
import math
from statistics import median


@dataclass(frozen=True)
class ScreenshotMatch:
    index: int = -1
    reason: str = 'missing'


def finite_time(value):
    try:
        value=float(value)
    except (TypeError,ValueError,OverflowError):
        return None
    return value if math.isfinite(value) and value>=0 else None


def match_screenshot(shots, elapsed, settings=None, changes=(), *, truncated=False):
    """Never bridge an off/on boundary or an arbitrarily large missing interval.

    A nearby capture is useful evidence between scheduled shots. An explicit
    disabled period takes precedence over proximity, including after re-enable.
    Legacy records without settings use a conservative observed cadence.
    """
    elapsed=finite_time(elapsed)
    if elapsed is None:return ScreenshotMatch()
    settings=settings if isinstance(settings,dict) else {}
    enabled=settings.get('screenshots') if isinstance(settings.get('screenshots'),bool) else None
    interval=finite_time(settings.get('screenshot_interval_ms'))
    interval=interval/1000 if interval else None
    if truncated:enabled=None
    events=[]
    for change in changes if isinstance(changes,(list,tuple)) else ():
        if not isinstance(change,dict):continue
        stamp=finite_time(change.get('elapsed'))
        if stamp is not None and isinstance(change.get('settings'),dict):
            events.append((stamp,change['settings']))
    events.sort(key=lambda event:event[0])
    start,end=0.0,math.inf
    for stamp,state in events:
        flag=state.get('screenshots')
        flag=flag if isinstance(flag,bool) else enabled
        if stamp>elapsed:
            if flag!=enabled:
                end=stamp
                break
            continue
        if flag!=enabled:start=stamp
        enabled=flag
        candidate_interval=finite_time(state.get('screenshot_interval_ms'))
        if candidate_interval:interval=candidate_interval/1000
    if enabled is False:return ScreenshotMatch(reason='disabled')
    candidates=[]
    timestamps=[]
    for index,shot in enumerate(shots if isinstance(shots,(list,tuple)) else ()):
        if not isinstance(shot,dict):continue
        stamp=finite_time(shot.get('seconds',shot.get('elapsed')))
        if stamp is None:continue
        timestamps.append(stamp)
        if start<=stamp<end:candidates.append((abs(stamp-elapsed),index))
    if not candidates:return ScreenshotMatch()
    if interval is None:
        timestamps=sorted(set(timestamps))
        steps=[b-a for a,b in zip(timestamps,timestamps[1:]) if b>a]
        interval=min(10.0,median(steps)) if steps else 2.0
    distance,index=min(candidates)
    return ScreenshotMatch(index,'matched') if distance<=max(.15,interval*1.25) else ScreenshotMatch()
