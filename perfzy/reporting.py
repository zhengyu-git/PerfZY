from __future__ import annotations

import html
import base64
import io
import json
import math
import mimetypes
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from PIL import Image

from .domain.metrics import METRIC_BY_KEY, platform_metric_title, platform_metric_note
from .domain.models import Platform
from .domain.validation import metric_value_is_plausible
from .domain.collection_quality import collection_quality_notes
from .domain.capture_configuration import capture_request_summary
from .domain.provenance import metric_provenance, canonical_platform
from .application.charts import CHART_GROUPS, group_key_for_metric, chart_title
from .runtime_paths import RESOURCE_ROOT
from .report_ui import report_theme
from .storage.sessions import _apply_metadata, normalize_session_name, session_content_stamp


_REPORT_TEMPLATE_V10 = RESOURCE_ROOT / "assets" / "report_template_v10.html"


def safe_report_file_stem(value: object, fallback: str = "performance-report") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")[:80]
    cleaned = cleaned or fallback
    reserved = {"CON", "PRN", "AUX", "NUL",
                *(f"COM{index}" for index in range(1, 10)),
                *(f"LPT{index}" for index in range(1, 10))}
    if cleaned.partition(".")[0].upper() in reserved:
        cleaned = f"_{cleaned}"
    return cleaned


def _local_image_path(value: Any) -> str:
    text = str(value or "")
    if text.startswith("file:"):
        parsed = urlparse(text)
        path = unquote(parsed.path)
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return path
    return text


def _legacy_session(session: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, str]], list[tuple[str, str, tuple[str, ...]]]]:
    """Translate PerfZY's per-metric x/y storage into the original report schema."""
    timeline: dict[float, dict[str, float]] = {}
    platform_value = canonical_platform(session.get('device') or {})
    platform = Platform(platform_value) if platform_value else None
    used_keys: list[str] = []
    # Report generation owns this decoded session. Consume the largest lists as
    # they are transformed so peak memory does not retain both storage schemas.
    sample_value = session.pop("samples", {})
    scene_value = session.pop("scenes", [])
    screenshot_value = session.pop("screenshots", [])
    sample_groups = sample_value if isinstance(sample_value, dict) else {}
    scenes = scene_value if isinstance(scene_value, list) else []
    source_screenshots = screenshot_value if isinstance(screenshot_value, list) else []
    for key in list(sample_groups):
        points = sample_groups.pop(key)
        has_values = False
        for point in points if isinstance(points, list) else []:
            if not isinstance(point, dict):
                continue
            elapsed, value = _number(point.get("x")), _number(point.get("y"))
            if (elapsed is None or elapsed < 0 or value is None or
                    not metric_value_is_plausible(key, value)):
                continue
            stamp = round(elapsed, 6)
            timeline.setdefault(stamp, {"elapsed": elapsed})[str(key)] = value
            has_values = True
        if has_values:
            used_keys.append(str(key))
    rows = sorted(timeline.values(), key=lambda row: row["elapsed"])
    metric_meta = {
        key: {
            "title": platform_metric_title(key, platform) if platform else METRIC_BY_KEY[key].title if key in METRIC_BY_KEY else key,
            "unit": METRIC_BY_KEY[key].unit if key in METRIC_BY_KEY else "",
            "color": METRIC_BY_KEY[key].color if key in METRIC_BY_KEY else "#5576f7",
        }
        for key in used_keys
    }
    grouped: dict[str, list[str]] = {}
    for key in used_keys:
        grouped.setdefault(group_key_for_metric(key, platform) or "other", []).append(key)
    groups: list[tuple[str, str, tuple[str, ...]]] = [
        (group.key, chart_title(group, platform), tuple(grouped[group.key]))
        for group in CHART_GROUPS if grouped.get(group.key)
    ]
    for group_key, keys in grouped.items():
        if group_key not in {group.key for group in CHART_GROUPS}:
            groups.append((group_key, group_key.replace("_", " ").title(), tuple(keys)))
    target_value = session.get("target")
    device_value = session.get("device")
    target = dict(target_value) if isinstance(target_value, dict) else {}
    device = dict(device_value) if isinstance(device_value, dict) else {}
    adapted = {
        "id": session.get("id"), "name": normalize_session_name(session.get("name")),
        "source": "PerfZY", "started_at": session.get("started_at"),
        "ended_at": session.get("created_at"), "samples": rows,
        "segments": [
            {"id": item.get("id"), "name": item.get("name") or item.get("title"), "start": item.get("start"),
             "end": item.get("end"), "color": item.get("color")}
            for item in scenes if isinstance(item, dict)
        ],
        "screenshots": [
            {"elapsed": item.get("seconds", 0), "path": _local_image_path(item.get("image"))}
            for item in source_screenshots if isinstance(item, dict)
        ],
        "target": {
            "display_name": target.get("name"), "label": target.get("name"), "package": target.get("id"),
            "pid": target.get("pid"), "icon_path": _local_image_path(target.get("icon")),
        },
        "device": {
            "model": device.get("name") or device.get("model"), "label": device.get("name"),
            "os_version": device.get("os"), "state": "connected",
            "details": {key: value for key, value in device.items() if key not in {"id", "name"}},
        },
    }
    return adapted, metric_meta, groups


