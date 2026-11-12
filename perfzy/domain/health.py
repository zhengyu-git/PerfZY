"""Bounded collection diagnostics; querying never contacts a device."""
from collections import deque
import math


class SamplingHealth:
    def __init__(self, interval=1.0):
        self.interval = max(.001, float(interval))
        self.count = self.failed = self.overruns = self.skipped = 0
        self.total_ms = self.max_ms = 0.0
        self.recent_ms = deque(maxlen=128)
        self.fields = {}

    def observe(self, elapsed, points, work_ms=0.0, requested=(), error=False, held=()):
        self.count += 1
        self.failed += bool(error)
        duration = max(0.0, float(work_ms)) if math.isfinite(float(work_ms)) else 0.0
        self.total_ms += duration
        self.max_ms = max(self.max_ms, duration)
        self.overruns += duration > self.interval * 1000
        self.recent_ms.append(duration)
        for key in set(requested) | set(points):
            status = self.fields.setdefault(str(key), {'received': 0, 'missing': 0, 'last_elapsed': None})
            if key in points:
                status['received'] += 1
                status['last_elapsed'] = points[key]['x']
            elif key in held:
                status['held'] = status.get('held', 0) + 1
            else:
                status['missing'] += 1

    def snapshot(self, elapsed=0.0):
        recent = sorted(self.recent_ms)
        fields = {}
        for key, status in self.fields.items():
            fields[key] = dict(status)
            fields[key]['age_s'] = (max(0.0, elapsed - status['last_elapsed'])
                                    if status['last_elapsed'] is not None else None)
        return {'samples': self.count, 'failed_samples': self.failed,
                'overruns': self.overruns, 'skipped_ticks': self.skipped,
                'sample_interval_s': self.interval,
                'mean_work_ms': self.total_ms / self.count if self.count else 0.0,
                'max_work_ms': self.max_ms,
                'recent_p95_ms': recent[min(len(recent) - 1, int((len(recent) - 1) * .95))] if recent else None,
                'metrics': fields}
