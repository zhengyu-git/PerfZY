"""Ordered actual-present timestamps from SurfaceFlinger --latency."""
from ..domain.jank import FrameJankTracker
from ..domain.validation import MAX_FRAME_TIME_MS

FRAME_SOURCE = 'Android SurfaceFlinger --latency'


class AndroidPresentFrames:
    def __init__(self):
        self.tracker = FrameJankTracker()
        self.layer = ''
        self.last = None
        self.anchor = None
        self.epoch = 0
        self.error = ''
        self.lost_windows = 0

    def reset(self):
        self.last = self.anchor = None
        self.tracker.reset()
        self.epoch += 1

    def read(self, text, layer, elapsed):
        missing = {'frame_jank': None, 'frame_big_jank': None}
        if layer != self.layer:
            self.reset()
            self.layer = layer
        stamps = []
        for line in text.splitlines():
            fields = line.split()
            if len(fields) != 3:
                continue
            try:
                desired, actual, ready = map(int, fields)
            except ValueError:
                continue
            if all(0 < value < 2**63 - 1 for value in (desired, actual, ready)):
                stamps.append(actual)
        stamps = sorted(set(stamps))
        if not stamps:
            if text.strip():
                self.error = 'SurfaceFlinger 未提供目标图层的实际呈现时间；Jank / Big Jank 保持缺失'
            return [], missing
        self.error = ''
        if self.last is None or stamps[-1] < self.last:
            self.reset()
            self.last = stamps[-1]
            return [], missing
        if stamps[-1] == self.last:
            return [], missing
        if self.last not in stamps:
            # Ring-buffer history was lost. A missing span is not one long frame.
            self.lost_windows += 1
            self.reset()
            self.last = stamps[-1]
            self.error = 'SurfaceFlinger 环形缓冲已覆盖上次读数，本窗口丢弃并重新预热'
            return [], missing
        fresh = [stamp for stamp in stamps if stamp > self.last]
        if self.anchor is None:
            self.anchor = fresh[-1], elapsed
        anchor_tick, anchor_elapsed = self.anchor
        rows = []
        for stamp in fresh:
            cost = (stamp - self.last) / 1e6
            self.last = stamp
            moment = anchor_elapsed + (stamp - anchor_tick) / 1e9
            if 0 < cost <= MAX_FRAME_TIME_MS and moment >= 0:
                rows.append(dict(x=moment, frame_ms=cost, source=FRAME_SOURCE,
                                 source_time_s=stamp / 1e9, layer=layer, stream_epoch=self.epoch,
                                 clock='device_present_time_host_receipt_anchor', scope='selected SurfaceFlinger layer'))
        return self.tracker.annotate(rows)
