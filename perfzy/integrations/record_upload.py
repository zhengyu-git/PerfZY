"""Send one saved record to a user-configured HTTP endpoint."""

from __future__ import annotations

import base64
import hashlib
from ipaddress import ip_address
import json
import math
from pathlib import Path
import re
from tempfile import TemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..domain.application_identity import mobile_package_name
from ..domain.metrics import METRIC_BY_KEY
from ..domain.provenance import canonical_platform
from ..storage.sessions import METADATA_NAME, _session_directory, load_session, session_content_stamp


MAX_RESPONSE_BYTES = 64 * 1024
MAX_SCREENSHOT_BYTES = 16 * 1024 * 1024
DEVICE_FIELDS = ("name", "model", "os", "cpu", "gpu", "memory", "resolution")


class UploadError(ValueError):
    """An upload problem that can be shown without exposing request credentials."""


def _http_url(value, *, allow_fragment=False):
    if not isinstance(value, str) or len(value) > 4096 or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        raise UploadError("请输入完整的 HTTP 或 HTTPS 地址。")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or (parsed.fragment and not allow_fragment)):
            raise ValueError
        if parsed.port == 0:
            raise ValueError
    except ValueError as error:
        raise UploadError("请输入完整的 HTTP 或 HTTPS 地址，凭据请填在 Token 栏。") from error
    return parsed


def validate_upload_settings(endpoint: str, token: str) -> None:
    parsed = _http_url(endpoint)
    if token and not re.fullmatch(r"[A-Za-z0-9._~+/-]+={0,8}", token):
        raise UploadError("Token 格式无效，请填写 Bearer 后面的内容。")
    if len(token) > 4096:
        raise UploadError("Token 超过长度限制。")
    if token and parsed.scheme == "http":
        try:
            loopback = ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname.lower() == "localhost"
        if not loopback:
            raise UploadError("向远程服务发送 Token 时，请使用 HTTPS 地址。")


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _timeline_rows(rows, fields):
    if not isinstance(rows, list):
        return []
    selected = []
    for row in rows:
        if isinstance(row, dict):
            values = {field: _number(row.get(field)) for field in fields}
            if all(value is not None for value in values.values()):
                selected.append(values)
    return selected


def _snapshot_stamp(path):
    metadata = path.parent / METADATA_NAME
    return session_content_stamp(path), metadata.stat().st_mtime_ns if metadata.is_file() else 0


