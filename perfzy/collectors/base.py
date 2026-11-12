from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterable
from statistics import median
import math

from ..domain.models import Device, Sample, Target


class FpsStabilizer:
    """Suppress isolated upward telemetry spikes while preserving real changes."""

    def __init__(self) -> None:
        self.history: deque[float] = deque(maxlen=12)
        self.high_candidates: list[float] = []
        self.suppressed_spikes = 0
        self.last_raw: float | None = None
        self.last_reason = ""
        self._observation_id = None
        self._last_result = None

    def reset(self) -> None:
        self.history.clear()
        self.high_candidates.clear()
        self.suppressed_spikes = 0
        self.last_raw = None
        self.last_reason = ""
        self._observation_id = None
        self._last_result = None

    def accept(self, fps: float | None, *, observation_id=None) -> float | None:
        # A cached device observation must not count as another confirmation.
        if fps is not None and observation_id is not None and observation_id == self._observation_id:
            return self._last_result
        self._observation_id = observation_id if fps is not None else None
        result = self._accept(fps)
        self._last_result = result
        return result

    def _accept(self, fps: float | None) -> float | None:
        self.last_reason = ""
        self.last_raw = fps
        if fps is None:
            self.high_candidates.clear()
            return None
        try:
            fps = float(fps)
        except (TypeError, ValueError):
            fps = math.nan
        if not math.isfinite(fps) or not 0 <= fps <= 1000:
            self.high_candidates.clear()
            self.last_reason = "invalid_fps"
            return None
        if len(self.history) >= 3:
            baseline = float(median(self.history))
            upward_limit = baseline + max(3.0, baseline * .12)
            if fps > upward_limit:
                self.high_candidates.append(fps)
                del self.high_candidates[:-6]
                self.last_reason = "pending_upward_change"
                if len(self.high_candidates) < 3:
                    self.suppressed_spikes += 1
                    return baseline
                candidates = list(self.high_candidates)
                def stable(items):
                    return max(items) - min(items) <= max(3.0, median(items) * .12)
                fast_switch = (min(candidates[-3:]) > baseline * 1.45 and
                               stable(candidates[-3:]))
                sustained_switch = (len(candidates) >= 6 and
                                    min(candidates) > upward_limit and stable(candidates))
                if fast_switch or sustained_switch:
                    self.history.clear()
                    self.history.extend(candidates[-3:] if fast_switch else candidates)
                    self.high_candidates.clear()
                    self.last_reason = "confirmed_upward_change"
                    return fps
                self.suppressed_spikes += 1
                return baseline
        self.high_candidates.clear()
        self.history.append(fps)
        return fps


class Collector(ABC):
    """One platform collector. Implementations must keep blocking I/O off the UI thread."""

    def __init__(self) -> None:
        self._enabled: frozenset[str] = frozenset()

    def audited_sample(self, elapsed: float, values: dict, source: str,
                       *, raw_values: dict | None = None,
                       adjustments: dict | None = None,
                       sources: dict | None = None,
                       observed_elapsed=None, frames=(), held_keys=frozenset()) -> Sample:
        raw = dict(values)
        adjustments = dict(adjustments or {})
        stabilizer = getattr(self, "_fps_stabilizer", None)
        if stabilizer is not None and "fps" in values:
            raw["fps"] = stabilizer.last_raw
            if stabilizer.last_reason:
                adjustments["fps"] = stabilizer.last_reason
        raw.update(raw_values or {})
        for key, value in values.items():
            if value is None and raw.get(key) is not None and key not in adjustments:
                adjustments[key] = "source_validation_rejected"
        source_map = {key: source for key in values}
        source_map.update(sources or {})
        return Sample(elapsed, values, observed_elapsed=observed_elapsed or {},
                      frames=tuple(frames), held_keys=frozenset(held_keys),
                      raw_values=raw, adjustments=adjustments, sources=source_map)

    @abstractmethod
    def discover_devices(self) -> Iterable[Device]: ...

    @abstractmethod
    def discover_targets(self, device: Device) -> Iterable[Target]: ...

    @abstractmethod
    def start(self, device: Device, target: Target, enabled_metrics: frozenset[str]) -> None: ...

    @abstractmethod
    def sample(self, elapsed: float) -> Sample: ...

    @abstractmethod
    def stop(self) -> None: ...

    def set_enabled_metrics(self, enabled_metrics: frozenset[str]) -> None:
        """Update lightweight metric gates while collection is running.

        Cheap enough to run on the UI thread. Anything that has to talk to the
        device belongs in ``activate_pending_sources``.
        """
        self._enabled = enabled_metrics

    @property
    def enabled_metrics(self) -> frozenset[str]:
        """Immutable snapshot of the metric gate used by the next sample."""
        return self._enabled

    def activate_pending_sources(self) -> None:
        """Start device-side data sources that newly enabled metrics require.

        Called on a worker thread after every selection change, and expected to
        be idempotent: without it, a metric enabled mid-collection would stay
        empty for the rest of the session because its source was never started.
        """

    def set_capture_paused(self, paused: bool) -> None:
        """Nonblocking hook for excluding paused frame observations."""

    @property
    def fps_suppressed_spikes(self) -> int:
        """Number of implausible isolated FPS jumps corrected this run."""
        stabilizer = getattr(self, "_fps_stabilizer", None)
        return int(getattr(stabilizer, "suppressed_spikes", 0))