def _image_data_uri(value: Any, max_edge: int = 0, jpeg_quality: int = 0) -> str:
    path_text = _local_image_path(value)
    if not path_text:
        return ""
    if path_text.startswith("data:"):
        return path_text
    path = Path(path_text)
    if not path.is_file():
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if max_edge > 0:
        try:
            with Image.open(path) as image:
                image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                if jpeg_quality:
                    image.convert("RGB").save(buffer, "JPEG", quality=jpeg_quality, optimize=True)
                else:
                    image.save(buffer, "PNG", optimize=True)
                raw = buffer.getvalue()
                mime = "image/jpeg" if jpeg_quality else "image/png"
                if jpeg_quality and len(raw) >= path.stat().st_size:
                    raw = path.read_bytes()
                    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        except (OSError, ValueError):
            raw = path.read_bytes()
    else:
        raw = path.read_bytes()
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _report_payload(session: dict[str, Any], screenshot_mode: str = "compressed") -> dict[str, Any]:
    """Build the complete offline-report payload without an external engine."""
    telemetry = session.get('telemetry') or {}
    platform = canonical_platform(session.get('device') or {})
    collector_details = telemetry.get('collector') or {}
    measurement = {'platform': platform, 'android_frame_sampling': platform == 'android',
                   'frame_time_estimated': platform == 'ios'}
    source_keys = list(session.get('samples') or {})
    source_keys.extend(key for key in (session.get('enabled_metrics') or [])
                       if isinstance(key, str) and key not in source_keys)
    recorded_provenance = telemetry.get('provenance') or {}
    integrity = collector_details.get('integrity') or {}
    measurement['frame_data_incomplete'] = bool(integrity.get('queue_overflow_rows') or integrity.get('etw_loss_reported'))
    measurement['frame_data_incomplete'] |= bool(collector_details.get('frame_trace_dropped')
        or collector_details.get('present_frame_lost_windows') or collector_details.get('frame_trace_error'))
    provenance = telemetry.get('provenance') or metric_provenance(
        platform, source_keys, process_scope=collector_details.get('process_scope'), collector_details=collector_details)
    if platform in {'ios', 'android'}:
        # Correct the known historical iOS scope descriptions in newly generated
        # reports, retaining the old declaration for audit without changing data.
        provenance = metric_provenance(platform, source_keys, collector_details=collector_details)
    adapted, metrics, chart_groups = _legacy_session(session)
    samples = adapted["samples"]
    duration = _number(session.get("duration"))
    if duration is None:
        duration = max((_number(row.get("elapsed")) or 0.0 for row in samples), default=0.0)
    screenshots = []
    for item in adapted["screenshots"] if screenshot_mode != "none" else []:
        image = (_image_data_uri(item.get("path"), max_edge=854, jpeg_quality=30)
                 if screenshot_mode == "compressed" else _image_data_uri(item.get("path")))
        if image:
            screenshots.append({"elapsed": _number(item.get("elapsed")) or 0.0, "image": image})
    target = dict(adapted["target"])
    segments = []
    for item in adapted["segments"]:
        start, end = _number(item.get("start")), _number(item.get("end"))
        if start is None or end is None or end <= start:
            continue
        row = dict(item)
        row["start"], row["end"] = max(0.0, start), max(0.0, end)
        if row["end"] > row["start"]:
            segments.append(row)
    annotation_value = session.get("annotations")
    note_value = session.get("notes")
    note_value = list(note_value) if isinstance(note_value, list) else []
    note_value.extend(collection_quality_notes(telemetry.get('collector') or {}))
    if {'frame_jank', 'frame_big_jank'} & (set(session.get('enabled_metrics') or []) | set(metrics)):
        from .domain.jank import JANK_DESCRIPTION, JANK_CONTRACT
        note_value.append('逐帧卡顿：' + JANK_DESCRIPTION + ' 每个采样点为该窗口新观测帧的次数；Big Jank 已包含在 Jank 中，两者不能相加。缺少有效逐帧事件时保持缺失。')
        measurement['jank_contract'] = JANK_CONTRACT
    if platform == 'ios':
        if collector_details.get('fps_method') == 'ios-coreprofile-interval-fps-v1':
            note_value.append('iOS FPS 优先按 CoreProfile 实际帧间隔计算：1000×间隔数量/间隔总和，保留既有尖峰过滤；逐帧通道不可用时回退 CoreAnimation 计数器。两种来源均非 PID 隔离，按目标活动状态筛选。Frame Time 曲线仍为 1000/FPS 采样估算，真实逐帧事件单独保存。')
            measurement['fps_method'] = collector_details['fps_method']
            measurement['source_counts'] = collector_details.get('metric_source_counts') or {}
        else:
            note_value.append('iOS 口径：本记录的 FPS 为设备 CoreAnimation 计数，经目标活动状态筛选，非 PID 隔离；帧间隔为 1000/FPS 估算，不是实测逐帧耗时，无法据此判断真实长帧或 Jank。')
        limit = _number((session.get('capture_settings') or {}).get('ios_fps_limit'))
        if limit and limit > 0:
            note_value.append(f'本记录起始设置采用 {limit:g} FPS 声明上限校验；超过上限的源读数记为缺失，原始值及原因保存在审计中，不强行改为上限值。合并记录的各段依各自采集设置执行。')
            measurement['declared_fps_limit'] = limit
        note_value.append('CoreAnimation 计数器及历史记录可能只提供整数 FPS；回退时不会补造小数，显示位数本身不代表测量精度。')
        note_value.append('iOS GPU、图形内存、网络与电池数据为设备级，不可归因为目标应用独占；电池功率为电流×电压估算。各类内存口径不可简单相加。')
        for key in metrics:
            metrics[key]['note'] = platform_metric_note(key, Platform.IOS)
    if platform == 'android':
        note_value.append('Android 口径：帧时间来自 gfxinfo 窗口统计，可能回退 SurfaceFlinger 直方图桶近似，均值还可回退 1000/FPS；各窗口的统计值不等于整场逐帧分布。固定慢帧阈值不等同 PerfDog Jank；预算掉帧不是实测显示丢帧。')
        note_value.append('Android GPU、网络、电池功率为设备级或共享口径，非应用独占；Graphics PSS 回退不等同专用显存，各内存口径不可简单相加。CPU 频率降低比例不能单独证明温控降频。')
        counts = (telemetry.get('collector') or {}).get('metric_source_counts') or {}
        measurement['source_counts'] = counts
        measurement['source_tracking_available'] = bool(counts)
        note_value.append('来源计数表示有效采样窗口数，不是帧数，也不是源数据完整性证明。' if counts else '本记录未保留实际来源计数，无法追溯每个帧采样窗口使用了哪种回退来源。')
        for key in metrics:
            metrics[key]['note'] = platform_metric_note(key, Platform.ANDROID)
    note_value.append("曲线在缺失指标处断开；相邻记录间隔超过典型采样间隔的 3 倍时也断开连线。长间隔判断为估计，不代表精确断线时间。")
    end_reason = session.get("end_reason")
    merged_from = session.get("merged_from")
    if isinstance(merged_from, list) and merged_from:
        note_value.append(f"本记录由 {len(merged_from)} 条采集记录顺序合并，时间为各段采集时长累加，未计入记录之间的实际等待时间。设备、应用及初始测试条件采用第一条记录。")
        note_value.append("拼接顺序：" + " → ".join(normalize_session_name(part.get("name") or part.get("id"))
                                                   for part in merged_from if isinstance(part, dict)))
    if isinstance(end_reason, str) and end_reason.strip():
        note_value.append(f"采集结束原因：{end_reason}")
    requests = capture_request_summary(session, metrics)
    if platform:
        request_keys = {key for items in requests.values() for key in items}
        requests["titles"] = {key: platform_metric_title(key, Platform(platform))
                              for key in sorted(request_keys) if key in METRIC_BY_KEY}
    return {
        "id": str(adapted.get("id") or "local"),
        "source_revision": session.get("report_source_revision", ""),
        "name": normalize_session_name(adapted.get("name")),
        "source": "PerfZY",
        "metric_provenance": provenance,
        "recorded_provenance": recorded_provenance,
        "quality": session.get("quality") if isinstance(session.get("quality"), dict) else {},
        "capture_settings": session.get("capture_settings") if isinstance(session.get("capture_settings"), dict) else {},
        "merged_from": merged_from if isinstance(merged_from, list) else [],
        "capture_settings_changes": [item for item in session.get("capture_settings_changes", [])
                                     if isinstance(item, dict)]
                                    if isinstance(session.get("capture_settings_changes"), list) else [],
        "capture_settings_changes_truncated": bool(session.get("capture_settings_changes_truncated")),
        "excluded_intervals": session.get("excluded_intervals") or [],
        "timeline_cuts": session.get("timeline_cuts") or [],
        "measurement": measurement,
        "collection_quality": {'notes': collection_quality_notes(collector_details),
                               'integrity': integrity,
                               'render_stream': collector_details.get('render_stream') or {},
                               'process_scope': collector_details.get('process_scope') or {}},
        "end_reason": end_reason if isinstance(end_reason, str) else "",
        "missing_metrics": requests["missing"],
        "metric_requests": requests,
        "started_at": adapted.get("started_at"),
        "ended_at": adapted.get("ended_at"),
        "target": target,
        "device": adapted["device"],
        "app_icon": _image_data_uri(target.get("icon_path")),
        "samples": samples,
        "duration": max(0.0, duration),
        "metrics": metrics,
        "groups": [{"key": key, "title": title, "keys": list(keys)}
                   for key, title, keys in chart_groups],
        "segments": segments,
        "annotations": [item for item in annotation_value if isinstance(item, dict)]
                       if isinstance(annotation_value, list) else [],
        "screenshots": screenshots,
        "screenshot_total": len(screenshots),
        "notes": [str(item) for item in note_value
                  if isinstance(item, (str, int, float))]
                 if isinstance(note_value, list) else [],
    }


