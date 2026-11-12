from __future__ import annotations

import math


# Complete long frames remain inspectable; five seconds marks review, not loss.
# Android's incomplete timestamps are rejected before their subtraction.
MAX_FRAME_TIME_MS = 86_400_000.0
MAX_DROPPED_FRAMES_PER_SAMPLE = 10_000.0

FRAME_TIME_METRICS = frozenset({
    "frame_time", "frame_time_p95", "frame_time_p99", "cpu_frame_busy",
    "cpu_frame_wait", "gpu_time", "gpu_busy", "gpu_wait", "gpu_latency",
})
PERCENT_METRICS = frozenset({
    "jank_rate", "dropped_frame_rate", "app_cpu", "app_user_cpu",
    "app_kernel_cpu", "total_cpu", "app_gpu_usage", "system_cpu_user",
    "system_cpu_kernel", "system_gpu_usage", "system_gpu_3d",
})
FPS_METRICS = frozenset({"fps", "fps_1_low", "fps_0_1_low"})


def metric_value_is_plausible(key: object, value: object) -> bool:
    """Return whether a finite metric value is safe to persist and display."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(number):
        return False
    name = str(key)
    if name in FRAME_TIME_METRICS:
        return 0.0 <= number <= MAX_FRAME_TIME_MS
    if name == "dropped_frames":
        return 0.0 <= number <= MAX_DROPPED_FRAMES_PER_SAMPLE
    if name in PERCENT_METRICS or (name.startswith("cpu_core_") and name.endswith("_usage")):
        return 0.0 <= number <= 100.0
    if name in FPS_METRICS:
        return 0.0 <= number <= 1_000.0
    return True


def sample_points(sample, allowed_keys=None) -> dict[str, dict[str, float]]:
    """Validate values and source timestamps together for GUI, API and disk."""
    points = {}
    timestamps = getattr(sample, 'observed_elapsed', {}) or {}
    for key, raw in sample.values.items():
        if key in getattr(sample, 'held_keys', ()):
            continue
        if allowed_keys is not None and key not in allowed_keys:
            continue
        if not metric_value_is_plausible(key, raw):
            continue
        try:
            stamp = float(timestamps.get(key, sample.elapsed))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(stamp) or stamp < 0:
            continue
        points[str(key)] = {'x': stamp, 'y': float(raw)}
    return points
