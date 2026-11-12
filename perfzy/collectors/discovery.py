from __future__ import annotations

import platform
import socket
import re
import shutil
import subprocess
import ctypes
import psutil
from collections.abc import Iterable

from ..domain.models import Device, Platform
from ..runtime_paths import find_tool
from .ios_runtime import run_json as run_ios_json
from .windows_system import windows_gpu_description


SOC_NAMES = {
    "SM8450": "Snapdragon 8 Gen 1",
    "SM8475": "Snapdragon 8+ Gen 1",
    "SM8550": "Snapdragon 8 Gen 2",
    "SM8650": "Snapdragon 8 Gen 3",
    "SM8750": "Snapdragon 8 Elite",
}

IPHONE_MODELS = {
    "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12",
    "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max",
    "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,4": "iPhone 13 mini", "iPhone14,5": "iPhone 13",
    "iPhone14,6": "iPhone SE (3rd generation)", "iPhone14,7": "iPhone 14",
    "iPhone14,8": "iPhone 14 Plus", "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,3": "iPhone 14 Pro Max", "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus", "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max", "iPhone17,1": "iPhone 16 Pro",
    "iPhone17,2": "iPhone 16 Pro Max", "iPhone17,3": "iPhone 16",
    "iPhone17,4": "iPhone 16 Plus", "iPhone17,5": "iPhone 16e",
}


def _friendly_soc(raw: str) -> str:
    raw = raw.strip()
    friendly = SOC_NAMES.get(raw.upper(), "")
    return f"{friendly}（{raw.upper()}）" if friendly else raw


def _friendly_memory(total_kb: int) -> str:
    usable_gb = total_kb / 1024 / 1024
    installed_gb = next((size for size in (2, 3, 4, 6, 8, 12, 16, 18, 24, 32)
                         if usable_gb <= size and usable_gb >= size * .78), 0)
    if installed_gb:
        return f"{installed_gb} GB（系统可用 {usable_gb:.1f} GB）"
    return f"系统可用 {usable_gb:.1f} GB"


def _android_connection(serial: str, attributes: dict[str, str], *, emulator: bool = False,
                        devpath: str = "") -> str:
    """Use ADB transport evidence, never assume an opaque serial means USB."""
    if emulator or serial.startswith('emulator-'):
        return 'emulator'
    if attributes.get('usb'):
        return 'usb'
    if re.fullmatch(r'(?:\[[^\]]+\]|[^\s:]+):\d+', serial):
        return 'network'
    if re.search(r'\._adb-tls-connect\._tcp(?:\.local)?\.?$', serial, re.IGNORECASE):
        return 'network'
    path = devpath.strip().casefold()
    if path.startswith(('usb:', '\\\\?\\usb#', 'usb\\vid_')):
        return 'usb'
    return 'unknown'


def _ios_connection(item: dict) -> str:
    """usbmux transport is evidence; RSD/loopback tunnel endpoints are not."""
    raw = str(item.get('connection_type') or item.get('ConnectionType') or '').casefold()
    return {'usb': 'usb', 'network': 'network'}.get(raw, 'unknown')


def _run(command: list[str], timeout: float = 4.0) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _windows_registry_value(path: str, name: str) -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            value = str(winreg.QueryValueEx(key, name)[0]).strip()
            if value.lower() in {"system product name", "to be filled by o.e.m.", "default string"}:
                return ""
            return value
    except (ImportError, OSError):
        return ""


def _windows_computer_name() -> str:
    # Read the running machine directly, without platform.uname()'s cache.
    buffer = ctypes.create_unicode_buffer(256)
    size = ctypes.c_ulong(len(buffer))
    if ctypes.windll.kernel32.GetComputerNameW(buffer, ctypes.byref(size)):
        return buffer.value
    return socket.gethostname() or "Windows"