def _render_v10_payload(destination: Path, payload: dict[str, Any], *, theme: str | None = None) -> Path:
    if not _REPORT_TEMPLATE_V10.is_file():
        raise FileNotFoundError(f"报告模板不存在：{_REPORT_TEMPLATE_V10}")
    template = _REPORT_TEMPLATE_V10.read_text(encoding="utf-8")
    # Embed presentation assets into the same offline file. Never read preview
    # exports: they may contain explicitly requested demonstration pictures.
    for placeholder, asset_name in (
            ('__PERFZY_REPORT_THEME_STYLE__', 'report_theme.css'),
            ('__PERFZY_REPORT_THEME_SCRIPT__', 'report_theme.js'),
            ('__PERFZY_WORKBENCH_STYLE__', 'report_workbench.css'),
            ('__PERFZY_WORKBENCH_SCRIPT__', 'report_workbench.js')):
        if placeholder in template:
            asset = RESOURCE_ROOT / 'assets' / asset_name
            template = template.replace(placeholder, asset.read_text(encoding='utf-8'))
    template = template.replace('__PERFZY_REPORT_THEME__',
                                report_theme(destination.parent.parent / 'data' / 'preferences.ini', theme))
    required = ("__PERFZY_REPORT_DATA__", "__PERFZY_LOGO_DATA_URI__",
                "__PERFZY_REPORT_TITLE__")
    missing = [placeholder for placeholder in required if placeholder not in template]
    if missing:
        raise ValueError(f"报告模板缺少占位符：{', '.join(missing)}")
    logo_uri = _image_data_uri(
        RESOURCE_ROOT / "assets" / "perfzy-icon-rounded.png", max_edge=128)
    target_name = str(payload.get("name") or (payload.get("target") or {}).get("display_name") or "性能测试")
    template = template.replace("__PERFZY_LOGO_DATA_URI__", logo_uri)
    template = template.replace("__PERFZY_REPORT_TITLE__", html.escape(target_name))
    before, after = template.split("__PERFZY_REPORT_DATA__", 1)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
    try:
        with temporary.open("w", encoding="utf-8", newline="") as output:
            output.write(before)
            carry = ""
            for chunk in encoder.iterencode(payload):
                text = carry + chunk
                if text.endswith("<"):
                    carry, text = "<", text[:-1]
                else:
                    carry = ""
                output.write(text.replace("</", "<\\/"))
            output.write(carry)
            output.write(after)
            output.flush()
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _clock(seconds: float) -> str:
    minutes, remainder = divmod(max(0, int(round(seconds))), 60)
    return f"{minutes:02d}:{remainder:02d}"


