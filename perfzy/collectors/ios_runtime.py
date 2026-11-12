from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from ..runtime_paths import RESOURCE_ROOT


def command(*arguments: str) -> list[str]:
    # Force the bridge pipe to UTF-8. Windows otherwise chooses the active ANSI
    # code page for redirected stdout while the parent correctly expects UTF-8.
    if getattr(sys, "frozen", False):
        return [sys.executable, "--ios-bridge", *arguments]
    bridge = RESOURCE_ROOT / "tools" / "ios_bridge.py"
    return [sys.executable, "-X", "utf8", str(bridge), *arguments]


def run_json(*arguments: str, timeout: float = 20.0, strict: bool = False) -> Any:
    try:
        result = subprocess.run(command(*arguments), capture_output=True, text=True, timeout=timeout,
                                encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as error:
        if strict:
            raise RuntimeError(f"iOS 扫描程序启动失败或超时：{error}") from error
        return []
    if result.returncode:
        if strict:
            detail = (result.stderr or result.stdout or "无错误输出").strip()
            raise RuntimeError(f"iOS 扫描程序退出码 {result.returncode}：{detail}")
        return []
    try:
        payload = json.loads(result.stdout.strip() or ("" if strict else "[]"))
    except json.JSONDecodeError as error:
        if strict:
            raise RuntimeError("iOS 扫描程序返回的内容不是有效 JSON") from error
        return []
    if strict and not isinstance(payload, list):
        label = "应用列表" if arguments and arguments[0] == "apps" else "设备列表"
        raise RuntimeError(f"iOS 扫描程序返回的数据不是{label}")
    return payload
