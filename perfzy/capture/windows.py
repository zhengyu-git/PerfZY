"""Read-only Windows target discovery and occlusion-independent snapshots."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__' and sys.argv[1:] == ['--capture-window-worker']:
    # Match app.py's source-install dependency lookup in vendor-only installs.
    vendor = Path(__file__).resolve().parents[2] / 'vendor'
    if vendor.is_dir():
        sys.path.insert(0, str(vendor))

from PIL import Image
import psutil


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
GA_ROOT = 2
GW_OWNER = 4
PW_RENDERFULLCONTENT = 2
MAX_CAPTURE_PIXELS = 40_000_000
CAPTURE_TIMEOUT_S = 3.0
_ENUM_CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_INVALID_GDI_OBJECT = ctypes.c_void_p(-1).value


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]


def _configure_winapi() -> None:
    # HWND/HDC/HBITMAP/HGDIOBJ must not use ctypes' default 32-bit int return.
    signatures = (
        (user32, "WindowFromPoint", [wintypes.POINT], wintypes.HWND),
        (user32, "GetAncestor", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        (user32, "GetCursorPos", [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        (user32, "GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        (user32, "GetWindowRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        (user32, "IsWindow", [wintypes.HWND], wintypes.BOOL),
        (user32, "IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
        (user32, "IsIconic", [wintypes.HWND], wintypes.BOOL),
        (user32, "IsHungAppWindow", [wintypes.HWND], wintypes.BOOL),
        (user32, "GetWindow", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        (user32, "GetDesktopWindow", [], wintypes.HWND),
        (user32, "GetShellWindow", [], wintypes.HWND),
        (user32, "GetWindowTextLengthW", [wintypes.HWND], ctypes.c_int),
        (user32, "GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user32, "GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user32, "EnumWindows", [_ENUM_CALLBACK, wintypes.LPARAM], wintypes.BOOL),
        (user32, "GetDC", [wintypes.HWND], wintypes.HDC),
        (user32, "ReleaseDC", [wintypes.HWND, wintypes.HDC], ctypes.c_int),
        (user32, "PrintWindow", [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL),
        (gdi32, "CreateCompatibleDC", [wintypes.HDC], wintypes.HDC),
        (gdi32, "CreateDIBSection", [wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD], wintypes.HBITMAP),
        (gdi32, "SelectObject", [wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ),
        (gdi32, "DeleteObject", [wintypes.HGDIOBJ], wintypes.BOOL),
        (gdi32, "DeleteDC", [wintypes.HDC], wintypes.BOOL),
    )
    for library, name, arguments, result in signatures:
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result


_configure_winapi()


def _pid_for_window(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _process_identity(process_id: int) -> tuple[str, float | None]:
    name, created = "", None
    try:
        process = psutil.Process(process_id)
    except (psutil.Error, OSError, ValueError):
        return name, created
    try:
        name = process.name()
    except (psutil.Error, OSError):
        pass
    try:
        created = process.create_time()
    except (psutil.Error, OSError):
        pass
    return name, created


def describe_window(hwnd: int, expected_pid: int | None = None) -> dict | None:
    """Describe one visible top-level window without activating or capturing it."""
    try:
        hwnd = int(hwnd)
        if not hwnd or not user32.IsWindow(hwnd):
            return None
        if hwnd in (user32.GetDesktopWindow(), user32.GetShellWindow()):
            return None
        if (not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd)
                or user32.IsHungAppWindow(hwnd)):
            return None
        if user32.GetAncestor(hwnd, GA_ROOT) != hwnd:
            return None
        kind = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, kind, len(kind))
        if kind.value in {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}:
            return None
        pid = _pid_for_window(hwnd)
        if not pid or pid == os.getpid() or (expected_pid is not None and pid != int(expected_pid)):
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        title = ctypes.create_unicode_buffer(min(32767, max(0, user32.GetWindowTextLengthW(hwnd))) + 1)
        user32.GetWindowTextW(hwnd, title, len(title))
        name, created = _process_identity(pid)
        # Window destruction or handle reuse during metadata reads is invalid.
        if not user32.IsWindow(hwnd) or _pid_for_window(hwnd) != pid:
            return None
        return {"hwnd": hwnd, "pid": pid, "title": title.value,
                "process_name": name, "create_time": created,
                "rect": {"left": rect.left, "top": rect.top, "right": rect.right,
                         "bottom": rect.bottom, "width": width, "height": height}}
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def window_at_cursor(exclude_process_id: int | None = None) -> dict | None:
    """Resolve the top-level window under the cursor; never change focus."""
    try:
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None
        hit = user32.WindowFromPoint(point)
        if not hit:
            return None
        hwnd = user32.GetAncestor(hit, GA_ROOT)
        if not hwnd:
            return None
        description = describe_window(hwnd)
        if description is not None and description["pid"] != exclude_process_id:
            return description
    except (OSError, ValueError, TypeError, OverflowError):
        pass
    return None


def list_visible_windows(exclude_process_id: int | None = None) -> list[dict]:
    """List eligible window metadata in Z order, without screen capture."""
    windows = []

    @_ENUM_CALLBACK
    def visit(hwnd, _lparam):
        description = describe_window(hwnd)
        if description is not None and description["pid"] != exclude_process_id:
            windows.append(description)
        return True

    try:
        if not user32.EnumWindows(visit, 0):
            return []
    except (OSError, ValueError):
        return []
    return windows


def find_main_window(process_id: int) -> int:
    candidates = []

    @_ENUM_CALLBACK
    def visit(hwnd, _lparam):
        # Periodic snapshots should not open process handles for every other
        # application's windows; enrich only candidates of the requested PID.
        if _pid_for_window(hwnd) != process_id:
            return True
        window = describe_window(hwnd, expected_pid=process_id)
        if window is not None:
            rect = window["rect"]
            if (not user32.GetWindow(hwnd, GW_OWNER)
                    and rect["width"] > 100 and rect["height"] > 100):
                candidates.append((rect["width"] * rect["height"], hwnd))
        return True

    try:
        if not user32.EnumWindows(visit, 0):
            return 0
    except (OSError, ValueError):
        return 0
    return max(candidates, default=(0, 0))[1]


def _capture_window_content(hwnd: int, width: int, height: int) -> Image.Image | None:
    """Render only in our helper process: PrintWindow can block indefinitely."""
    if width <= 0 or height <= 0 or width * height > MAX_CAPTURE_PIXELS:
        return None
    window_dc = user32.GetDC(hwnd)
    if not window_dc:
        return None
    memory_dc, bitmap, previous = None, None, None
    try:
        memory_dc = gdi32.CreateCompatibleDC(window_dc)
        if not memory_dc:
            return None
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth, info.bmiHeader.biHeight = width, -height
        info.bmiHeader.biPlanes, info.bmiHeader.biBitCount = 1, 32
        size = width * height * 4
        info.bmiHeader.biSizeImage = size
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(window_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            return None
        previous = gdi32.SelectObject(memory_dc, bitmap)
        if not previous or previous == _INVALID_GDI_OBJECT:
            previous = None
            return None
        # Reject only untouched sentinel bytes, not valid black/dark scenes.
        prefix = bytes(range(64))[:min(64, size)]
        ctypes.memset(bits.value, 0xA7, size)
        ctypes.memmove(bits.value, prefix, len(prefix))
        rendered = user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT)
        if not rendered:
            rendered = user32.PrintWindow(hwnd, memory_dc, 0)
        if not rendered:
            return None
        raw = ctypes.string_at(bits.value, size)
        if raw[:len(prefix)] == prefix and raw.count(0xA7) == size - len(prefix):
            return None
        return Image.frombytes("RGB", (width, height), raw, "raw", "BGRX")
    except (OSError, ValueError, MemoryError):
        return None
    finally:
        if memory_dc:
            if previous:
                gdi32.SelectObject(memory_dc, previous)
            # Deleting the DC first also releases selected bitmap references
            # if restoration fails while the target is being destroyed.
            gdi32.DeleteDC(memory_dc)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        user32.ReleaseDC(hwnd, window_dc)


def _same_window(first: dict, second: dict | None) -> bool:
    return bool(second is not None and second["hwnd"] == first["hwnd"]
                and second["pid"] == first["pid"]
                and second["create_time"] == first["create_time"]
                and second["rect"] == first["rect"])


def _capture_worker_main() -> int:
    """Private helper protocol: bounded JSON on stdin, one PNG on stdout.

    A timeout terminates this helper only. Windows reclaims its GDI objects;
    the target process is never opened for termination or otherwise changed.
    """
    try:
        source = getattr(getattr(sys, "stdin", None), "buffer", None)
        destination = getattr(getattr(sys, "stdout", None), "buffer", None)
        if source is None or destination is None:
            return 2
        payload = source.read(4097)
        if len(payload) > 4096:
            return 2
        request = json.loads(payload)
        hwnd, process_id = int(request["hwnd"]), int(request["pid"])
        before = describe_window(hwnd, expected_pid=process_id)
        if not _same_window(request, before):
            return 1
        rect = before["rect"]
        image = _capture_window_content(hwnd, rect["width"], rect["height"])
        if image is None:
            return 1
        try:
            if not _same_window(before, describe_window(hwnd, expected_pid=process_id)):
                return 1
            width = int(request.get("max_width", 800))
            if width > 0 and image.width > width:
                resized = image.resize((width, max(1, round(image.height * width / image.width))))
                image.close()
                image = resized
            image.save(destination, "PNG", compress_level=1)
            return 0
        finally:
            image.close()
    except (OSError, ValueError, TypeError, OverflowError, KeyError, MemoryError):
        return 2


def _capture_worker_command() -> list[str]:
    # A frozen application must dispatch --capture-window-worker in its entry
    # point. Source installs execute this same module without importing the UI.
    if getattr(sys, "frozen", False):
        return [sys.executable, "--capture-window-worker"]
    executable = Path(sys.executable)
    if executable.stem.casefold().startswith("pythonw"):
        # The GUI launcher may be pythonw. Use its same-installation console
        # sibling for predictable binary streams; CREATE_NO_WINDOW hides it.
        sibling = executable.with_name("python" + executable.name[len("pythonw"):])
        if sibling.is_file():
            executable = sibling
    return [str(executable), "-X", "utf8", str(Path(__file__).resolve()), "--capture-window-worker"]


def _capture_window_isolated(window: dict, max_width: int) -> Image.Image | None:
    """Bound startup, rendering and IPC to our own disposable child process."""
    command = _capture_worker_command()
    request = {key: window[key] for key in ("hwnd", "pid", "create_time", "rect")}
    request["max_width"] = int(max_width)
    try:
        # subprocess.run kills and reaps only the Popen it created on timeout.
        # No game process ID is ever used as an OS termination target.
        result = subprocess.run(command, input=json.dumps(request).encode("utf-8"),
                                capture_output=True, timeout=CAPTURE_TIMEOUT_S,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0 or not result.stdout:
            return None
        rect = window["rect"]
        expected_width = min(rect["width"], max_width) if max_width > 0 else rect["width"]
        expected_height = max(1, round(rect["height"] * expected_width / rect["width"]))
        with Image.open(BytesIO(result.stdout)) as image:
            if image.size != (expected_width, expected_height):
                return None
            image.load()
            return image.convert("RGB")
    except (OSError, ValueError, subprocess.TimeoutExpired, MemoryError):
        return None


def capture_process_window(process_id: int, destination: Path, max_width: int = 800,
                           *, window_handle: int | None = None) -> Path | None:
    """Capture a validated window, optionally selected independently.

    PrintWindow runs in an owned helper with a three-second timeout. It can
    fail for protected or GPU-rendered windows. Return None
    instead of copying whatever overlaps its screen rectangle. This never
    activates, restores or otherwise operates the target window.
    """
    try:
        process_id = int(process_id)
        if process_id <= 0:
            return None
        hwnd = int(window_handle) if window_handle is not None else find_main_window(process_id)
        before = describe_window(hwnd, expected_pid=process_id)
        if before is None:
            return None
        rect = before["rect"]
        image = _capture_window_isolated(before, max_width)
        if image is None:
            return None
        try:
            after = describe_window(hwnd, expected_pid=process_id)
            if not _same_window(before, after):
                return None
            if max_width > 0 and image.width > max_width:
                resized = image.resize((max_width, max(1, round(image.height * max_width / image.width))))
                image.close()
                image = resized
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, "JPEG", quality=60, optimize=True, progressive=True)
            return destination
        finally:
            image.close()
    except (OSError, ValueError, TypeError, OverflowError):
        return None


if __name__ == "__main__" and sys.argv[1:] == ["--capture-window-worker"]:
    raise SystemExit(_capture_worker_main())