def generate_local_report(session_path: Path, destination_dir: Path, file_stem: str = "", *, screenshot_mode: str = "compressed", theme: str | None = None) -> Path:
    if screenshot_mode not in {"none", "compressed", "original"}:
        raise ValueError("截图导出模式必须是 none、compressed 或 original")
    """Generate a fully self-contained offline HTML report from this repository."""
    session_path = Path(session_path)
    revision, _ = session_content_stamp(session_path)
    with session_path.open("r", encoding="utf-8") as stream:
        session = json.load(stream)
    if not isinstance(session, dict):
        raise ValueError("采集记录格式无效：JSON 根节点必须是对象")
    session = _apply_metadata(session, session_path)
    if session_content_stamp(session_path)[0] != revision:
        raise ValueError("采集记录在读取期间发生变化，请重新生成报告。")
    session["report_source_revision"] = revision
    identifier = str(session.get("id") or session_path.parent.name)
    destination_dir.mkdir(parents=True, exist_ok=True)
    requested_name = file_stem or normalize_session_name(session.get("name"))
    destination = destination_dir / f"{safe_report_file_stem(requested_name, identifier + '-performance-report')}.html"
    if _REPORT_TEMPLATE_V10.is_file():
        payload = _report_payload(session, screenshot_mode=screenshot_mode)
        del session
        preferences = session_path.parents[2] / 'preferences.ini' if len(session_path.parents) >= 3 else None
        return _render_v10_payload(destination, payload, theme=report_theme(preferences, theme))
    if screenshot_mode == "none":
        session["screenshots"] = []
    elif screenshot_mode == "compressed":
        for shot in session.get("screenshots", []):
            if isinstance(shot, dict):
                shot["image"] = _image_data_uri(shot.get("image"), max_edge=854, jpeg_quality=30)
    return _generate_compact_report(session_path, destination_dir, file_stem, session)


