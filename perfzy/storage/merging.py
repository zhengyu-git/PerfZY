"""Concatenate saved captures into an independent, ordinary session."""
from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
from urllib.parse import unquote, urlparse

from ..domain.provenance import canonical_platform
from ..domain.quality import QualityTracker
from .sessions import (LIVE_MANIFEST_NAME, LIVE_SAMPLES_DIR, SUMMARY_NAME,
                       _session_directory, _write_json_atomic, load_session,
                       new_session_id, normalize_session_name, save_session_from_spool)


def _number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else default
    except (TypeError, ValueError, OverflowError):
        return default


def _platform(session):
    device = session.get("device") or {}
    return canonical_platform(device) or {
        "windows 电脑": "windows", "windows 本机": "windows",
        "android": "android", "ios": "ios", "windows": "windows",
    }.get(str(device.get("platform") or "").casefold(), "")


def _image_path(value, directory):
    text = str(value or "")
    parsed = urlparse(text)
    # Prefer the recording's own copy, including after moving the whole app.
    name = Path(unquote(parsed.path) if parsed.scheme == "file" else text).name
    local = directory / "screenshots" / name
    if name and local.is_file():
        return local
    if parsed.scheme == "file":
        text = unquote(parsed.path)
        if parsed.netloc:
            text = "//" + parsed.netloc + text
        elif len(text) >= 3 and text[0] == "/" and text[2] == ":":
            text = text[1:]
    local = Path(text)
    if not local.is_absolute():
        local = directory / local
    if not text or not local.is_file():
        raise FileNotFoundError(f"截图文件缺失：{name or '未记录文件名'}；请恢复截图后重试")
    return local


def _copy_frames(source, destination, offset, source_id):
    path = source / "frames.sqlite3"
    if not path.is_file():
        return 0.0, 0
    last, count = 0.0, 0
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as incoming:
        with closing(sqlite3.connect(destination / "frames.sqlite3")) as output:
            output.execute("CREATE TABLE IF NOT EXISTS frames (seq INTEGER PRIMARY KEY, x REAL NOT NULL, ms REAL NOT NULL, payload TEXT NOT NULL)")
            output.execute("CREATE INDEX IF NOT EXISTS frames_time ON frames(x)")
            output.execute("CREATE INDEX IF NOT EXISTS frames_slowest ON frames(ms DESC,x)")
            cursor = incoming.execute("SELECT x,ms,payload FROM frames ORDER BY x,seq")
            while batch := cursor.fetchmany(4096):
                rows = []
                for raw_x, raw_ms, encoded in batch:
                    x, ms = _number(raw_x, None), _number(raw_ms, None)
                    if x is None or ms is None or ms <= 0:
                        raise ValueError(f"记录 {source_id} 的原始帧数据无效")
                    payload = json.loads(encoded)
                    if not isinstance(payload, dict):
                        raise ValueError(f"记录 {source_id} 的原始帧格式无效")
                    payload.update(x=offset + x, frame_ms=ms, source_session_id=source_id)
                    rows.append((offset + x, ms, json.dumps(payload, ensure_ascii=False, allow_nan=False)))
                    last = max(last, x)
                output.executemany("INSERT INTO frames(x,ms,payload) VALUES (?,?,?)", rows)
                count += len(rows)
            output.commit()
    return last, count


def _copy_audit(source, output, offset, source_id):
    path = source / "audit.jsonl"
    tracker = QualityTracker()
    last = 0.0
    if not path.is_file():
        return None, last
    with path.open(encoding="utf-8") as incoming:
        for line in incoming:
            if not line.strip():
                continue
            row = json.loads(line)
            elapsed = _number(row.get("elapsed"), None)
            if elapsed is None:
                raise ValueError(f"记录 {source_id} 的审计时间无效")
            tracker.record(elapsed, row["values"], row["requested"], row.get("adjustments"),
                           row.get("collector_ms"), row.get("interval_seconds"),
                           observed_elapsed=row.get("observed_elapsed"), held_keys=row.get("held_keys") or ())
            last = max(last, elapsed)
            observed = {}
            for key, value in (row.get("observed_elapsed") or {}).items():
                stamp = _number(value, None)
                observed[key] = offset + stamp if stamp is not None else value
                if stamp is not None:
                    last = max(last, stamp)
            row.update(elapsed=offset + elapsed, observed_elapsed=observed,
                       source_session_id=source_id)
            output.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    return tracker, last


