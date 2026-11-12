"""All window APIs and pixels are simulated; no user window is captured."""
import ctypes
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from perfzy.capture import windows as capture


HWND = 0x1_0000_1234
CHILD = 0x1_0000_5678
PID = 4321


class FakeUser:
    def __init__(self):
        self.windows = {
            HWND: dict(pid=PID, title="Mock game", root=HWND, visible=True, iconic=False,
                       rect=(20, 30, 340, 230), kind="MockWindow", owner=0),
            CHILD: dict(pid=PID, title="Child", root=HWND, visible=True, iconic=False,
                        rect=(20, 30, 30, 40), kind="ChildWindow", owner=0),
        }
        self.hit = CHILD
        self.cursor_ok = True
        self.print_results = [True]
        self.draw = True
        self.pixel = bytes((10, 20, 30, 0))
        self.calls = []
        self.gdi = None

    def IsWindow(self, hwnd): return hwnd in self.windows
    def IsWindowVisible(self, hwnd): return self.windows[hwnd]["visible"]
    def IsIconic(self, hwnd): return self.windows[hwnd]["iconic"]
    def IsHungAppWindow(self, hwnd): return self.windows[hwnd].get("hung", False)
    def GetDesktopWindow(self): return 10
    def GetShellWindow(self): return 11
    def GetAncestor(self, hwnd, flag): return self.windows[hwnd]["root"] if hwnd in self.windows else 0
    def GetWindow(self, hwnd, flag): return self.windows[hwnd]["owner"]
    def GetWindowTextLengthW(self, hwnd): return len(self.windows[hwnd]["title"])

    def GetClassNameW(self, hwnd, buffer, length):
        buffer.value = self.windows[hwnd]["kind"]
        return len(buffer.value)

    def GetWindowTextW(self, hwnd, buffer, length):
        buffer.value = self.windows[hwnd]["title"]
        return len(buffer.value)

    def GetWindowThreadProcessId(self, hwnd, pointer):
        pointer._obj.value = self.windows.get(hwnd, {}).get("pid", 0)
        return 1

    def GetWindowRect(self, hwnd, pointer):
        rect = pointer._obj
        rect.left, rect.top, rect.right, rect.bottom = self.windows[hwnd]["rect"]
        return True

    def GetCursorPos(self, pointer):
        pointer._obj.x, pointer._obj.y = 33, 44
        return self.cursor_ok

    def WindowFromPoint(self, point):
        assert (point.x, point.y) == (33, 44)
        return self.hit

    def EnumWindows(self, callback, param):
        for hwnd in self.windows:
            callback(hwnd, param)
        return True

    def GetDC(self, hwnd):
        self.calls.append(("GetDC", hwnd))
        return 0x2_0000_0010

    def ReleaseDC(self, hwnd, dc):
        self.calls.append(("ReleaseDC", hwnd, dc))
        return 1

    def PrintWindow(self, hwnd, dc, flags):
        self.calls.append(("PrintWindow", hwnd, dc, flags))
        result = self.print_results.pop(0) if len(self.print_results) > 1 else self.print_results[0]
        if result and self.draw:
            ctypes.memmove(self.gdi.buffer, self.pixel * (len(self.gdi.buffer) // 4), len(self.gdi.buffer))
        return result


class FakeGdi:
    def __init__(self):
        self.calls = []
        self.buffer = None
        self.fail_dc = self.fail_bitmap = self.fail_select = False
        self.raise_print = False

    def CreateCompatibleDC(self, dc):
        self.calls.append(("CreateCompatibleDC", dc))
        return 0 if self.fail_dc else 0x3_0000_0020

    def CreateDIBSection(self, dc, info, usage, bits, handle, offset):
        self.calls.append(("CreateDIBSection", dc))
        if self.fail_bitmap:
            return 0
        assert info._obj.bmiHeader.biHeight == -2
        self.buffer = ctypes.create_string_buffer(info._obj.bmiHeader.biSizeImage)
        bits._obj.value = ctypes.addressof(self.buffer)
        return 0x4_0000_0030

    def SelectObject(self, dc, obj):
        self.calls.append(("SelectObject", dc, obj))
        return capture._INVALID_GDI_OBJECT if self.fail_select else 0x5_0000_0040

    def DeleteDC(self, dc):
        self.calls.append(("DeleteDC", dc))
        return 1

    def DeleteObject(self, obj):
        self.calls.append(("DeleteObject", obj))
        return 1


@pytest.fixture
def native(monkeypatch):
    user, gdi = FakeUser(), FakeGdi()
    user.gdi = gdi
    monkeypatch.setattr(capture, "user32", user)
    monkeypatch.setattr(capture, "gdi32", gdi)
    monkeypatch.setattr(capture, "os", SimpleNamespace(getpid=lambda: 111))
    monkeypatch.setattr(capture, "_process_identity", lambda pid: ("mock-game.exe", 123.5))
    return user, gdi


def test_cursor_child_resolves_to_64bit_root_and_metadata(native):
    user, _ = native
    result = capture.window_at_cursor()
    assert result == {
        "hwnd": HWND, "pid": PID, "title": "Mock game", "process_name": "mock-game.exe",
        "create_time": 123.5,
        "rect": {"left": 20, "top": 30, "right": 340, "bottom": 230, "width": 320, "height": 200},
    }
    assert capture.window_at_cursor(exclude_process_id=PID) is None
    assert not user.calls  # selection does not capture or operate a window


@pytest.mark.parametrize("change", [
    {"visible": False}, {"iconic": True}, {"hung": True}, {"pid": 111}, {"pid": 0},
    {"kind": "Progman"}, {"kind": "WorkerW"}, {"kind": "Shell_TrayWnd"},
    {"rect": (0, 0, 0, 100)}, {"root": CHILD},
])
def test_invalid_or_ineligible_window_is_not_selected(native, change):
    user, _ = native
    user.windows[HWND].update(change)
    assert capture.describe_window(HWND) is None


def test_missing_window_wrong_pid_and_cursor_failure(native):
    user, _ = native
    assert capture.describe_window(0) is None
    assert capture.describe_window(999) is None
    assert capture.describe_window(HWND, expected_pid=PID + 1) is None
    user.cursor_ok = False
    assert capture.window_at_cursor() is None
    user.cursor_ok = True
    user.hit = 0
    assert capture.window_at_cursor() is None


def test_window_identity_is_rechecked_after_metadata(native, monkeypatch):
    user, _ = native
    def identity(pid):
        user.windows[HWND]["pid"] = PID + 1
        return "changed.exe", 999.0
    monkeypatch.setattr(capture, "_process_identity", identity)
    assert capture.describe_window(HWND, expected_pid=PID) is None


def test_visible_list_and_default_largest_window(native):
    user, _ = native
    small = HWND + 1
    user.windows[small] = dict(user.windows[HWND], root=small, rect=(0, 0, 150, 150))
    assert [item["hwnd"] for item in capture.list_visible_windows()] == [HWND, small]
    assert capture.list_visible_windows(exclude_process_id=PID) == []
    assert capture.find_main_window(PID) == HWND
    user.windows[HWND]["owner"] = small
    assert capture.find_main_window(PID) == small


def test_printwindow_renders_pixels_and_releases_all_gdi(native):
    user, gdi = native
    image = capture._capture_window_content(HWND, 2, 2)
    assert image is not None
    try:
        assert image.getpixel((0, 0)) == (30, 20, 10)
    finally:
        image.close()
    assert ("PrintWindow", HWND, 0x3_0000_0020, 2) in user.calls
    assert ("SelectObject", 0x3_0000_0020, 0x5_0000_0040) in gdi.calls
    assert ("DeleteDC", 0x3_0000_0020) in gdi.calls
    assert ("DeleteObject", 0x4_0000_0030) in gdi.calls
    assert ("ReleaseDC", HWND, 0x2_0000_0010) in user.calls


@pytest.mark.parametrize("results", [[False, False], [True]])
def test_failed_or_undrawn_capture_is_none_with_cleanup(native, results):
    user, gdi = native
    user.print_results, user.draw = results, False
    assert capture._capture_window_content(HWND, 2, 2) is None
    assert any(call[0] == "DeleteObject" for call in gdi.calls)
    assert any(call[0] == "DeleteDC" for call in gdi.calls)
    assert any(call[0] == "ReleaseDC" for call in user.calls)


def test_legacy_printwindow_fallback_does_not_use_desktop(native):
    user, _ = native
    user.print_results = [False, True]
    image = capture._capture_window_content(HWND, 2, 2)
    assert image is not None
    image.close()
    assert [call[-1] for call in user.calls if call[0] == "PrintWindow"] == [2, 0]


def test_valid_black_scene_is_kept(native):
    user, _ = native
    user.pixel = b"\0\0\0\0"
    image = capture._capture_window_content(HWND, 2, 2)
    assert image is not None
    assert image.getpixel((0, 0)) == (0, 0, 0)
    image.close()


@pytest.mark.parametrize("failure", ["fail_dc", "fail_bitmap", "fail_select"])
def test_gdi_partial_initialization_releases_owned_resources(native, failure):
    user, gdi = native
    setattr(gdi, failure, True)
    assert capture._capture_window_content(HWND, 2, 2) is None
    assert any(call[0] == "ReleaseDC" for call in user.calls)
    if failure != "fail_dc":
        assert any(call[0] == "DeleteDC" for call in gdi.calls)
    if failure == "fail_select":
        assert any(call[0] == "DeleteObject" for call in gdi.calls)
    assert not any(call[0] == "PrintWindow" for call in user.calls)


def test_printwindow_exception_still_releases_resources(native, monkeypatch):
    user, gdi = native
    def fail(*args):
        raise OSError("target unavailable")
    monkeypatch.setattr(user, "PrintWindow", fail)
    assert capture._capture_window_content(HWND, 2, 2) is None
    assert any(call[0] == "DeleteObject" for call in gdi.calls)
    assert any(call[0] == "DeleteDC" for call in gdi.calls)
    assert any(call[0] == "ReleaseDC" for call in user.calls)


def test_capture_invalid_or_excessive_dimensions_does_not_allocate(native):
    user, _ = native
    assert capture._capture_window_content(HWND, 0, 2) is None
    assert capture._capture_window_content(HWND, 100000, 100000) is None
    assert not user.calls


def test_explicit_window_bypasses_default_and_saves_resized_content(native, monkeypatch, tmp_path):
    calls = []
    def image(window, max_width):
        calls.append((window["hwnd"], window["rect"]["width"], window["rect"]["height"]))
        return Image.new("RGB", (320, 200), "blue")
    monkeypatch.setattr(capture, "_capture_window_isolated", image)
    monkeypatch.setattr(capture, "find_main_window", lambda pid: pytest.fail("explicit target ignored"))
    destination = tmp_path / "shots" / "one.jpg"
    assert capture.capture_process_window(PID, destination, 80, window_handle=HWND) == destination
    assert calls == [(HWND, 320, 200)]
    with Image.open(destination) as saved:
        assert saved.size == (80, 50)


def test_default_capture_remains_backward_compatible(native, monkeypatch, tmp_path):
    monkeypatch.setattr(capture, "_capture_window_isolated", lambda *args: Image.new("RGB", (320, 200)))
    destination = tmp_path / "default.jpg"
    assert capture.capture_process_window(PID, destination) == destination


def test_invalid_explicit_window_does_not_fallback_or_capture(native, monkeypatch, tmp_path):
    monkeypatch.setattr(capture, "find_main_window", lambda pid: pytest.fail("unexpected fallback"))
    monkeypatch.setattr(capture, "_capture_window_isolated", lambda *args: pytest.fail("wrong PID captured"))
    assert capture.capture_process_window(PID + 1, tmp_path / "invalid.jpg", window_handle=HWND) is None
    assert not list(tmp_path.iterdir())


def test_process_reuse_during_capture_rejects_image(native, monkeypatch, tmp_path):
    identities = iter([("game.exe", 1.0), ("game.exe", 2.0)])
    monkeypatch.setattr(capture, "_process_identity", lambda pid: next(identities))
    monkeypatch.setattr(capture, "_capture_window_isolated", lambda *args: Image.new("RGB", (320, 200)))
    assert capture.capture_process_window(PID, tmp_path / "reused.jpg", window_handle=HWND) is None
    assert not list(tmp_path.iterdir())


def test_process_name_permission_failure_retains_safe_window_identity(monkeypatch):
    def denied(pid):
        raise capture.psutil.AccessDenied(pid)
    monkeypatch.setattr(capture.psutil, "Process", denied)
    assert capture._process_identity(PID) == ("", None)


def test_native_handle_prototypes_are_pointer_sized():
    # The fixture replaces Python references only; this inspects the configured
    # DLL functions, not a real target or screen.
    assert ctypes.sizeof(capture.ctypes.windll.user32.WindowFromPoint.restype) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(capture.ctypes.windll.gdi32.CreateDIBSection.restype) == ctypes.sizeof(ctypes.c_void_p)


def test_isolated_capture_uses_owned_python_process_with_deadline(native, monkeypatch):
    calls = []
    data = BytesIO()
    with Image.new("RGB", (80, 50), "red") as image:
        image.save(data, "PNG")
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=data.getvalue())
    monkeypatch.setattr(capture.subprocess, "run", run)
    monkeypatch.setattr(capture, "_capture_window_content", lambda *args: pytest.fail("in-process PrintWindow"))
    window = capture.describe_window(HWND)
    image = capture._capture_window_isolated(window, 80)
    assert image is not None
    assert image.size == (80, 50)
    image.close()
    command, kwargs = calls[0]
    assert command[0] == capture.sys.executable
    assert command[-1] == "--capture-window-worker"
    assert kwargs["timeout"] == capture.CAPTURE_TIMEOUT_S == 3.0
    assert kwargs["capture_output"] is True
    payload = json.loads(kwargs["input"])
    assert payload["pid"] == PID
    assert payload["hwnd"] == HWND
    assert payload["max_width"] == 80


def test_isolated_timeout_fails_cleanly_without_target_termination(native, monkeypatch):
    calls = []
    def timeout(command, **kwargs):
        calls.append(command)
        raise capture.subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(capture.subprocess, "run", timeout)
    monkeypatch.setattr(capture.psutil, "Process", lambda pid: pytest.fail("target process operation"))
    assert capture._capture_window_isolated(capture.describe_window(HWND), 80) is None
    assert len(calls) == 1


@pytest.mark.parametrize("result", [
    SimpleNamespace(returncode=1, stdout=b""),
    SimpleNamespace(returncode=0, stdout=b"not an image"),
])
def test_isolated_helper_errors_do_not_fallback(native, monkeypatch, result):
    monkeypatch.setattr(capture.subprocess, "run", lambda *args, **kwargs: result)
    assert capture._capture_window_isolated(capture.describe_window(HWND), 80) is None


def test_worker_validates_identity_renders_and_encodes(native, monkeypatch):
    request = dict(capture.describe_window(HWND), max_width=80)
    output = BytesIO()
    fake_sys = SimpleNamespace(stdin=SimpleNamespace(buffer=BytesIO(json.dumps(request).encode())),
                               stdout=SimpleNamespace(buffer=output))
    monkeypatch.setattr(capture, "sys", fake_sys)
    monkeypatch.setattr(capture, "_capture_window_content", lambda *args: Image.new("RGB", (320, 200), "blue"))
    assert capture._capture_worker_main() == 0
    with Image.open(BytesIO(output.getvalue())) as image:
        assert image.size == (80, 50)


def test_worker_rejects_reused_identity_before_printwindow(native, monkeypatch):
    request = dict(capture.describe_window(HWND), max_width=80)
    request["create_time"] += 1
    fake_sys = SimpleNamespace(stdin=SimpleNamespace(buffer=BytesIO(json.dumps(request).encode())),
                               stdout=SimpleNamespace(buffer=BytesIO()))
    monkeypatch.setattr(capture, "sys", fake_sys)
    monkeypatch.setattr(capture, "_capture_window_content", lambda *args: pytest.fail("wrong identity rendered"))
    assert capture._capture_worker_main() == 1


def test_worker_bounds_input(native, monkeypatch):
    fake_sys = SimpleNamespace(stdin=SimpleNamespace(buffer=BytesIO(b" " * 4097)),
                               stdout=SimpleNamespace(buffer=BytesIO()))
    monkeypatch.setattr(capture, "sys", fake_sys)
    assert capture._capture_worker_main() == 2


def test_pythonw_parent_selects_same_installation_python_with_no_window(monkeypatch):
    executable = Path(capture.sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip("this installation has no pythonw.exe")
    fake_sys = SimpleNamespace(executable=str(pythonw), frozen=False)
    monkeypatch.setattr(capture, "sys", fake_sys)
    command = capture._capture_worker_command()
    assert Path(command[0]) == pythonw.with_name("python.exe")
    assert command[-1] == "--capture-window-worker"


def test_frozen_worker_retains_packaged_entrypoint(monkeypatch):
    fake_sys = SimpleNamespace(executable=r"C:\Mock\PerfZY.exe", frozen=True)
    monkeypatch.setattr(capture, "sys", fake_sys)
    assert capture._capture_worker_command() == [fake_sys.executable, "--capture-window-worker"]


@pytest.mark.parametrize("missing", ["stdin", "stdout"])
def test_worker_missing_standard_stream_fails_explicitly(monkeypatch, missing):
    streams = {"stdin": SimpleNamespace(buffer=BytesIO(b"{}")),
               "stdout": SimpleNamespace(buffer=BytesIO())}
    streams[missing] = None
    monkeypatch.setattr(capture, "sys", SimpleNamespace(**streams))
    assert capture._capture_worker_main() == 2


def test_actual_pythonw_pipe_support_with_synthetic_input_only():
    """Launch our own Python process; do not enumerate or capture any window."""
    pythonw = Path(capture.sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip("this installation has no pythonw.exe")
    payload = b"PerfZY synthetic binary-pipe probe\x00\xff"
    code = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
    result = capture.subprocess.run([str(pythonw), "-c", code], input=payload,
                                    capture_output=True, timeout=3,
                                    creationflags=capture.subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0
    assert result.stdout == payload


def test_actual_worker_protocol_rejects_zero_handle_without_capture():
    """Invalid HWND proves helper startup/protocol without reading a screen."""
    request = {"hwnd": 0, "pid": 99999, "create_time": None, "rect": {}}
    result = capture.subprocess.run(capture._capture_worker_command(),
                                    input=json.dumps(request).encode(), capture_output=True,
                                    timeout=3, creationflags=capture.subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 1
    assert result.stdout == b""
