"""A bounded, serialized ADB shell for small read-only telemetry requests."""
from __future__ import annotations

import queue
import subprocess
import threading
import time
import uuid


class TelemetryReadError(OSError):
    """A failed round must not update any counter baseline."""


class PersistentAdbShell:
    def __init__(self, adb, serial):
        self.adb, self.serial = adb, serial
        self._lock = threading.Lock()
        self._process = None
        self._reader = None
        self._lines = None
        self._closed = False
        self._diagnostic_lock = threading.Lock()
        self._stats = {"commands": 0, "timeouts": 0, "errors": 0, "spawns": 0,
                       "duration_ms_total": 0.0, "duration_ms_max": 0.0}

    @staticmethod
    def _read(process, lines):
        try:
            for line in process.stdout:
                try:
                    lines.put(line, timeout=0.2)
                except queue.Full:
                    process.kill()
                    break
        except (OSError, ValueError):
            pass
        finally:
            try: lines.put_nowait(None)
            except queue.Full: pass

    def _discard(self):
        process, self._process = self._process, None
        if process is not None:
            if process.poll() is None:
                process.kill()
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired: pass
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    try: stream.close()
                    except OSError: pass
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.join(timeout=0.5)
        self._lines = None

    def execute(self, script, timeout=4.0):
        started = time.monotonic()
        with self._diagnostic_lock:
            self._stats["commands"] += 1
        try:
            return self._execute(script, timeout)
        except TimeoutError:
            with self._diagnostic_lock:
                self._stats["timeouts"] += 1
            raise
        except (OSError, ValueError, RuntimeError):
            with self._diagnostic_lock:
                self._stats["errors"] += 1
            raise
        finally:
            duration = (time.monotonic() - started) * 1000.0
            with self._diagnostic_lock:
                self._stats["duration_ms_total"] += duration
                self._stats["duration_ms_max"] = max(self._stats["duration_ms_max"], duration)

    def diagnostics(self):
        # A different lock keeps this snapshot nonblocking during a slow read.
        with self._diagnostic_lock:
            result = dict(self._stats)
        process = self._process
        result.update({"connected": bool(process is not None and process.poll() is None
                                         and not self._closed), "closed": self._closed})
        return result

    def _execute(self, script, timeout=4.0):
        with self._lock:
            if self._closed:
                raise RuntimeError("ADB shell has been closed")
            try:
                if self._process is None or self._process.poll() is not None:
                    self._discard()
                    self._process = subprocess.Popen(
                        [self.adb, "-s", self.serial, "shell", "-T", "sh"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace", bufsize=1,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    with self._diagnostic_lock:
                        self._stats["spawns"] += 1
                    self._lines = queue.Queue(maxsize=1024)
                    self._reader = threading.Thread(target=self._read, args=(self._process, self._lines), daemon=True)
                    self._reader.start()
                token = "__PERFZY_REQUEST_" + uuid.uuid4().hex
                begin, end = token + "_BEGIN", token + "_END"
                # Subshell isolates variables and cwd between requests. Unique
                # markers separate partial, delayed and completed responses.
                payload = f"printf '%s\\n' '{begin}'\n(\n{script}\n)\nprintf '\\n%s:%s\\n' '{end}' \"$?\"\n"
                self._process.stdin.write(payload)
                self._process.stdin.flush()
                deadline = time.monotonic() + timeout
                collecting = False
                output = []
                size = 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("ADB telemetry request timed out")
                    try: line = self._lines.get(timeout=remaining)
                    except queue.Empty: raise TimeoutError("ADB telemetry request timed out") from None
                    if line is None:
                        raise OSError("ADB telemetry connection ended")
                    clean = line.rstrip("\r\n")
                    if clean == begin:
                        collecting = True
                        continue
                    if collecting and clean.startswith(end + ":"):
                        if clean != end + ":0":
                            raise OSError("ADB telemetry command failed: " + clean)
                        return "".join(output)
                    if collecting:
                        size += len(line)
                        if size > 1024 * 1024:
                            raise OSError("ADB telemetry response exceeds limit")
                        output.append(line)
            except (OSError, ValueError, TimeoutError):
                self._discard()
                raise

    def close(self):
        with self._lock:
            self._closed = True
            self._discard()
