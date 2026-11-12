"""Scene annotations and non-destructive saved-record time edits."""
from datetime import datetime
import math
from pathlib import Path
import re

from .sessions import SCENES_NAME, _session_directory, _write_json_atomic, normalize_session_name
from .record_cuts import compact_removed_ranges, remove_time_range


def _time(value):
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("时间必须是非负的有效数值")
    return result


def save_scene_annotations(root: Path, session_id: str, scenes: list, duration: float) -> None:
    directory = _session_directory(root, session_id)
    if not (directory / "session.json").is_file():
        raise FileNotFoundError("原采集记录已不存在，场景未保存")
    duration = _time(duration)
    cleaned, identifiers = [], set()
    for scene in scenes:
        first, last = _time(scene["start"]), _time(scene["end"])
        if not 0 <= first < last <= duration + .001:
            raise ValueError("场景时间段必须位于采集记录范围内")
        identifier = scene["id"]
        if not isinstance(identifier, (str, int)) or identifier in identifiers:
            raise ValueError("场景编号重复或无效")
        identifiers.add(identifier)
        name = normalize_session_name(scene.get("name"))
        if not name:
            raise ValueError("请输入场景名称")
        color = str(scene.get("color") or "#4269d4")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?", color):
            raise ValueError("场景颜色无效")
        cleaned.append(dict(scene, start=first, end=last, name=name, color=color))
    _write_json_atomic(directory / SCENES_NAME, dict(schema=1, scenes=cleaned,
        updated_at=datetime.now().astimezone().isoformat(timespec="seconds")))
