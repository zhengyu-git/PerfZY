"""Ordered-frame Jank statistics, kept separate from legacy slow-frame buckets."""
from collections import deque
import math

from .validation import MAX_FRAME_TIME_MS

JANK_METRICS = frozenset({'frame_jank', 'frame_big_jank'})
JANK_CONTRACT = 'perfzy-ordered-frames-v1'
WINDOWS_PRESENTMON_FRAME_SOURCE = 'PresentMon'
WINDOWS_DXGI_FRAME_SOURCE = 'DXGI ETW (compatibility)'
JANK_SOURCES = {WINDOWS_PRESENTMON_FRAME_SOURCE: 'windows', WINDOWS_DXGI_FRAME_SOURCE: 'windows',
                'Android SurfaceFlinger --latency': 'android',
                'iOS CoreProfileSessionTap': 'ios'}
JANK_DESCRIPTION = ('按最近三帧均值判定：Jank 同时超过均值 2 倍和 83.33 ms；'
                    'Big Jank 同时超过均值 3 倍和 125 ms，属于 Jank 子集。'
                    '前三帧用于建立基线，三端采用相同阈值。')


class FrameJankTracker:
    def __init__(self):
        self.history = deque(maxlen=3)
        self.stream = None

    def reset(self):
        self.history.clear()
        self.stream = None

    def annotate(self, frames):
        annotated, evaluated, jank, big = [], 0, 0, 0
        for original in frames:
            row = dict(original)
            if row.get('source') not in JANK_SOURCES:
                self.reset()
                annotated.append(row)
                continue
            stream = tuple(row.get(key) for key in ('source', 'pid', 'target_pid', 'swapchain', 'layer', 'stream_epoch', 'source_session_id'))
            if stream != self.stream:
                self.reset()
                self.stream = stream
            value = row.get('frame_ms')
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 < value <= MAX_FRAME_TIME_MS:
                self.reset()
                annotated.append(row)
                continue
            # Compare frame durations rounded to two decimal places.
            cost = float(f'{value:.2f}')
            if cost <= 0:
                self.reset()
                annotated.append(row)
                continue
            valid = len(self.history) == 3
            mean = sum(self.history) / 3 if valid else 0
            is_jank = valid and cost > 2 * mean and cost > 1000 / 24 * 2
            is_big = valid and cost > 3 * mean and cost > 125
            row.update(jank_contract=JANK_CONTRACT, jank_valid=valid,
                       jank=bool(is_jank) if valid else None,
                       big_jank=bool(is_big) if valid else None)
            self.history.append(cost)
            if valid:
                evaluated += 1
                jank += int(is_jank)
                big += int(is_big)
            annotated.append(row)
        return annotated, {'frame_jank': float(jank) if evaluated else None,
                           'frame_big_jank': float(big) if evaluated else None}
