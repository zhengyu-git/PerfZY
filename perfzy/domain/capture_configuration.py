"""Resolve what a recording requested without using its saved UI selection as evidence."""
from __future__ import annotations


def _metric_keys(value) -> set[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {key for key in value if isinstance(key, str) and key}


def _requested_in_audit(session: dict) -> set[str] | None:
    quality = session.get("quality")
    rows = quality.get("metrics") if isinstance(quality, dict) else None
    if not isinstance(rows, dict) or not (rows or quality.get("samples")):
        return None
    return {key for key, row in rows.items() if isinstance(key, str)
            and isinstance(row, dict) and isinstance(row.get("requested"), int) and row["requested"] > 0}


def capture_request_summary(session: dict, observed) -> dict:
    """Separate absent observations, unrequested selections and known stop events."""
    observed = set(observed)
    selected = _metric_keys(session.get("enabled_metrics"))
    requested = _requested_in_audit(session)
    settings = session.get("capture_settings") or {}
    active = settings.get("requested_metrics") if isinstance(settings, dict) else None
    if isinstance(active, list):
        selected.update(_metric_keys(active))
        previous = None if session.get("capture_settings_changes_truncated") else _metric_keys(active)
    else:
        previous = None
    stopped = set()
    changes = session.get("capture_settings_changes")
    for change in changes if isinstance(changes, list) else ():
        settings = change.get("settings") if isinstance(change, dict) else None
        current = settings.get("requested_metrics") if isinstance(settings, dict) else None
        if not isinstance(current, list):
            continue
        keys = _metric_keys(current)
        selected.update(keys)
        elapsed = change.get("elapsed")
        if previous is not None and isinstance(elapsed, (int, float)) and elapsed > 0:
            stopped.update(previous - keys)
        previous = keys

    absent = selected - observed
    return {
        "missing": sorted(requested - observed) if requested is not None else [],
        "not_requested": sorted(absent - requested) if requested is not None else [],
        "stopped": sorted(stopped),
        "unverified": sorted(absent) if requested is None else [],
    }


def reusable_capture_configuration(session: dict) -> tuple[dict, set[str]]:
    """Prefer the start configuration; old records fall back to actual requests or observations."""
    settings = session.get("capture_settings")
    settings = settings if isinstance(settings, dict) else {}
    initial = settings.get("requested_metrics")
    if isinstance(initial, list):
        metrics = _metric_keys(initial)
    else:
        metrics = _requested_in_audit(session)
        if metrics is None:
            samples = session.get("samples")
            metrics = {key for key, rows in samples.items() if rows} if isinstance(samples, dict) else set()
    return settings, metrics
