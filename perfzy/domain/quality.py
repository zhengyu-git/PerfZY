"""Bounded capture-quality counters; raw readings live in the session audit log."""
from __future__ import annotations

import math

from .validation import FRAME_TIME_METRICS, metric_value_is_plausible


def json_reading(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)[:120]
    return number if math.isfinite(number) else str(value)[:120]


class QualityTracker:
    def __init__(self):
        self.metrics = {}
        self.samples = 0
        self.last_elapsed = None
        self.max_gap_seconds = 0.0
        self.collector_total_ms = 0.0
        self.collector_max_ms = 0.0

    def record(self, elapsed, values, requested, adjustments=None, collector_ms=0, interval_seconds=None,
               observed_elapsed=None, held_keys=()):
        self.samples += 1
        if self.last_elapsed is not None:
            self.max_gap_seconds = max(self.max_gap_seconds, elapsed - self.last_elapsed)
        self.last_elapsed = elapsed
        try:
            cost = float(collector_ms or 0)
        except (TypeError, ValueError):
            cost = 0.0
        if math.isfinite(cost) and cost >= 0:
            self.collector_total_ms += cost
            self.collector_max_ms = max(self.collector_max_ms, cost)
        for key in set(requested):
            row = self.metrics.setdefault(str(key), dict(requested=0, valid=0, missing=0,
                                                        adjusted=0, suspicious_long=0, held=0,
                                                        _coverage=0.0, _coverage_end=0.0,
                                                        _period_known=False))
            if key in held_keys:
                row['held'] += 1
                continue
            row['requested'] += 1
            value = values.get(key)
            try:
                observed = float((observed_elapsed or {}).get(key, elapsed))
            except (ValueError, TypeError, OverflowError):
                observed = math.nan
            valid = (math.isfinite(observed) and observed >= 0
                     and value is not None and metric_value_is_plausible(key, value))
            row['valid' if valid else 'missing'] += 1
            if interval_seconds is not None and math.isfinite(interval_seconds) and interval_seconds > 0:
                row['_period_known'] = True
                if valid:
                    end = observed + interval_seconds
                    row['_coverage'] += max(0.0, end - max(observed, row['_coverage_end']))
                    row['_coverage_end'] = max(row['_coverage_end'], end)
            reason = (adjustments or {}).get(key, '')
            if reason and reason != 'confirmed_upward_change':
                row['adjusted'] += 1
            if valid and key in FRAME_TIME_METRICS and (float(value) > 5000 or
                    reason == 'long_complete_frame_needs_review'):
                row['suspicious_long'] += 1

    def summary(self, duration=None):
        duration = max(0.0, float(duration if duration is not None else self.last_elapsed or 0))
        metrics = {}
        for key, row in sorted(self.metrics.items()):
            covered = min(duration, max(0.0, row['_coverage'] - max(0.0, row['_coverage_end'] - duration)))
            metrics[key] = {name: value for name, value in row.items() if not name.startswith('_')}
            metrics[key].update(valid_percent=100 * row['valid'] / max(1, row['requested']),
                                coverage_estimate_seconds=covered if row['_period_known'] else None,
                                coverage_estimate_percent=100 * covered / duration
                                if row['_period_known'] and duration > 0 else None)
        return dict(schema=1, samples=self.samples, max_gap_seconds=self.max_gap_seconds,
                    capture_duration_seconds=duration,
                    collector_avg_ms=self.collector_total_ms / max(1, self.samples),
                    collector_max_ms=self.collector_max_ms,
                    metrics=metrics,
                    definition='有效读数占非缓存请求采样次数，held 单独计数且不重复计入请求、有效读数或覆盖。时间覆盖按每条有效源观察之后的配置采样周期保守估计，空窗不跨越补齐，分母为整次时长；慢通道和中途启停指标可能降低覆盖率。两者都不代表准确率。')
