"""Bounded main-render-stream selection; never merge independent swapchains."""
from collections import Counter


class RenderStreamSelector:
    def __init__(self):
        self.reset()

    def reset(self):
        self.selected = None
        self.candidate = None
        self.candidate_windows = 0
        self.switches = 0
        self.last_stream_count = 0
        self.excluded_rows = 0

    @staticmethod
    def identity(row):
        try:
            pid = int(row.get('processid') or row.get('process id') or 0)
        except (ValueError, TypeError):
            return None
        return pid, str(row.get('swapchainaddress') or '').strip().casefold()

    def choose(self, rows):
        # Only comparable valid FrameTime rows should be supplied by the caller.
        counts = Counter(self.identity(row) for row in rows)
        counts.pop(None, None)
        self.last_stream_count = len(counts)
        if not counts:
            return None
        winner = max(counts, key=lambda key: (counts[key], key == self.selected))
        previous = self.selected
        if self.selected not in counts:
            self.selected = winner
            self.candidate = None
            self.candidate_windows = 0
        elif winner != self.selected and counts[winner] >= counts[self.selected] * 2:
            if winner != self.candidate:
                self.candidate, self.candidate_windows = winner, 0
            self.candidate_windows += 1
            if self.candidate_windows >= 3:
                self.selected = winner
                self.candidate = None
                self.candidate_windows = 0
        else:
            self.candidate = None
            self.candidate_windows = 0
        if previous is not None and previous != self.selected:
            self.switches += 1
        self.excluded_rows += sum(value for key, value in counts.items() if key != self.selected)
        return self.selected

    def snapshot(self):
        return {'policy': 'dominant PID+swapchain; challenger >=2x for 3 windows',
                'pid': self.selected[0] if self.selected else None,
                'swapchain': self.selected[1] if self.selected else None,
                'last_stream_count': self.last_stream_count, 'switches': self.switches,
                'excluded_other_stream_rows': self.excluded_rows,
                'limitation': '主渲染流按活动帧数选择，不保证是肉眼所见的游戏主窗口；可查看 PID/交换链核对。'}