def _generate_compact_report(session_path: Path, destination_dir: Path, file_stem: str = "",
                             session: dict[str, Any] | None = None) -> Path:
    """Previous compact renderer retained for compatibility tests."""
    session_path = Path(session_path)
    if session is None:
        with session_path.open("r", encoding="utf-8") as stream:
            session = json.load(stream)
    if not isinstance(session, dict):
        raise ValueError("采集记录格式无效：JSON 根节点必须是对象")
    raw_samples = session.get("samples")
    samples = raw_samples if isinstance(raw_samples, dict) else {}
    metrics: list[dict[str, Any]] = []
    for key, rows in samples.items():
        definition = METRIC_BY_KEY.get(str(key))
        points = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            x, y = _number(row.get("x")), _number(row.get("y"))
            if x is not None and x >= 0 and y is not None:
                points.append({"x": x, "y": y})
        if not points:
            continue
        values = [point["y"] for point in points]
        metrics.append({
            "key": key,
            "title": definition.title if definition else str(key),
            "unit": definition.unit if definition else "",
            "color": definition.color if definition else "#6d8cff",
            "points": points,
            "avg": statistics.fmean(values), "min": min(values), "max": max(values),
            "p5": _percentile(values, .05), "p50": _percentile(values, .5),
            "p95": _percentile(values, .95), "count": len(values),
        })
    if not metrics:
        raise ValueError("该采集记录没有可用于生成报告的指标数据")

    identifier = str(session.get("id") or session_path.parent.name)
    destination_dir.mkdir(parents=True, exist_ok=True)
    requested_name = file_stem or normalize_session_name(session.get("name"))
    destination = destination_dir / f"{safe_report_file_stem(requested_name, identifier + '-performance-report')}.html"
    raw_target, raw_device = session.get("target"), session.get("device")
    target = dict(raw_target) if isinstance(raw_target, dict) else {}
    device = dict(raw_device) if isinstance(raw_device, dict) else {}
    duration = _number(session.get("duration"))
    if duration is None or duration < 0:
        duration = max((m["points"][-1]["x"] for m in metrics), default=0.0)
    target_name = str(target.get("name") or "目标应用")
    device_name = str(device.get("name") or device.get("model") or "未知设备")
    model = str(device.get("model") or "")
    if model and model not in device_name:
        device_name += f" / {model}"
    raw_scenes, raw_screenshots = session.get("scenes"), session.get("screenshots")
    scenes = []
    for index, item in enumerate(raw_scenes if isinstance(raw_scenes, list) else []):
        if not isinstance(item, dict):
            continue
        start, end = _number(item.get("start")), _number(item.get("end"))
        if start is None or end is None or end <= start:
            continue
        start, end = max(0.0, start), max(0.0, end)
        if end <= start:
            continue
        scenes.append({"name": item.get("name") or item.get("title") or f"标记 {index + 1}",
                       "start": start, "end": end})
    screenshots = []
    for item in raw_screenshots if isinstance(raw_screenshots, list) else []:
        if not isinstance(item, dict):
            continue
        image = _image_data_uri(item.get("image"))
        if not image:
            continue
        seconds = _number(item.get("seconds"))
        screenshots.append({"image": image, "time": item.get("time"),
                            "seconds": max(0.0, seconds or 0.0)})
    headline_keys = ("fps", "frame_time", "app_cpu", "total_cpu", "memory", "gpu", "temperature")
    headlines = [item for key in headline_keys for item in metrics if item["key"] == key][:6]
    if not headlines:
        headlines = metrics[:6]

    def fmt(value: float) -> str:
        return f"{value:.2f}"

    cards = "".join(
        f'<div class="kpi"><span>{html.escape(item["title"])}</span><b style="color:{item["color"]}">{fmt(item["avg"])}</b><small>{html.escape(item["unit"])} · 平均值</small></div>'
        for item in headlines
    )
    rows = "".join(
        f'<tr><td><i style="background:{item["color"]}"></i>{html.escape(item["title"])}</td><td>{html.escape(item["unit"]) or "—"}</td><td>{fmt(item["avg"])}</td><td>{fmt(item["min"])}</td><td>{fmt(item["p5"])}</td><td>{fmt(item["p50"])}</td><td>{fmt(item["p95"])}</td><td>{fmt(item["max"])}</td><td>{item["count"]}</td></tr>'
        for item in metrics
    )
    scene_rows = "".join(
        f'<tr><td>{html.escape(str(item["name"]))}</td><td>{item["start"]:.2f} s</td><td>{item["end"]:.2f} s</td><td>{max(0, item["end"] - item["start"]):.2f} s</td></tr>'
        for item in scenes
    ) or '<tr><td colspan="4" class="empty">本次采集没有添加时间段标记</td></tr>'
    shot_cards = "".join(
        f'<figure><img loading="lazy" src="{html.escape(item["image"], quote=True)}"><figcaption>{html.escape(str(item.get("time") or _clock(item["seconds"])))}</figcaption></figure>'
        for item in screenshots
    ) or '<div class="empty">本次采集没有截图</div>'
    report_data = json.dumps({"duration": duration, "metrics": metrics}, ensure_ascii=False).replace("</", "<\\/")
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    started = str(session.get("started_at") or session.get("created_at") or "—").replace("T", " ")[:19]
    document = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(target_name)} · PerfZY 性能测试报告</title>
