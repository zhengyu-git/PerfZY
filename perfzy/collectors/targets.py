from __future__ import annotations

import shutil
import subprocess
import ctypes
import re
from ctypes import wintypes
import getpass
from collections.abc import Iterable

import psutil

from ..domain.models import Device, Platform, Target
from .android_labels import resolve_labels
from .ios_runtime import run_json as run_ios_json
from ..runtime_paths import WORK_ROOT, find_tool


WINDOWS_SYSTEM_PROCESSES = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "services.exe",
    "lsass.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe", "conhost.exe",
}
BACKGROUND_NAMES = {
    "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "runtimebroker.exe", "applicationframehost.exe", "textinputhost.exe",
    "securityhealthsystray.exe", "onedrive.exe", "widgets.exe", "widgetservice.exe",
}


def _run(command: list[str], timeout: float = 8.0) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "") if result.returncode == 0 else ""


def windows_targets(device: Device) -> Iterable[Target]:
    visible_pids: set[int] = set()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(hwnd: int, _lparam: int) -> bool:
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        if ctypes.windll.user32.GetWindow(hwnd, 4):
            return True
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value > 4:
            visible_pids.add(int(pid.value))
        return True

    ctypes.windll.user32.EnumWindows(visit, 0)
    rows: dict[str, Target] = {}
    current_user = getpass.getuser().casefold()
    for process in psutil.process_iter(("pid", "name", "exe", "username", "memory_info")):
        try:
            pid = int(process.info["pid"])
            name = str(process.info.get("name") or "").strip()
            exe = str(process.info.get("exe") or "").strip()
            username = str(process.info.get("username") or "").casefold()
            memory = process.info.get("memory_info")
            fallback_candidate = (
                not visible_pids
                and current_user in username
                and memory is not None and memory.rss >= 25 * 1024 * 1024
                and "\\windows\\" not in exe.casefold()
            )
            if (pid not in visible_pids and not fallback_candidate) or not name or name.lower() in WINDOWS_SYSTEM_PROCESSES | BACKGROUND_NAMES or not exe:
                continue
            key = exe.casefold()
            rows.setdefault(key, Target(id=f"windows:{pid}", device_id=device.id,
                                        name=name.removesuffix(".exe"), process_id=pid))
        except (psutil.Error, OSError, ValueError):
            continue
    return sorted(rows.values(), key=lambda item: item.name.casefold())


def android_targets(device: Device) -> Iterable[Target]:
    adb = find_tool("adb")
    if not adb:
        return ()
    serial = device.id.partition(":")[2]
    output = _run([adb, "-s", serial, "shell", "cmd", "package", "query-activities", "--brief",
                   "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER"])
    packages = sorted({line.strip().split("/", 1)[0] for line in output.splitlines()
                       if "/" in line and not line.startswith("No activities")})
    third_party = set(_run([adb, "-s", serial, "shell", "pm", "list", "packages", "-3"]).replace("package:", "").split())
    cache_root = WORK_ROOT / "data" / "android-app-labels"
    vendor_prefixes = ("com.android.", "com.miui.", "com.xiaomi.", "com.duokan.")
    focused_output = _run([adb, "-s", serial, "shell", "dumpsys", "window"])
    focused_match = re.search(r"mCurrentFocus=.*?\s([A-Za-z0-9_.]+)/(?:[A-Za-z0-9_.$]+)", focused_output)
    focused_package = focused_match.group(1) if focused_match else ""
    process_output = _run([adb, "-s", serial, "shell", "ps", "-A"])
    running_packages = {line.split()[-1].split(":", 1)[0] for line in process_output.splitlines()
                        if line.split() and "." in line.split()[-1]}
    candidates = {item for item in packages if item in third_party and not item.startswith(vendor_prefixes)}
    if focused_package in packages and focused_package not in {"com.miui.home", "com.android.settings"}:
        candidates.add(focused_package)
    packages = sorted(candidates)
    labels = resolve_labels(adb, serial, packages, cache_root)
    def display_name(package: str) -> str:
        label = labels.get(package, "").strip()
        if label:
            return f"{label}  ·  {package}"
        suffix = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", package.rsplit(".", 1)[-1]).replace("_", " ")
        return f"{suffix.title()}  ·  {package}"
    targets = [Target(id=f"android:{serial}:{package}", device_id=device.id,
                      name=display_name(package)) for package in packages]
    return tuple(sorted(targets, key=lambda item: (item.id.rsplit(":", 1)[-1] != focused_package,
                                                    item.id.rsplit(":", 1)[-1] not in running_packages,
                                                    "game" not in item.id.casefold(),
                                                    item.name.casefold())))


def ios_targets(device: Device, *, strict: bool = False) -> Iterable[Target]:
    udid = device.id.partition(":")[2]
    payload = run_ios_json("apps", "--udid", udid, timeout=25.0, strict=strict)
    if not isinstance(payload, list):
        return ()
    return tuple(Target(id=f"ios:{udid}:{item['bundle_id']}", device_id=device.id,
                        name=f"{item.get('name') or item['bundle_id']}  ·  {item['bundle_id']}")
                 for item in payload
                 if isinstance(item, dict) and item.get("bundle_id"))


def discover_targets(device: Device, *, strict: bool = False) -> list[Target]:
    if device.platform is Platform.WINDOWS:
        return list(windows_targets(device))
    if device.platform is Platform.ANDROID:
        return list(android_targets(device))
    return list(ios_targets(device, strict=strict))