def discover_windows() -> Iterable[Device]:
    width, height = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    memory_gb = psutil.virtual_memory().total / (1024 ** 3)
    logical_cores = max(1, psutil.cpu_count(logical=True) or 1)
    yield Device(
        id="windows:local",
        platform=Platform.WINDOWS,
        name=f"本机 PC · {_windows_computer_name()}",
        model=(_windows_registry_value(r"HARDWARE\DESCRIPTION\System\BIOS", "SystemProductName")
               or _windows_registry_value(r"HARDWARE\DESCRIPTION\System\BIOS", "BaseBoardProduct")
               or "未知型号"),
        os_version=platform.platform(),
        details={"cpu": (_windows_registry_value(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString") or "未知处理器"), "resolution": f"{width} × {height}" if width and height else "",
                 "memory": f"{memory_gb:.1f} GB", "cpu_cores": str(logical_cores),
                 "gpu": windows_gpu_description(), "connection_type": "local", "connection_source": "local"},
    )


def discover_android() -> Iterable[Device]:
    adb = find_tool("adb")
    if not adb:
        return
    for line in _run([adb, "devices", "-l"]).splitlines()[1:]:
        fields = line.split()
        if len(fields) < 2 or fields[1] != "device":
            continue
        serial = fields[0]
        attributes = dict(item.split(":", 1) for item in fields[2:] if ":" in item)
        props = _run([adb, "-s", serial, "shell", "getprop"])
        def prop(name: str) -> str:
            match = re.search(rf"\[{re.escape(name)}\]: \[(.*?)\]", props)
            return match.group(1).strip() if match else ""
        size = _run([adb, "-s", serial, "shell", "wm", "size"])
        size_match = re.search(r"(?:Override|Physical) size:\s*(\d+x\d+)", size)
        mem = _run([adb, "-s", serial, "shell", "cat", "/proc/meminfo"])
        mem_match = re.search(r"MemTotal:\s*(\d+)\s*kB", mem)
        cpu_present = _run([adb, "-s", serial, "shell", "cat", "/sys/devices/system/cpu/present"]).strip()
        cpu_range = re.search(r"0-(\d+)", cpu_present)
        cpu_cores = int(cpu_range.group(1)) + 1 if cpu_range else 0
        raw_soc = prop("ro.soc.model") or prop("ro.board.platform")
        connection = _android_connection(serial, attributes, emulator=prop('ro.kernel.qemu') == '1')
        connection_source = "adb devices -l"
        if connection == 'unknown':
            # Some Windows ADB builds omit the USB column in devices -l.
            connection = _android_connection(serial, attributes,
                devpath=_run([adb, "-s", serial, "get-devpath"], timeout=1.5))
            if connection != 'unknown':
                connection_source = "adb get-devpath"
        details = {
            "cpu": _friendly_soc(raw_soc),
            "resolution": size_match.group(1).replace("x", " × ") if size_match else "",
            "memory": _friendly_memory(int(mem_match.group(1))) if mem_match else "",
            "cpu_cores": str(cpu_cores) if cpu_cores else "",
            "connection_type": connection,
            "connection_source": connection_source,
        }
        market_name = prop("ro.product.marketname") or prop("ro.product.vendor.marketname")
        model_name = prop("ro.product.model") or attributes.get("model", "") or attributes.get("device", "")
        yield Device(
            id=f"android:{serial}", platform=Platform.ANDROID,
            name=market_name or model_name or serial,
            model=model_name,
            os_version=f"Android {prop('ro.build.version.release')}".strip(), connected=True,
            details=details,
        )


def discover_ios(*, strict: bool = False) -> Iterable[Device]:
    payload = run_ios_json("devices", timeout=18.0, strict=strict)
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        udid = str(item.get("udid") or "")
        if udid:
            product_type = str(item.get("model") or "")
            friendly_model = IPHONE_MODELS.get(product_type, product_type or "iPhone")
            yield Device(
                id=f"ios:{udid}", platform=Platform.IOS,
                name=friendly_model,
                model=f"{friendly_model}（{product_type}）" if product_type and friendly_model != product_type else friendly_model,
                os_version=f"iOS {item.get('version', '')}".strip(),
                connected=not bool(item.get("error")),
                details={"cpu": str(item.get("cpu") or ""), "build": str(item.get("build") or ""),
                         "product_type": product_type, "product_name": friendly_model,
                         "device_name": str(item.get("name") or ""), "udid": udid,
                         "cpu_cores": str(item.get("cpu_cores") or 6),
                         "connection_type": _ios_connection(item),
                         "connection_source": "usbmux" if _ios_connection(item) != 'unknown' else ''},
            )


def discover_all(*, errors: dict[str, str] | None = None) -> list[Device]:
    devices = [*discover_windows(), *discover_android()]
    try:
        devices.extend(discover_ios(strict=errors is not None))
    except RuntimeError as error:
        if errors is not None:
            errors["ios"] = str(error)
        else:
            raise
    return devices