def _quality(parts, duration):
    """Sum per-record coverage without extending observations across a seam."""
    known = [part["quality"] for part in parts if isinstance(part.get("quality"), dict)]
    if not known:
        return None
    result = QualityTracker().summary(duration)
    result.update(samples=sum(_number(q.get("samples")) for q in known),
                  collector_max_ms=max(_number(q.get("collector_max_ms")) for q in known),
                  max_gap_seconds=max(_number(q.get("max_gap_seconds")) for q in known),
                  partial=len(known) != len(parts) or any(q.get("partial") for q in known),
                  audited_source_count=len(known), source_count=len(parts))
    result["collector_avg_ms"] = sum(_number(q.get("collector_avg_ms")) * _number(q.get("samples"))
                                     for q in known) / max(1, result["samples"])
    keys = set().union(*(q.get("metrics", {}) for q in known))
    for key in sorted(keys):
        rows = [q.get("metrics", {}).get(key, {}) for q in known]
        counters = {name: sum(_number(row.get(name)) for row in rows)
                    for name in ("requested", "valid", "missing", "adjusted", "suspicious_long", "held")}
        covered = sum(_number(row.get("coverage_estimate_seconds")) for row in rows)
        complete = not result["partial"] and all(
            key in q.get("metrics", {}) and q["metrics"][key].get("coverage_estimate_seconds") is not None
            for q in known)
        counters.update(valid_percent=100 * counters["valid"] / max(1, counters["requested"]),
                        coverage_estimate_seconds=covered if complete else None,
                        coverage_estimate_percent=100 * covered / duration if complete and duration > 0 else None)
        result["metrics"][key] = counters
    result["definition"] += " 合并记录按来源分别计算后相加，不跨记录边界补齐覆盖；缺少质量记录的来源不推算有效率。"
    return result


def merge_sessions(root: Path, session_ids, session_name: str = "") -> Path:
    """Preserve input order, copy every local asset and publish only on success."""
    if not isinstance(session_ids, (list, tuple)) or len(session_ids) < 2:
        raise ValueError("请至少选择两条采集记录")
    identifiers = [str(value) for value in session_ids]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("同一条记录不能重复选择")
    sources = [_session_directory(root, value) for value in identifiers]
    for source in sources:
        if not (source / "session.json").is_file():
            raise FileNotFoundError(f"采集记录不存在：{source.name}")

    session_id = new_session_id()
    final_directory = _session_directory(root, session_id)
    # The nested temporary root has no session.json at history's glob depth.
    staging = final_directory.parent / ("_merge_" + session_id)
    staging.mkdir(parents=True, exist_ok=False)
    directory = _session_directory(staging, session_id)
    directory.mkdir(parents=True)
    spool = directory / LIVE_SAMPLES_DIR
    spool.mkdir()
    sample_files, screenshots, scenes, parts, changes, events = {}, [], [], [], [], []
    offset, frame_count, frames_dropped = 0.0, 0, 0
    first, platform = None, ""
    enabled = set()
    try:
        # Keep audit under a temporary name while finalizing: quality is summed
        # per segment so an interval cannot count time from the next capture.
        with (directory / "audit-merged.jsonl").open("w", encoding="utf-8", newline="\n") as audit:
            for ordinal, source in enumerate(sources, 1):
                session = load_session(root, source.name)
                if session is None:
                    raise ValueError(f"无法读取采集记录：{source.name}")
                current_platform = _platform(session)
                if not current_platform or (platform and platform != current_platform):
                    raise ValueError("请选择同一平台（Windows、Android 或 iOS）的记录，以保证报告和转换的指标含义一致")
                platform = current_platform
                if first is None:
                    first = {key: deepcopy(session.get(key)) for key in
                             ("device", "target", "started_at", "created_at", "capture_settings", "telemetry")}
                duration = _number(session.get("duration"))
                enabled.update(key for key in session.get("enabled_metrics", []) if isinstance(key, str))
                for key, points in session["samples"].items():
                    if not re.fullmatch(r"[A-Za-z0-9_]+", key):
                        raise ValueError(f"记录 {source.name} 包含无效指标名称")
                    enabled.add(key)
                    path = sample_files.setdefault(key, spool / (key + ".jsonl"))
                    with path.open("a", encoding="utf-8", newline="\n") as output:
                        for point in points:
                            duration = max(duration, point["x"])
                            output.write(json.dumps(dict(x=offset + point["x"], y=point["y"]), separators=(",", ":")) + "\n")
                for index, shot in enumerate(sorted(session["screenshots"], key=lambda row: row["seconds"]), 1):
                    row = dict(shot)
                    if not str(row.get("image") or "").startswith("data:image/"):
                        image = _image_path(row.get("image"), source)
                        filename = f"{ordinal:04d}_{index:07d}{image.suffix}"
                        (directory / "screenshots").mkdir(exist_ok=True)
                        shutil.copy2(image, directory / "screenshots" / filename)
                        row["image"] = (final_directory / "screenshots" / filename).as_uri()
                    seconds = row["seconds"]
                    duration = max(duration, seconds)
                    row.update(seconds=offset + seconds, source_session_id=source.name)
                    minutes, shown_seconds = divmod(int(row["seconds"]), 60)
                    row["time"] = f"{minutes:02d}:{shown_seconds:02d}"
                    screenshots.append(row)
                for scene in session["scenes"]:
                    duration = max(duration, scene["end"])
                    scenes.append(dict(scene, id=len(scenes) + 1, source_scene_id=scene.get("id"),
                                       start=offset + scene["start"], end=offset + scene["end"],
                                       source_session_id=source.name))
                frame_end, count = _copy_frames(source, directory, offset, source.name)
                frame_count += count
                duration = max(duration, frame_end)
                retention = source / "frame-retention.json"
                if retention.is_file():
                    data = json.loads(retention.read_text(encoding="utf-8"))
                    frames_dropped += _number(data.get("dropped_by_limit"))
                evidence = source / "evidence.json"
                if evidence.is_file():
                    for event in json.loads(evidence.read_text(encoding="utf-8")):
                        row = dict(event, id=len(events) + 1, source_session_id=source.name)
                        for key in ("time", "start", "end"):
                            if key in row:
                                row[key] = offset + _number(row[key])
                        events.append(row)
                tracker, audit_end = _copy_audit(source, audit, offset, source.name)
                duration = max(duration, audit_end)
                quality = (session.get("quality") if session.get("merged_from") else
                           tracker.summary(duration) if tracker is not None else session.get("quality"))
                part = dict(id=source.name, name=session.get("name") or source.name, start=offset,
                            end=offset + duration, duration=duration, started_at=session.get("started_at"),
                            device=session["device"], target=session["target"], quality=quality,
                            capture_settings=session.get("capture_settings"), telemetry=session.get("telemetry"),
                            merged_from=session.get("merged_from", []))
                parts.append(part)
                changes.append(dict(elapsed=offset, settings=session.get("capture_settings") or {},
                                    source_session_id=source.name))
                for change in session.get("capture_settings_changes") or []:
                    changes.append(dict(change, elapsed=offset + _number(change.get("elapsed")),
                                        source_session_id=source.name))
                offset += duration
                # Drop the large per-metric lists before decoding the next file.
                del session
        name = normalize_session_name(session_name) or normalize_session_name(
            f"合并 {len(parts)} 条 · {parts[0]['name']}")
        manifest = dict(capture_settings=first.get("capture_settings") or {},
                        capture_settings_changes=changes)
        quality = _quality(parts, offset)
        if quality is not None:
            manifest["quality"] = quality
        audit_path = directory / "audit-merged.jsonl"
        if audit_path.stat().st_size:
            manifest["audit_file"] = "audit.jsonl"
        _write_json_atomic(directory / LIVE_MANIFEST_NAME, manifest)
        if events:
            _write_json_atomic(directory / "evidence.json", events)
        if frame_count or frames_dropped:
            _write_json_atomic(directory / "frame-retention.json", dict(
                count=frame_count, dropped_by_limit=frames_dropped, merged=True,
                missing_frames_not_reconstructed=True))
        telemetry = deepcopy(first.get("telemetry") or {})
        telemetry["merge"] = dict(source_count=len(parts), timeline="concatenated_capture_durations",
                                 metadata_source="first_record", source_ids=identifiers)
        integrities = [(part.get("telemetry") or {}).get("collector", {}).get("integrity", {}) for part in parts]
        if any(integrities):
            integrity = telemetry.setdefault("collector", {}).setdefault("integrity", {})
            integrity["queue_overflow_rows"] = sum(_number(row.get("queue_overflow_rows")) for row in integrities)
            integrity["etw_loss_reported"] = any(row.get("etw_loss_reported") for row in integrities)
        device = dict(first["device"], platform=platform)
        save_session_from_spool(staging, device, first["target"], sample_files, screenshots,
                                enabled, session_id, scenes,
                                first.get("started_at") or first.get("created_at") or "", offset, name,
                                end_reason="多条采集记录顺序合并", telemetry=telemetry, merged_from=parts)
        if audit_path.stat().st_size:
            audit_path.replace(directory / "audit.jsonl")
        else:
            audit_path.unlink()
        # Fix the small sidecar's path before publishing the completed folder.
        summary_path = directory / SUMMARY_NAME
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["path"] = str(final_directory / "session.json")
        _write_json_atomic(summary_path, summary)
        directory.rename(final_directory)
        return final_directory / "session.json"
    finally:
        # staging was constructed directly under the resolved sessions root.
        if staging.resolve().parent == final_directory.parent and staging.is_dir():
            shutil.rmtree(staging)
