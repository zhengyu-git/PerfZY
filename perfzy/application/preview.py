"""Deterministic UI states used to review the interface offscreen.

This is a development harness, not part of the collection path. It reaches into
``AppController`` internals on purpose so that the entry point stays free of
preview scaffolding, and it is only imported when a ``--*-preview`` flag is
passed on the command line.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..domain.models import Platform


PREVIEW_FLAGS = ("--session-preview", "--chart-preview", "--chart-range-preview", "--overlay-preview",
                 "--screenshot-preview", "--exit-preview")


def _ramp(base: float, step: float, modulus: int) -> list[dict]:
    return [{"x": x, "y": base + (x % modulus) * step} for x in range(1, 131, 2)]


def _synthetic_curves(controller) -> None:
    controller._preferences.visible_charts.update({"fps", "cpu", "memory"})
    controller._preferences.enabled_for(Platform.WINDOWS).update(
        {"fps", "frame_time", "app_cpu", "total_cpu", "memory", "available_memory"})
    controller._samples["fps"] = _ramp(55, 1, 9)
    controller._samples["frame_time"] = _ramp(14, .35, 11)
    controller._samples["app_cpu"] = _ramp(18, 1, 13)
    controller._samples["total_cpu"] = _ramp(34, 1, 17)
    controller._samples["memory"] = [{"x": x, "y": 620 + x * 5} for x in range(1, 131, 2)]


def _recent_screenshots(controller, root: Path) -> None:
    candidates = sorted((root / "data" / "sessions").glob("*/screenshots/*.jpg"))[-24:]
    controller._screenshots = [
        {"image": item.as_uri(), "time": f"{index:02d}:00", "seconds": index * 3.0}
        for index, item in enumerate(candidates)]
    controller._selected_screenshot = len(controller._screenshots) - 1


def _active_collection(controller) -> None:
    controller._collecting = True
    controller._started_at = time.monotonic() - 155
    controller._screenshots = [{"image": "", "time": "00:00", "seconds": 0}] * 8


def apply_previews(controller, flags: set[str], root: Path) -> None:
    """Put the controller into whichever review state the flags ask for."""
    if "--session-preview" in flags and controller.sessionRecords:
        controller.openSession(controller.sessionRecords[0]["id"])
    if "--chart-preview" in flags or "--chart-range-preview" in flags:
        _synthetic_curves(controller)
    if "--chart-range-preview" in flags:
        controller.setChartViewRange(.45, .75)
    if "--overlay-preview" in flags:
        controller.setSelectionRatios(.12, .68)
        controller.setTimeLockRatio(.24)
        if "--chart-range-preview" in flags:
            controller.setSelectionRatios(.52, .68)
            controller.setTimeLockRatio(.58)
    if "--screenshot-preview" in flags:
        _recent_screenshots(controller, root)
    if "--exit-preview" in flags:
        _active_collection(controller)