<style>
:root{{--bg:#0b1020;--panel:#151c2f;--line:#29344f;--text:#e8edff;--muted:#8f9bb8;--blue:#4e7cff}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif}}main{{width:min(1320px,calc(100% - 40px));margin:auto;padding:38px 0 72px}}header{{padding:34px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(135deg,#19294d,#11182a 65%);box-shadow:0 18px 50px #0005}}.brand{{color:#6f91ff;font-weight:700;letter-spacing:.14em}}h1{{font-size:32px;margin:8px 0 4px}}.subtitle,.meta,small{{color:var(--muted)}}.meta{{display:flex;gap:18px;flex-wrap:wrap;margin-top:22px}}section{{margin-top:22px;padding:24px;background:var(--panel);border:1px solid var(--line);border-radius:14px}}h2{{font-size:18px;margin:0 0 16px}}.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}.kpi{{padding:18px;background:#101729;border:1px solid #26324c;border-radius:12px}}.kpi span,.kpi small{{display:block}}.kpi b{{display:block;font-size:26px;margin:7px 0 1px}}.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:14px}}.chart{{min-width:0;background:#101729;border:1px solid #26324c;border-radius:12px;padding:14px}}.chart-head{{display:flex;justify-content:space-between;margin-bottom:8px}}canvas{{display:block;width:100%;height:210px}}.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;min-width:760px}}th,td{{padding:10px 12px;text-align:right;border-bottom:1px solid #273149;white-space:nowrap}}th{{color:#9fabc8;font-weight:500}}th:first-child,td:first-child{{text-align:left}}td i{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}}.empty{{padding:30px;text-align:center;color:var(--muted)}}.shots{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;max-height:620px;overflow:auto}}figure{{margin:0;background:#0d1424;border:1px solid #273149;border-radius:10px;overflow:hidden}}figure img{{display:block;width:100%;height:220px;object-fit:contain;background:#080c15}}figcaption{{padding:7px;text-align:center;color:var(--muted)}}footer{{text-align:center;color:var(--muted);padding-top:30px}}@media print{{body{{background:white;color:#172033}}main{{width:100%;padding:0}}section,header{{break-inside:avoid;background:white;box-shadow:none}}.chart{{break-inside:avoid}}}}
</style></head><body><main>
<header><div class="brand">PERFZY · LOCAL PERFORMANCE REPORT</div><h1>{html.escape(target_name)}</h1><div class="subtitle">本地实机性能测试报告</div><div class="meta"><span>设备：{html.escape(device_name)}</span><span>系统：{html.escape(str(device.get("os") or "—"))}</span><span>开始时间：{html.escape(started)}</span><span>采集时长：{_clock(duration)}</span><span>有效指标：{len(metrics)}</span><span>生成时间：{generated}</span></div></header>
<section><h2>核心指标概览</h2><div class="kpis">{cards}</div></section>
<section><h2>性能趋势</h2><div class="charts" id="charts"></div></section>
<section><h2>完整指标统计</h2><div class="table-wrap"><table><thead><tr><th>指标</th><th>单位</th><th>平均</th><th>最小</th><th>P5</th><th>P50</th><th>P95</th><th>最大</th><th>样本</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section><h2>时间段标记</h2><div class="table-wrap"><table><thead><tr><th>名称</th><th>开始</th><th>结束</th><th>时长</th></tr></thead><tbody>{scene_rows}</tbody></table></div></section>
<section><h2>截图证据（{len(screenshots)}）</h2><div class="shots">{shot_cards}</div></section>
<footer>报告由 PerfZY 根据本地采集数据自动生成 · 单次采集结果应结合测试环境与重复测试复核</footer>
</main><script id="data" type="application/json">{report_data}</script><script>
const R=JSON.parse(document.getElementById('data').textContent),box=document.getElementById('charts');
const esc=s=>String(s).replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
R.metrics.forEach(m=>{{const el=document.createElement('div');el.className='chart';el.innerHTML=`<div class="chart-head"><b>${{esc(m.title)}}</b><span style="color:${{m.color}}">Avg ${{m.avg.toFixed(2)}} ${{esc(m.unit)}}</span></div><canvas></canvas>`;box.appendChild(el);const c=el.querySelector('canvas'),dpr=devicePixelRatio||1,rect=c.getBoundingClientRect();c.width=rect.width*dpr;c.height=210*dpr;const g=c.getContext('2d');g.scale(dpr,dpr);const w=rect.width,h=210,p={{l:42,r:12,t:10,b:28}},lo=Math.min(...m.points.map(x=>x.y)),hi=Math.max(...m.points.map(x=>x.y)),pad=Math.max((hi-lo)*.12,Math.abs(hi)*.03,.1),y0=lo-pad,y1=hi+pad,x1=Math.max(R.duration,1);g.strokeStyle='#283450';g.fillStyle='#8290af';g.font='11px Segoe UI';for(let i=0;i<4;i++){{let y=p.t+(h-p.t-p.b)*i/3,v=y1-(y1-y0)*i/3;g.beginPath();g.moveTo(p.l,y);g.lineTo(w-p.r,y);g.stroke();g.fillText(v.toFixed(1),3,y+4)}}g.beginPath();m.points.forEach((q,i)=>{{let x=p.l+q.x/x1*(w-p.l-p.r),y=p.t+(y1-q.y)/(y1-y0)*(h-p.t-p.b);i?g.lineTo(x,y):g.moveTo(x,y)}});g.strokeStyle=m.color;g.lineWidth=2;g.stroke();g.fillStyle='#8290af';g.fillText('00:00',p.l,h-8);g.fillText(`${{String(Math.floor(x1/60)).padStart(2,'0')}}:${{String(Math.floor(x1%60)).padStart(2,'0')}}`,w-46,h-8)}});
</script></body></html>'''
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        temporary.write_text(document, encoding="utf-8")
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination
