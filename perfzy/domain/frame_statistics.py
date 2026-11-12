"""Frame statistics with storage proportional to bins, not frame count."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from math import isfinite
from typing import Iterable, Mapping

from .validation import MAX_FRAME_TIME_MS


class FrameTimeDistribution:
    """Retain existing linear-interpolated percentiles for weighted samples.

    Histogram values remain bucket representatives; this does not turn a
    coarse device histogram into precise per-frame timestamps.
    """

    def __init__(self, counts: Mapping[float, int]) -> None:
        self.bins = tuple(sorted(
            (float(value), int(count)) for value, count in counts.items()
            if isfinite(float(value)) and 0 < float(value) <= MAX_FRAME_TIME_MS
            and int(count) > 0))
        total = 0
        cumulative = []
        for _, count in self.bins:
            total += count
            cumulative.append(total)
        self.count = total
        self._cumulative = tuple(cumulative)

    @classmethod
    def from_samples(cls, values: Iterable[float]) -> FrameTimeDistribution:
        return cls(Counter(values))

    @property
    def mean(self) -> float | None:
        return (sum(value * count for value, count in self.bins) / self.count
                if self.count else None)

    def percentile(self, ratio: float) -> float | None:
        if not isfinite(ratio) or not 0 <= ratio <= 1:
            raise ValueError('percentile ratio must be within [0, 1]')
        if not self.count:
            return None
        position = (self.count - 1) * ratio
        lower = int(position)
        upper = min(self.count - 1, lower + 1)
        low_value = self.bins[bisect_right(self._cumulative, lower)][0]
        high_value = self.bins[bisect_right(self._cumulative, upper)][0]
        return low_value + (high_value - low_value) * (position - lower)

    def between(self, lower: float, upper: float = float('inf')) -> int:
        return sum(count for value, count in self.bins if lower < value <= upper)

    def dropped_frames(self, refresh_budget: float) -> float:
        return float(sum(max(0, int(value / refresh_budget) - 1) * count
                         for value, count in self.bins))