def _screenshot_data(shot, screenshot_dir):
    reference = str(shot.get("image") or "")
    try:
        if reference.startswith("data:image/"):
            prefix, separator, encoded = reference.partition(",")
            if (not separator or not prefix.endswith(";base64")
                    or len(encoded) > 4 * ((MAX_SCREENSHOT_BYTES + 2) // 3)):
                raise ValueError
            content = base64.b64decode(encoded, validate=True)
        else:
            parsed = urlsplit(reference)
            name = Path(unquote(parsed.path) if parsed.scheme == "file" else reference).name
            path = (screenshot_dir / name).resolve()
            if not name or path.parent != screenshot_dir or not path.is_file():
                raise ValueError
            with path.open("rb") as image:
                content = image.read(MAX_SCREENSHOT_BYTES + 1)
        if len(content) > MAX_SCREENSHOT_BYTES:
            raise ValueError
        if content.startswith(b"\xff\xd8\xff"):
            content_type = "image/jpeg"
        elif content.startswith(b"\x89PNG\r\n\x1a\n"):
            content_type = "image/png"
        elif content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            content_type = "image/webp"
        else:
            raise ValueError
    except (OSError, ValueError) as error:
        raise UploadError("有截图缺失、格式不支持或超过 16 MiB。可取消勾选截图后重试。") from error
    return {"seconds": shot["seconds"], "content_type": content_type,
            "data": base64.b64encode(content).decode("ascii")}


def _write_payload(root, record_id, include_screenshots, output):
    try:
        directory = _session_directory(root, record_id)
        session_path = directory / "session.json"
        if session_path.is_symlink():
            raise UploadError("无法读取这条采集记录。")
        stamp = _snapshot_stamp(session_path)
    except (OSError, ValueError) as error:
        raise UploadError("无法读取这条采集记录。请确认记录仍在列表中。") from error
    session = load_session(root, record_id)
    if not session:
        raise UploadError("无法读取这条采集记录。请确认记录仍在列表中。")

    device = session["device"]
    target = session["target"]
    platform = canonical_platform(device)
    details = {key: str(device[key]) for key in DEVICE_FIELDS if device.get(key)}
    details["platform"] = platform or str(device.get("platform") or "")
    if not details.get("os") and device.get("os_version"):
        details["os"] = str(device["os_version"])
    application = {"name": str(target.get("name") or "")}
    if platform in {"android", "ios"}:
        package = mobile_package_name(target)
        if re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+", package):
            application["package"] = package

    samples = session["samples"]
    enabled = session.get("enabled_metrics")
    enabled_keys = {key for key in enabled if isinstance(key, str)} if isinstance(enabled, list) else set(samples)
    keys = sorted(set(samples) | enabled_keys)
    metrics = {key: {"title": METRIC_BY_KEY[key].title, "unit": METRIC_BY_KEY[key].unit}
               for key in keys if key in METRIC_BY_KEY}
    record = {
        "id": record_id,
        "name": str(session.get("name") or application["name"] or record_id),
        "started_at": str(session.get("started_at") or session.get("created_at") or ""),
        "duration": max(_number(session.get("duration")) or 0,
                        max((points[-1]["x"] for points in samples.values()), default=0)),
        "device": details,
        "application": application,
        "enabled_metrics": sorted(enabled_keys),
        "metrics": metrics,
        "scenes": [{"name": str(row.get("name") or ""), "start": row["start"], "end": row["end"],
                    "color": str(row.get("color") or "")} for row in session["scenes"]],
        "excluded_intervals": _timeline_rows(session.get("excluded_intervals"), ("start", "end")),
        "timeline_cuts": _timeline_rows(session.get("timeline_cuts"), ("time", "removed_seconds")),
        "merged_segments": _timeline_rows(session.get("merged_from"), ("start", "end", "duration")),
    }
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    output.write('{"schema":"perfzy.record.v1","record":')
    output.write(encoder.encode(record))
    output.write(',"samples":{')
    for index, (key, points) in enumerate(sorted(samples.items())):
        if index:
            output.write(",")
        output.write(encoder.encode(key))
        output.write(":")
        for chunk in encoder.iterencode([{"x": point["x"], "y": point["y"]} for point in points]):
            output.write(chunk)
    output.write('},"screenshots":[')
    if include_screenshots:
        screenshot_dir = (directory / "screenshots").resolve()
        if screenshot_dir.parent != directory:
            raise UploadError("截图目录无效。")
        for index, shot in enumerate(session["screenshots"]):
            if index:
                output.write(",")
            output.write(encoder.encode(_screenshot_data(shot, screenshot_dir)))
    output.write("]}")
    try:
        changed = stamp != _snapshot_stamp(session_path)
    except OSError:
        changed = True
    if changed:
        raise UploadError("上传准备期间记录有变动，请重新选择后上传。")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _receipt(raw, token):
    try:
        response = json.loads(raw)
        record_id = response["record_id"]
        if (not isinstance(record_id, str) or not record_id.strip() or len(record_id) > 160
                or re.search(r"[\x00-\x1f\x7f]", record_id) or (token and record_id == token)):
            raise ValueError
        report_url = response.get("report_url") or ""
        if report_url:
            _http_url(report_url, allow_fragment=True)
            if token and token in report_url:
                raise ValueError
        return {"record_id": record_id, "report_url": report_url}
    except (KeyError, TypeError, ValueError) as error:
        raise UploadError("服务已响应，但回执缺少有效的记录编号或报告链接。请先在服务端核对。") from error


def upload_record(root: Path, record_id: str, endpoint: str, token: str = "",
                  *, include_screenshots: bool = False, timeout: float = 30) -> dict:
    """Send a single snapshot. Retrying the same payload yields the same idempotency key."""
    validate_upload_settings(endpoint, token)
    with TemporaryFile(mode="w+", encoding="utf-8", newline="\n") as output:
        try:
            _write_payload(root, record_id, include_screenshots, output)
            output.flush()
        except UploadError:
            raise
        except (OSError, ValueError, TypeError) as error:
            raise UploadError("无法准备采集记录。请检查记录完整性和磁盘空间。") from error
        body = output.buffer
        size = body.tell()
        body.seek(0)
        digest = hashlib.file_digest(body, "sha256").hexdigest()
        body.seek(0)
        headers = {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json",
                   "Content-Length": str(size), "Idempotency-Key": digest}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            with error:
                if 300 <= error.code < 400:
                    raise UploadError("接口返回了跳转，请在设置里填写最终接收地址。") from error
                if error.code in (401, 403):
                    raise UploadError("接口拒绝授权。请检查 Token 和服务权限。") from error
                raise UploadError(f"上传失败，服务返回 HTTP {error.code}。") from error
        except (URLError, OSError, ValueError) as error:
            raise UploadError("连接中断或未能收到回执。请检查网络并在服务端核对接收状态。") from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise UploadError("服务回执过大。请先在服务端核对接收状态。")
        return _receipt(raw, token)
