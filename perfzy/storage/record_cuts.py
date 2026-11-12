"""Publish independent trimmed recordings using a single compacted time axis."""
from contextlib import closing
from datetime import datetime
import json
import logging
from pathlib import Path
import shutil
import sqlite3

from ..domain.quality import QualityTracker
from ..domain.record_timeline import TimelineEdit, finite_time
from .merging import _image_path
from .record_overwrite import RecordOverwrite
from .sessions import (SUMMARY_NAME, _record_from_payload, _session_directory,
                       _write_json_atomic, load_session, new_session_id,
                       normalize_session_name)


def _observation_time(value):
    try: return finite_time(value)
    except (TypeError,ValueError,OverflowError): return None


def _frames(source, destination, timeline, identifier):
    path = source / "frames.sqlite3"
    if not path.is_file():
        return
    count = 0
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True)) as incoming:
        with closing(sqlite3.connect(destination / path.name)) as output:
            output.execute("CREATE TABLE frames(seq INTEGER PRIMARY KEY,x REAL NOT NULL,ms REAL NOT NULL,payload TEXT NOT NULL)")
            output.execute("CREATE INDEX frames_time ON frames(x)")
            output.execute("CREATE INDEX frames_slowest ON frames(ms DESC,x)")
            for ordinal, segment in enumerate(timeline.kept):
                end_operator = "<=" if segment["end"] == timeline.duration else "<"
                cursor = incoming.execute(f"SELECT x,ms,payload FROM frames WHERE x>=? AND x{end_operator}? ORDER BY x,seq",
                                          (segment["start"], segment["end"]))
                while batch := cursor.fetchmany(4096):
                    rows = []
                    for x, ms, encoded in batch:
                        if timeline.contains(x): continue
                        frame = json.loads(encoded)
                        stamp = timeline.map_time(x)
                        frame["x"] = stamp
                        # Raw-frame reconstruction must reset across each splice,
                        # while source identifiers and original QPC clocks stay intact.
                        frame["stream_epoch"] = f"{frame.get('stream_epoch','')}:edit:{identifier}:{ordinal}"
                        rows.append((stamp, ms, json.dumps(frame, ensure_ascii=False, allow_nan=False)))
                    output.executemany("INSERT INTO frames(x,ms,payload) VALUES(?,?,?)", rows)
                    count += len(rows)
            output.commit()
    retention = source / "frame-retention.json"
    details = json.loads(retention.read_text(encoding="utf-8")) if retention.is_file() else {}
    details.update(count=count, edited=True, missing_frames_not_reconstructed=True)
    _write_json_atomic(destination / retention.name, details)


def _audit(source, destination, payload, timeline):
    filename = str(payload.get("audit_file") or "audit.jsonl")
    if Path(filename).name != filename:
        raise ValueError("采集审计文件路径无效")
    path = source / filename
    if not path.is_file():
        payload.pop("quality", None)
        payload.pop("audit_file", None)
        return
    tracker = QualityTracker()
    with path.open(encoding="utf-8") as incoming, (destination / "audit.jsonl").open("w",encoding="utf-8",newline="\n") as output:
        for line in incoming:
            if not line.strip(): continue
            row = json.loads(line)
            elapsed = finite_time(row["elapsed"])
            if timeline.contains(elapsed): continue
            observed = row.get("observed_elapsed") or {}
            removed = {key for key, stamp in observed.items()
                       if _observation_time(stamp) is not None and timeline.contains(stamp)}
            for field in ("values","raw_values","adjustments"):
                if isinstance(row.get(field),dict):
                    row[field] = {key:value for key,value in row[field].items() if key not in removed}
            row["observed_elapsed"] = {key:timeline.map_time(stamp) if _observation_time(stamp) is not None else stamp
                                       for key,stamp in observed.items() if key not in removed}
            for field in ("requested","held_keys"):
                if isinstance(row.get(field),list):
                    row[field] = [key for key in row[field] if key not in removed]
            if row.get("interval_seconds") is not None:
                row["interval_seconds"] = timeline.observation_interval(elapsed,row["interval_seconds"])
            row["elapsed"] = timeline.map_time(elapsed)
            tracker.record(row["elapsed"],row.get("values") or {},row.get("requested") or [],
                           row.get("adjustments"),row.get("collector_ms"),row.get("interval_seconds"),
                           observed_elapsed=row["observed_elapsed"],held_keys=row.get("held_keys") or ())
            output.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n")
    payload["quality"] = tracker.summary(timeline.edited_duration)
    payload["audit_file"] = "audit.jsonl"


