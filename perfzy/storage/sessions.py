from __future__ import annotations

import json
import math
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from ..domain.validation import metric_value_is_plausible
from ..domain.quality import QualityTracker, json_reading


SUMMARY_NAME = "summary.json"
METADATA_NAME = "metadata.json"
LIVE_SAMPLES_DIR = "samples-live"
LIVE_MANIFEST_NAME = "live-session.json"
DISCARD_MARKER_NAME = ".discarded"
SCENES_NAME = "scenes.json"


def _session_directory(root: Path, session_id: object) -> Path:
    """Resolve one session directory without permitting path traversal."""
    identifier = str(session_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise ValueError("采集记录编号无效")
    sessions_root = (Path(root) / "data" / "sessions").resolve()
    session_dir = (sessions_root / identifier).resolve()
    if session_dir.parent != sessions_root:
        raise ValueError("采集记录目录越界")
    return session_dir


def _decode_live_point(line: str, metric_key: str = "") -> dict[str, float] | None:
    """Return one finite x/y point, ignoring a crash-truncated JSONL row."""
    try:
        raw = json.loads(line)
        x, y = float(raw["x"]), float(raw["y"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not math.isfinite(x) or not math.isfinite(y) or x < 0:
        return None
    if metric_key and not metric_value_is_plausible(metric_key, y):
        return None
    return {"x": x, "y": y}


class LiveSessionWriter:
    """Append live points per metric without retaining the full capture in RAM."""

    def __init__(self, root: Path, session_id: str, flush_every: int = 5,
                 manifest: dict | None = None, sample_interval: float | None = None) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(session_id)):
            raise ValueError("采集记录编号无效")
        self.session_id = str(session_id)
        self.directory = root / "data" / "sessions" / self.session_id / LIVE_SAMPLES_DIR
        self.directory.mkdir(parents=True, exist_ok=True)
        self._streams: dict[str, TextIO] = {}
        self._paths: dict[str, Path] = {}
        self._writes_since_flush = 0
        self._flush_every = max(1, int(flush_every))
        self._closed = False
        self._manifest: dict = {}
        self._quality = QualityTracker()
        self._audit = None
        from ..studio.storage import RecordExtras
        self.extras = RecordExtras(root, self.directory.parent)
        if sample_interval is not None and math.isfinite(float(sample_interval)) and float(sample_interval)>0:
            self.extras.monitor.options['interval']=float(sample_interval)
        if manifest is not None:
            self.checkpoint(manifest)

    @property
    def sample_files(self) -> dict[str, Path]:
        return dict(self._paths)

    def append(self, elapsed: float, values: dict[str, float | None],
               *, observed_elapsed=None, frame_events=(), raw_values=None,
               adjustments=None, sources=None, requested=None, collector_ms=0,
               held_keys=()) -> None:
        if self._closed:
            raise RuntimeError("实时样本文件已经关闭")
        try:
            point_time = float(elapsed)
        except (TypeError, ValueError):
            return
        if not math.isfinite(point_time) or point_time < 0:
            return
        requested = sorted(set(requested if requested is not None else values))
        allowed = set(requested)
        values = {key: value for key, value in values.items() if key in allowed}
        raw_values = {key: value for key, value in (raw_values or {}).items()
                      if key in allowed or key == 'long_frame_max_ms'}
        adjustments = {key: value for key, value in (adjustments or {}).items() if key in allowed}
        sources = {key: value for key, value in (sources or {}).items() if key in allowed}
        observed_elapsed = {key: value for key, value in (observed_elapsed or {}).items() if key in allowed}
        held_keys = set(held_keys) & allowed
        changes = self._manifest.get('capture_settings_changes') or []
        settings = changes[-1]['settings'] if changes else self._manifest.get('capture_settings') or {}
        try:
            interval = float(settings.get('sample_interval_ms')) / 1000
        except (ValueError, TypeError):
            interval = None
        self._quality.record(point_time, values, requested, adjustments, collector_ms, interval,
                             observed_elapsed=observed_elapsed, held_keys=held_keys)
        if self._audit is None:
            self._audit = (self.directory.parent / 'audit.jsonl').open(
                'a', encoding='utf-8', newline='\n', buffering=64 * 1024)
        audit = dict(elapsed=point_time, requested=requested,
                     raw_values={str(k): json_reading(v) for k,v in {**values, **(raw_values or {})}.items()},
                     values={str(k): json_reading(v) for k,v in values.items()},
                     adjustments=dict(adjustments or {}), sources=dict(sources or {}),
                     observed_elapsed={str(k): json_reading(v) for k,v in (observed_elapsed or {}).items()},
                     held_keys=sorted(held_keys), collector_ms=json_reading(collector_ms),
                     interval_seconds=json_reading(interval))
        self._audit.write(json.dumps(audit, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n')
        wrote = True
        for key, value in values.items():
            if key in held_keys:
                continue
            try:
                metric_time = float((observed_elapsed or {}).get(key, point_time))
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(metric_time) or metric_time < 0:
                continue
            if value is None:
                continue
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(numeric_value):
                continue
            key = str(key)
            if not re.fullmatch(r"[A-Za-z0-9_]+", key):
                continue
            if not metric_value_is_plausible(key, numeric_value):
                continue
            stream = self._streams.get(key)
            if stream is None:
                path = self.directory / f"{key}.jsonl"
                stream = path.open("a", encoding="utf-8", newline="\n", buffering=64 * 1024)
                self._streams[key] = stream
                self._paths[key] = path
            stream.write(json.dumps({"x": metric_time, "y": numeric_value},
                                    ensure_ascii=False, separators=(",", ":")) + "\n")
            wrote = True
        if wrote:
            self._writes_since_flush += 1
        if self._writes_since_flush >= self._flush_every:
            self.flush()
        self.extras.append(point_time, {k:v for k,v in values.items() if k not in held_keys}, frame_events)

    def flush(self) -> None:
        if self._audit is not None:
            self._audit.flush()
        for stream in self._streams.values():
            stream.flush()
        self._writes_since_flush = 0

    def checkpoint(self, manifest: dict) -> None:
        """Persist the small recovery manifest independently of sample files."""
        self._manifest.update(manifest)
        payload = dict(self._manifest)
        payload["id"] = self.session_id
        payload["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _write_json_atomic(self.directory.parent / LIVE_MANIFEST_NAME, payload)

    def close(self) -> dict[str, Path]:
        if not self._closed:
            try:
                self.flush()
                self.checkpoint({'quality': self._quality.summary(), 'audit_file': 'audit.jsonl'})
            finally:
                for stream in self._streams.values():
                    stream.close()
                self.extras.close()
                if self._audit is not None:
                    self._audit.close()
                self._closed = True
        return self.sample_files


def normalize_session_name(value: object) -> str:
    """Return a compact user-facing name without rewriting large session data."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:80]


def _apply_metadata(payload: dict, session_path: Path) -> dict:
    # Scene edits live in a small atomic sidecar; performance evidence stays intact.
    try:
        annotations = json.loads((session_path.parent / SCENES_NAME).read_text(encoding="utf-8"))
        if isinstance(annotations, dict) and isinstance(annotations.get("scenes"), list):
            payload["scenes"] = annotations["scenes"]
    except (OSError, ValueError):
        pass
    metadata_path = session_path.parent / METADATA_NAME
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return payload
    if not isinstance(metadata, dict):
        return payload
    name = normalize_session_name(metadata.get("name"))
    if name:
        payload["name"] = name
    return payload


def _record_from_payload(payload: dict, path: Path) -> dict:
    device_value = payload.get("device")
    target_value = payload.get("target")
    samples_value = payload.get("samples")
    device = device_value if isinstance(device_value, dict) else {}
    target = target_value if isinstance(target_value, dict) else {}
    samples = samples_value if isinstance(samples_value, dict) else {}
    try:
        duration = float(payload.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if not math.isfinite(duration) or duration < 0:
        duration = 0.0
    if duration <= 0:
        for rows in samples.values():
            if not isinstance(rows, list) or not rows or not isinstance(rows[-1], dict):
                continue
            try:
                candidate = float(rows[-1].get("x", 0.0))
            except (TypeError, ValueError):
                continue
            if math.isfinite(candidate):
                duration = max(duration, candidate)
    raw_time = payload.get("started_at") or payload.get("created_at") or ""
    try:
        shown_time = datetime.fromisoformat(raw_time).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        shown_time = str(raw_time).replace("T", " ")[:19]
    record_time = (payload.get("created_at") if payload.get("merged_from") else raw_time) or raw_time
    try:
        recorded = datetime.fromisoformat(record_time).astimezone()
        recorded_time = recorded.strftime("%Y-%m-%d %H:%M:%S")
        sort_timestamp = recorded.timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        recorded_time, sort_timestamp = shown_time, 0.0
    minutes, seconds = divmod(max(0, int(round(duration))), 60)
    model = str(device.get("model") or "").strip()
    device_name = str(device.get("name") or device.get("platform") or "未知设备").strip()
    return {
        "id": str(payload.get("id") or path.parent.name),
        "name": normalize_session_name(payload.get("name")) or str(target.get("name") or path.parent.name),
        "application": str(target.get("name") or "未知应用"),
        "device": f"{device_name} / {model}" if model and model not in device_name else device_name,
        "deviceName": device_name,
        "deviceModel": model,
        "deviceOS": str(device.get("os") or device.get("os_version") or "").strip(),
        "deviceCPU": str(device.get("cpu") or "").strip(),
        "deviceMemory": str(device.get("memory") or "").strip(),
        "platform": str(device.get("platform") or "").strip(),
        "startedAt": shown_time,
        "recordedAt": recorded_time,
        "sortTimestamp": sort_timestamp,
        "duration": f"{minutes:02d}:{seconds:02d}",
        "mergeCount": len(payload.get("merged_from") or []),
        "status": "已保存到本地",
        "path": str(path),
    }


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Stream JSON to disk so large sessions do not need a second full-size string."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _capture_metadata(directory: Path, duration=None) -> dict:
    try:
        manifest = json.loads((directory / LIVE_MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    result = {key: manifest[key] for key in ("capture_settings", "quality", "audit_file",
                                           "capture_settings_changes", "capture_settings_changes_truncated")
              if isinstance(manifest, dict) and key in manifest}
    audit_path = directory / "audit.jsonl"
    if audit_path.is_file():
        # Streaming is essential for multi-hour sessions.
        tracker = QualityTracker()
        with audit_path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    elapsed = float(row["elapsed"])
                    if math.isfinite(elapsed) and elapsed >= 0:
                        tracker.record(elapsed, row["values"], row["requested"],
                                       row.get("adjustments"), row.get("collector_ms"), row.get("interval_seconds"),
                                       observed_elapsed=row.get("observed_elapsed"), held_keys=row.get("held_keys") or ())
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
        result.update(quality=tracker.summary(duration), audit_file="audit.jsonl")
    return result


def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:6]


def save_session(root: Path, device: dict, target: dict, samples: dict,
                 screenshots: list, enabled_metrics: set[str], session_id: str = "",
                 scenes: list | None = None, started_at: str = "", duration: float = 0.0,
                 session_name: str = "", *, end_reason: str = "", telemetry: dict | None = None,
                 capture_settings: dict | None = None) -> Path:
    session_id = session_id or new_session_id()
    destination = _session_directory(root, session_id) / "session.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "telemetry": dict(telemetry or {}),
        "end_reason": end_reason,
        "id": session_id,
        "name": normalize_session_name(session_name),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "started_at": started_at,
        "duration": float(duration),
        "device": device,
        "target": target,
        "enabled_metrics": sorted(enabled_metrics),
        "samples": samples,
        "screenshots": screenshots,
        "scenes": list(scenes or []),
    }
    payload.update(_capture_metadata(destination.parent, duration))
    if capture_settings is not None and "capture_settings" not in payload:
        payload["capture_settings"] = dict(capture_settings)
    _write_json_atomic(destination, payload)
    # History only needs a few fields. A tiny sidecar avoids reparsing every
    # sample and screenshot whenever the history page refreshes.
    summary = _record_from_payload(payload, destination)
    summary["session_mtime_ns"] = destination.stat().st_mtime_ns
    _write_json_atomic(destination.parent / SUMMARY_NAME, summary)
    return destination


def save_session_from_spool(root: Path, device: dict, target: dict,
                            sample_files: dict[str, Path], screenshots: list,
                            enabled_metrics: set[str], session_id: str,
                            scenes: list | None = None, started_at: str = "",
                            duration: float = 0.0, session_name: str = "", *, end_reason: str = "",
                            telemetry: dict | None = None, capture_settings: dict | None = None,
                            merged_from: list[dict] | None = None) -> Path:
    """Finalize a classic session.json by streaming per-metric live files."""
    destination = _session_directory(root, session_id) / "session.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    header = {
        "telemetry": dict(telemetry or {}),
        "end_reason": end_reason,
        "schema": 1,
        "id": str(session_id),
        "name": normalize_session_name(session_name),
        "created_at": datetime.now().astimezone().isoformat(timespec="microseconds" if merged_from else "seconds"),
        "started_at": started_at,
        "duration": float(duration),
        "device": device,
        "target": target,
        "enabled_metrics": sorted(enabled_metrics),
    }
    header.update(_capture_metadata(destination.parent, duration))
    if merged_from is not None:
        header["merged_from"] = merged_from
    if capture_settings is not None and "capture_settings" not in header:
        header["capture_settings"] = dict(capture_settings)
    try:
        with temporary.open("w", encoding="utf-8", newline="") as output:
            prefix = json.dumps(header, ensure_ascii=False, separators=(",", ":"))
            output.write(prefix[:-1] + ',"samples":{')
            first_metric = True
            for key, path in sorted(sample_files.items()):
                if not path.is_file():
                    continue
                metric_started = False
                first_point = True
                with path.open("r", encoding="utf-8") as source:
                    for line in source:
                        point = _decode_live_point(line, str(key))
                        if point is None:
                            continue
                        if not metric_started:
                            if not first_metric:
                                output.write(",")
                            first_metric = False
                            metric_started = True
                            output.write(json.dumps(str(key), ensure_ascii=False) + ":[")
                        if not first_point:
                            output.write(",")
                        first_point = False
                        json.dump(point, output, ensure_ascii=False, separators=(",", ":"))
                if metric_started:
                    output.write("]")
            output.write('},"screenshots":')
            json.dump(screenshots, output, ensure_ascii=False, separators=(",", ":"))
            output.write(',"scenes":')
            json.dump(list(scenes or []), output, ensure_ascii=False, separators=(",", ":"))
            output.write("}")
            output.flush()
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    summary_payload = dict(header, samples={}, screenshots=screenshots, scenes=list(scenes or []))
    summary = _record_from_payload(summary_payload, destination)
    summary["session_mtime_ns"] = destination.stat().st_mtime_ns
    _write_json_atomic(destination.parent / SUMMARY_NAME, summary)
    spool_dir = destination.parent / LIVE_SAMPLES_DIR
    if spool_dir.is_dir():
        shutil.rmtree(spool_dir)
    manifest_path = destination.parent / LIVE_MANIFEST_NAME
    if manifest_path.is_file():
        manifest_path.unlink()
    return destination


def recover_incomplete_sessions(root: Path, session_ids: set[str] | None = None) -> list[Path]:
    """Finalize recoverable live spools left behind by an abnormal exit."""
    sessions_root = root / "data" / "sessions"
    if not sessions_root.is_dir():
        return []
    # A user-selected "do not save" session must never reappear as recovered
    # data. Delayed workers may keep the marker directory alive until process
    # exit, so it is cleaned safely on the next launch.
    for session_dir in sessions_root.iterdir():
        if session_dir.is_dir() and (session_dir / DISCARD_MARKER_NAME).is_file():
            try:
                shutil.rmtree(session_dir)
            except OSError:
                pass
    recovered: list[Path] = []
    for session_dir in sessions_root.iterdir():
        if session_ids is not None and session_dir.name not in session_ids:
            continue
        manifest_path = session_dir / LIVE_MANIFEST_NAME
        spool_dir = session_dir / LIVE_SAMPLES_DIR
        if (not session_dir.is_dir() or (session_dir / "session.json").is_file() or
                not manifest_path.is_file() or not spool_dir.is_dir()):
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        sample_files = {path.stem: path for path in spool_dir.glob("*.jsonl") if path.is_file()}
        if not sample_files:
            continue
        duration = 0.0
        for path in sample_files.values():
            try:
                with path.open("r", encoding="utf-8") as stream:
                    for line in stream:
                        point = _decode_live_point(line, path.stem)
                        if point is not None:
                            duration = max(duration, point["x"])
            except OSError:
                continue
        screenshots = []
        screenshot_dir = session_dir / "screenshots"
        if screenshot_dir.is_dir():
            for image in sorted(screenshot_dir.glob("*.jpg")):
                try:
                    seconds = int(image.stem) / 1000.0
                except ValueError:
                    continue
                minutes, shown_seconds = divmod(int(seconds), 60)
                screenshots.append({"image": image.resolve().as_uri(),
                                    "time": f"{minutes:02d}:{shown_seconds:02d}",
                                    "seconds": seconds})
        target_value = manifest.get("target")
        device_value = manifest.get("device")
        scene_value = manifest.get("scenes")
        target = dict(target_value) if isinstance(target_value, dict) else {}
        device = dict(device_value) if isinstance(device_value, dict) else {}
        scenes = list(scene_value) if isinstance(scene_value, list) else []
        enabled_value = manifest.get("enabled_metrics")
        enabled_metrics = ({str(key) for key in enabled_value}
                           if isinstance(enabled_value, (list, tuple, set)) else set())
        try:
            recovered.append(save_session_from_spool(
                root, device, target, sample_files,
                screenshots, enabled_metrics | set(sample_files),
                session_dir.name, scenes,
                str(manifest.get("started_at") or ""), duration,
                normalize_session_name(manifest.get("name")) or
                f"已恢复 {target.get('name') or '性能采集'}",
                telemetry=manifest.get('telemetry') if isinstance(manifest.get('telemetry'), dict) else {}))
        except (OSError, TypeError, ValueError):
            continue
    return recovered


def rename_session(root: Path, session_id: str, name: str) -> str:
    """Rename a session by updating only tiny metadata/summary sidecars."""
    cleaned = normalize_session_name(name)
    if not cleaned:
        raise ValueError("采集名称不能为空")
    session_path = _session_directory(root, session_id) / "session.json"
    if not session_path.is_file():
        raise FileNotFoundError(f"采集记录不存在：{session_id}")
    _write_json_atomic(session_path.parent / METADATA_NAME, {
        "name": cleaned,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    summary_path = session_path.parent / SUMMARY_NAME
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = json.loads(session_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("采集记录格式无效")
        payload = _apply_metadata(payload, session_path)
        summary = _record_from_payload(payload, session_path)
        summary["session_mtime_ns"] = session_path.stat().st_mtime_ns
    else:
        if not isinstance(summary, dict):
            payload = json.loads(session_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("采集记录格式无效")
            payload = _apply_metadata(payload, session_path)
            summary = _record_from_payload(payload, session_path)
        summary["name"] = cleaned
        summary["session_mtime_ns"] = session_path.stat().st_mtime_ns
    _write_json_atomic(summary_path, summary)
    return cleaned


def delete_session(root: Path, session_id: str) -> None:
    """Delete exactly one saved session directory, including its screenshots."""
    session_dir = _session_directory(root, session_id)
    if not (session_dir / "session.json").is_file():
        raise FileNotFoundError(f"采集记录不存在：{session_id}")
    shutil.rmtree(session_dir)


def discard_incomplete_session(root: Path, session_id: str) -> None:
    """Mark and remove one unsaved capture so startup recovery ignores it."""
    session_dir = _session_directory(root, session_id)
    if not session_dir.exists():
        return
    if (session_dir / "session.json").is_file():
        raise ValueError("已保存的采集记录不能按未保存数据丢弃")
    marker = session_dir / DISCARD_MARKER_NAME
    marker.write_text("discarded\n", encoding="utf-8")
    for path in (session_dir / LIVE_MANIFEST_NAME, session_dir / LIVE_SAMPLES_DIR,
                 session_dir / "screenshots", session_dir / "audit.jsonl", session_dir / 'frames.sqlite3',
                 session_dir / 'frames.sqlite3-journal', session_dir / 'evidence.json',
                 session_dir / 'frame-retention.json'):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError:
            # Keeping the marker is more important than immediate cleanup: the
            # next launch will retry without recovering this session.
            pass



def session_content_stamp(path: Path, session_stat=None) -> tuple[str, int]:
    """A small version stamp for reportable content, independent of record naming."""
    session_stat = path.stat() if session_stat is None else session_stat
    try:
        scenes = (path.parent / SCENES_NAME).stat()
        scenes_ns, scenes_size = scenes.st_mtime_ns, scenes.st_size
    except FileNotFoundError:
        scenes_ns, scenes_size = 0, 0
    revision = f"{session_stat.st_mtime_ns:x}-{session_stat.st_size:x}-{scenes_ns:x}-{scenes_size:x}"
    return revision, max(session_stat.st_mtime_ns, scenes_ns)


def load_session_records(root: Path) -> list[dict]:
    records: list[dict] = []
    sessions_root = root / "data" / "sessions"
    for path in sessions_root.glob("*/session.json") if sessions_root.exists() else ():
        summary_path = path.parent / SUMMARY_NAME
        try:
            session_stat = path.stat()
            revision, modified_ns = session_content_stamp(path, session_stat)
            source = dict(sourceRevision=revision, sourceModifiedNs=modified_ns)
            if summary_path.is_file():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                if (isinstance(summary, dict) and
                        all(key in summary for key in ("deviceName", "deviceModel", "deviceOS", "deviceCPU", "deviceMemory", "platform", "recordedAt", "sortTimestamp")) and
                        isinstance(summary.get("sortTimestamp"), (int, float)) and math.isfinite(summary["sortTimestamp"]) and
                        int(summary.get("session_mtime_ns", -1)) == session_stat.st_mtime_ns):
                    record = {key: value for key, value in summary.items()
                              if key != "session_mtime_ns"}
                    # The folder may have been copied to another PC.
                    record.update(source, path=str(path))
                    records.append(record)
                    continue
            decoded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(decoded, dict):
                continue
            payload = _apply_metadata(decoded, path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        try:
            record = _record_from_payload(payload, path)
            summary = dict(record, session_mtime_ns=session_stat.st_mtime_ns)
            _write_json_atomic(summary_path, summary)
        except OSError:
            record = _record_from_payload(payload, path)
        record.update(source)
        records.append(record)
    return sorted(records, key=lambda item: item["sortTimestamp"], reverse=True)


def load_session(root: Path, session_id: str) -> dict | None:
    try:
        path = _session_directory(root, session_id) / "session.json"
    except ValueError:
        return None
    try:
        # json.load avoids holding a second full-size UTF-8 string while a
        # long recording is decoded.
        with path.open("r", encoding="utf-8") as stream:
            decoded = json.load(stream)
        if not isinstance(decoded, dict):
            return None
        payload = _apply_metadata(decoded, path)
    except (OSError, json.JSONDecodeError):
        return None
    samples_value = payload.get("samples")
    samples = samples_value if isinstance(samples_value, dict) else {}
    for key in list(samples):
        rows = samples[key]
        if not isinstance(rows, list):
            samples.pop(key, None)
            continue
        write_index = 0
        ordered = True
        previous_x = -math.inf
        for item in rows:
            if not isinstance(item, dict):
                continue
            raw_x, raw_y = item.get("x"), item.get("y")
            try:
                x, y = float(raw_x), float(raw_y)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(x) or not math.isfinite(y) or x < 0:
                continue
            if not metric_value_is_plausible(key, y):
                continue
            ordered = ordered and x >= previous_x
            previous_x = x
            # Normal records already contain finite JSON numbers. Reuse their
            # dictionaries to keep opening a long session allocation-light;
            # only repaired string/foreign values need a replacement object.
            rows[write_index] = (item if isinstance(raw_x, (int, float)) and
                                 isinstance(raw_y, (int, float)) and
                                 not isinstance(raw_x, bool) and
                                 not isinstance(raw_y, bool)
                                 else {"x": x, "y": y})
            write_index += 1
        del rows[write_index:]
        if not ordered:
            rows.sort(key=lambda row: row["x"])
        if not rows:
            samples.pop(key, None)
    payload["samples"] = samples

    screenshots = []
    screenshot_value = payload.get("screenshots")
    screenshot_rows = screenshot_value if isinstance(screenshot_value, list) else []
    for index, item in enumerate(screenshot_rows):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        old_name = Path(str(row.get("image") or "")).name
        local = path.parent / "screenshots" / old_name if old_name else None
        if local and local.exists():
            row["image"] = local.resolve().as_uri()
        try:
            shot_seconds = float(row.get("seconds"))
        except (TypeError, ValueError):
            shot_seconds = math.nan
        if not math.isfinite(shot_seconds) or shot_seconds < 0:
            shown = str(row.get("time") or "0:00")
            try:
                minutes, seconds = shown.split(":", 1)
                shot_seconds = int(minutes) * 60 + float(seconds)
            except (ValueError, TypeError):
                shot_seconds = float(index)
        row["seconds"] = max(0.0, shot_seconds)
        screenshots.append(row)
    payload["screenshots"] = screenshots
    scene_value = payload.get("scenes")
    scenes = []
    for item in scene_value if isinstance(scene_value, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            start, end = float(item.get("start")), float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        row = dict(item)
        row["start"], row["end"] = max(0.0, start), max(0.0, end)
        if row["end"] > row["start"]:
            scenes.append(row)
    payload["scenes"] = scenes
    for object_key in ("device", "target"):
        if not isinstance(payload.get(object_key), dict):
            payload[object_key] = {}
    payload["session_path"] = str(path)
    return payload
