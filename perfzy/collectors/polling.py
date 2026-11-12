"""Bounded background reads with explicit identity, freshness and consumption."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Callable, Hashable


@dataclass(frozen=True, slots=True)
class TimedReading:
    value: str
    context: Hashable
    started: float
    completed: float


class SingleFlightPoller:
    """Try once per caller tick; never queue a second read behind a slow one.

    A completed reading is delivered once. Its completion time is preserved;
    results from a previous process or beyond ``max_age_s`` are discarded.
    This owns a separate worker so a slow diagnostic cannot occupy a worker
    used by fast counters. It neither sleeps nor chooses the caller's cadence.
    """

    def __init__(self, *, max_age_s: float = 5.0, clock=monotonic) -> None:
        if max_age_s <= 0:
            raise ValueError("max_age_s must be positive")
        self.max_age_s = float(max_age_s)
        self._clock = clock
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="PerfZY-Memory")
        self._future: Future[TimedReading] | None = None
        self._lock = Lock()
        self._closed = False
        self._submitted = self._completed = self._busy_polls = 0
        self._stale_discarded = self._context_discarded = self._failures = 0
        self._last_reading: TimedReading | None = None

    def _read(self, reader: Callable[[], str], context: Hashable) -> TimedReading:
        started = self._clock()
        value = reader()
        return TimedReading(value, context, started, self._clock())

    def poll(self, reader: Callable[[], str], context: Hashable) -> TimedReading | None:
        with self._lock:
            if self._closed:
                return None
            result = None
            if self._future is not None:
                if not self._future.done():
                    self._busy_polls += 1
                    return None
                future, self._future = self._future, None
                try:
                    candidate = future.result()
                except Exception:
                    self._failures += 1
                else:
                    self._completed += 1
                    if candidate.context != context:
                        self._context_discarded += 1
                    elif self._clock() - candidate.completed > self.max_age_s:
                        self._stale_discarded += 1
                    elif not candidate.value:
                        self._failures += 1
                    else:
                        self._last_reading = result = candidate
            self._future = self._executor.submit(self._read, reader, context)
            self._submitted += 1
            return result

    def diagnostics(self) -> dict:
        with self._lock:
            last = self._last_reading
            return {
                "submitted": self._submitted, "completed": self._completed,
                "busy_polls": self._busy_polls, "stale_discarded": self._stale_discarded,
                "context_discarded": self._context_discarded, "failures": self._failures,
                "in_flight": bool(self._future is not None and not self._future.done()),
                "pending_result": bool(self._future is not None and self._future.done()),
                "max_age_s": self.max_age_s,
                "last_received_age_s": max(0.0, self._clock() - last.completed) if last else None,
                "read_duration_s": last.completed - last.started if last else None,
                "closed": self._closed,
            }

    def set_max_age(self, seconds: float) -> None:
        if seconds <= 0:
            raise ValueError("max_age_s must be positive")
        with self._lock:
            self.max_age_s = float(seconds)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._future is not None:
                self._future.cancel()
            self._future = None
        # In-progress subprocess reads retain their own bounded timeout.
        self._executor.shutdown(wait=False, cancel_futures=True)