def _events(rows, timeline):
    events = []
    for event in rows:
        first = finite_time(event.get("start",event.get("elapsed",event.get("x",0))))
        last = finite_time(event.get("end",first))
        intersects = (timeline.contains(first) if first == last else
                      any(first < span["end"] and last > span["start"] for span in timeline.ranges))
        if intersects: continue
        row = dict(event)
        for key in ("start","end","elapsed","x"):
            if key in row: row[key] = timeline.map_time(row[key])
        events.append(row)
    return events


def _load(root, session_id):
    payload = load_session(root,session_id)
    if payload is None: raise FileNotFoundError("无法读取原采集记录")
    duration = max(finite_time(payload.get("duration") or 0),
        max((row["x"] for rows in payload["samples"].values() for row in rows),default=0),
        max((row["seconds"] for row in payload["screenshots"]),default=0))
    return payload,duration


def _publish(root, session_id, payload, timeline, transaction=None):
    source = _session_directory(root,session_id)
    identifier = session_id if transaction else new_session_id()
    job = transaction.token if transaction else identifier
    final = _session_directory(root,identifier)
    staging = final.parent / ("_edit_"+job)
    staging.mkdir(parents=True,exist_ok=False)
    directory = staging / identifier
    directory.mkdir()
    published = False
    try:
        if transaction:
            _write_json_atomic(staging/"edit-owner.json",dict(session_id=session_id,token=job))
            # Keep user-added attachments; canonical capture files below are
            # rebuilt from the retained data instead of copying stale evidence.
            canonical = {"session.json","summary.json","metadata.json","scenes.json","live-session.json",
                         "frames.sqlite3","frames.sqlite3-wal","frames.sqlite3-shm","frames.sqlite3-journal",
                         "audit.jsonl","evidence.json","frame-retention.json","samples-live","screenshots",".discarded"}
            for item in source.iterdir():
                if item.name in canonical: continue
                if item.is_dir(): shutil.copytree(item,directory/item.name,symlinks=True)
                else: shutil.copy2(item,directory/item.name,follow_symlinks=False)
        for key, rows in payload["samples"].items():
            payload["samples"][key] = [dict(row,x=timeline.map_time(row["x"])) for row in rows if not timeline.contains(row["x"])]
        if not any(payload["samples"].values()):
            raise ValueError("所选范围包含全部有效数据，未生成空记录；请缩小范围")
        shots = []
        for index, shot in enumerate(payload["screenshots"]):
            if timeline.contains(shot["seconds"]): continue
            path = _image_path(shot.get("image"),source)
            shot_directory = directory / "screenshots"
            shot_directory.mkdir(exist_ok=True)
            filename = f"shot_{job}_{index:06d}{path.suffix}" if transaction else f"shot_{index:06d}{path.suffix}"
            shutil.copy2(path,shot_directory / filename)
            stamp = timeline.map_time(shot["seconds"])
            row = dict(shot,seconds=stamp,time=f"{int(stamp)//60:02d}:{stamp%60:05.2f}",
                       image=(final / "screenshots" / filename).resolve().as_uri())
            if "elapsed" in row: row["elapsed"] = stamp
            shots.append(row)
        payload["screenshots"] = shots
        payload["scenes"] = timeline.scenes(payload.get("scenes") or [])
        payload["capture_settings"],payload["capture_settings_changes"] = timeline.capture_settings(
            payload.get("capture_settings"),payload.get("capture_settings_changes"))
        payload["timeline_cuts"] = timeline.cuts(payload.get("timeline_cuts"),session_id)
        payload["excluded_intervals"] = []
        payload["duration"] = timeline.edited_duration
        _frames(source,directory,timeline,job)
        _audit(source,directory,payload,timeline)
        evidence = source / "evidence.json"
        if evidence.is_file():
            _write_json_atomic(directory / evidence.name,_events(json.loads(evidence.read_text(encoding="utf-8")),timeline))
        if isinstance(payload.get("annotations"),list):
            payload["annotations"] = _events(payload["annotations"],timeline)
        parts = []
        for part in payload.get("merged_from") or []:
            first,last = timeline.map_time(part["start"]),timeline.map_time(part["end"])
            if last <= first: continue
            row = dict(part,start=first,end=last,duration=last-first)
            if row.get("quality"):
                row["source_quality"] = row.pop("quality")
            parts.append(row)
        if "merged_from" in payload: payload["merged_from"] = parts
        history = list(payload.get("edit_history") or [])
        if payload.get("edited_from") and not history:
            history.append(payload["edited_from"])
        edit = dict(id=session_id,removed_ranges=timeline.ranges,source_duration=timeline.duration,
                    duration=timeline.edited_duration,timeline="compacted",scenes="retimed_and_clipped",
                    save_mode="overwrite" if transaction else "copy",revision=job)
        history.append(edit)
        payload.update(id=identifier,edited_from=edit,edit_history=history,
            timeline_map=[dict(source_start=segment["start"],source_end=segment["end"],
                               start=timeline.map_time(segment["start"]),end=timeline.map_time(segment["end"])) for segment in timeline.kept])
        if not transaction:
            payload["created_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        name = payload.get("name") or payload.get("target",{}).get("name") or "采集记录"
        if not transaction and not name.endswith(" · 剪辑"): name += " · 剪辑"
        payload["name"] = normalize_session_name(name)
        payload.pop("session_path",None)
        notes = [note for note in payload.get("notes") or [] if not str(note).startswith(("用户编辑：移除 ","剪辑副本：","剪辑记录："))]
        label = "剪辑记录" if transaction else "剪辑副本"
        notes.append(f"{label}：移除 {len(timeline.ranges)} 段共 {timeline.removed_seconds:.2f} 秒，前后已拼接。显示时长为剪辑后的 {timeline.edited_duration:.2f} 秒，细线标示剪辑交界。")
        payload["notes"] = notes
        _write_json_atomic(directory / "session.json",payload)
        summary = _record_from_payload(payload,final / "session.json")
        summary["session_mtime_ns"] = (directory / "session.json").stat().st_mtime_ns
        _write_json_atomic(directory / SUMMARY_NAME,summary)
        if transaction:
            transaction.publish(staging,directory)
        else:
            directory.rename(final)
        published = True
        return final / "session.json"
    finally:
        if (staging.resolve().parent == final.parent.resolve() and staging.name == "_edit_"+job and staging.is_dir()
                and (transaction is None or not transaction.pending)):
            try:
                shutil.rmtree(staging)
            except OSError:
                if not published: raise
                logging.getLogger(__name__).warning("记录已保存，临时文件未能完全清理：%s",staging)


def remove_time_range(root: Path, session_id: str, first: float, last: float, *, create_copy=True) -> Path:
    if not create_copy:
        with RecordOverwrite(root,session_id) as transaction:
            payload,duration = _load(root,session_id)
            timeline = TimelineEdit(duration,[*(payload.get("excluded_intervals") or []),dict(start=first,end=last)])
            return _publish(root,session_id,payload,timeline,transaction)
    payload,duration = _load(root,session_id)
    timeline = TimelineEdit(duration,[*(payload.get("excluded_intervals") or []),dict(start=first,end=last)])
    return _publish(root,session_id,payload,timeline)


def compact_removed_ranges(root: Path, session_id: str, *, create_copy=True) -> Path:
    """Upgrade an existing gap-style copy without altering that copy or its source."""
    if not create_copy:
        with RecordOverwrite(root,session_id) as transaction:
            payload,duration = _load(root,session_id)
            timeline = TimelineEdit(duration,payload.get("excluded_intervals") or [])
            return _publish(root,session_id,payload,timeline,transaction)
    payload,duration = _load(root,session_id)
    timeline = TimelineEdit(duration,payload.get("excluded_intervals") or [])
    return _publish(root,session_id,payload,timeline)
