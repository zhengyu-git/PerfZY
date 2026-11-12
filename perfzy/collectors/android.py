from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from ..domain.models import Device, Sample, Target
from ..runtime_paths import find_tool
from ..domain.frame_statistics import FrameTimeDistribution
from ..domain.validation import MAX_FRAME_TIME_MS
from ..domain.jank import JANK_CONTRACT, JANK_METRICS
from .android_frames import AndroidPresentFrames, FRAME_SOURCE as PRESENT_SOURCE
from .base import Collector, FpsStabilizer
from .android_shell import PersistentAdbShell, TelemetryReadError
from .polling import SingleFlightPoller
from .discovery import discover_android
from .targets import android_targets


FRAME_METRICS = frozenset({"fps", "frame_time", "jank", "big_jank", "small_jank",
                           "frame_time_p95", "frame_time_p99", "jank_rate", "dropped_frames"}) | JANK_METRICS


class AndroidCollector(Collector):
    """ADB collector that shares each shell read across related metrics."""

    def __init__(self) -> None:
        super().__init__()
        self._adb = find_tool("adb") or ""
        self._serial = ""
        self._light_transport = None
        self._package = ""
        self._pid = ""
        self._frame_tracking = False
        self._frame_lock = threading.Lock()
        self._sample_executor: ThreadPoolExecutor | None = None
        self._memory_poller: SingleFlightPoller | None = None
        self._sample_interval_ms = 1000
        self._metric_sources = {}
        self._metric_source_counts = {}
        self._frame_baseline_resets = 0
        self._frame_reset_reason = ''
        self._frame_layer_switches = 0
        self._frame_counter_resets = 0
        self._memory_last_sample_elapsed: float | None = None
        self._memory_stopped_diagnostics: dict | None = None
        self._light_stopped_diagnostics: dict | None = None
        self._run_generation = 0
        self._shell_stats = {"commands": 0, "timeouts": 0, "errors": 0,
                             "duration_ms_total": 0.0, "duration_ms_max": 0.0}
        self._background_futures: dict[str, Future[str]] = {}
        self._background_cache: dict[str, str] = {}
        self._background_started: dict[str, float] = {}
        self._cpu_count = 1
        self._cpu_proc_available = None
        self._cpu_proc_retry_elapsed = 0.0
        self._cpu_proc_retry_delay = 5.0
        self._cpu_snapshot = None
        self._thermal_paths = []
        self._last_frame_completed = 0
        self._last_surface_frames = 0
        self._last_surface_name = ""
        self._last_surface_counts: dict[str, int] = {}
        self._display_refresh_rate = 60.0
        self._last_elapsed = 0.0
        self._last_network: tuple[int, int] | None = None
        self._last_network_elapsed = 0.0
        self._last_pid_refresh_elapsed = -10.0
        self._last_cpu_times: dict[int, tuple[int, int]] = {}
        self._baseline_pss_mb: float | None = None
        self._last_gpu_elapsed = 0.0
        self._last_surface_histogram: dict[int, int] = {}
        self._fps_stabilizer = FpsStabilizer()
        self._present_jank = AndroidPresentFrames()
        self._present_layer = ''
        self._jank_reset_pending = False
        self._connection_error = ""
        self._timeout_counts: dict[tuple[str, ...], int] = {}
        self._timeout_lock = threading.Lock()

    def _shell(self, *arguments: str, timeout: float = 4.0) -> str:
        if not self._adb:
            return ""
        started = time.monotonic()
        generation, serial = self._run_generation, self._serial
        with self._timeout_lock:
            self._shell_stats["commands"] += 1
        try:
            remote_arguments = list(arguments)
            if len(remote_arguments) >= 3 and remote_arguments[0:2] == ["sh", "-c"]:
                remote_arguments[2] = shlex.quote(remote_arguments[2])
            result = subprocess.run(
                [self._adb, "-s", serial, "shell", *remote_arguments],
                capture_output=True, text=True, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            with self._timeout_lock:
                if generation != self._run_generation:
                    return ""
                self._shell_stats["timeouts"] += 1
                key = tuple(arguments)
                count = self._timeout_counts.get(key, 0) + 1
                self._timeout_counts[key] = count
                if count >= 3:
                    self._connection_error = "Android ADB 同一读取命令连续超时 3 次，采集已中断，请检查设备连接"
            return ""
        except (OSError, subprocess.SubprocessError):
            with self._timeout_lock:
                if generation == self._run_generation:
                    self._shell_stats["errors"] += 1
            return ""
        finally:
            milliseconds = (time.monotonic() - started) * 1000.0
            with self._timeout_lock:
                if generation == self._run_generation:
                    self._shell_stats["duration_ms_total"] += milliseconds
                    self._shell_stats["duration_ms_max"] = max(
                        self._shell_stats["duration_ms_max"], milliseconds)
        with self._timeout_lock:
            if generation != self._run_generation:
                return ""
            self._timeout_counts.pop(tuple(arguments), None)
            if result.returncode != 0:
                self._shell_stats["errors"] += 1
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            folded = detail.casefold()
            # Only transport errors are fatal. Permission-denied counters and
            # unsupported dumpsys commands must remain optional empty readings.
            if ("device offline" in folded or "device unauthorized" in folded
                    or "no devices/emulators found" in folded
                    or ("device '" in folded and "' not found" in folded)
                    or "device not found" in folded):
                self._connection_error = f"Android ADB 连接已断开或未授权：{detail}"
        return result.stdout if result.returncode == 0 else ""

    def discover_devices(self): return discover_android()
    def discover_targets(self, device: Device): return android_targets(device)

    def set_capture_paused(self, paused):
        self._jank_reset_pending = True

    def set_sample_interval_ms(self, interval: int) -> None:
        """Align freshness with caller cadence without throttling memory reads."""
        if int(interval) <= 0:
            raise ValueError("sample interval must be positive")
        self._sample_interval_ms = int(interval)
        poller = self._memory_poller
        if poller is not None:
            poller.set_max_age(max(5.0, self._sample_interval_ms / 1000.0 * 2))

    def start(self, device: Device, target: Target, enabled_metrics: frozenset[str]) -> None:
        if not self._adb:
            raise RuntimeError("未找到 adb，请安装 Android platform-tools 并加入 PATH")
        self._metric_sources = {}
        self._metric_source_counts = {}
        self._frame_baseline_resets = 0
        self._frame_reset_reason = ''
        self._frame_layer_switches = self._frame_counter_resets = 0
        with self._timeout_lock:
            self._run_generation += 1
            self._timeout_counts.clear()
            self._shell_stats = {"commands": 0, "timeouts": 0, "errors": 0,
                                 "duration_ms_total": 0.0, "duration_ms_max": 0.0}
        if self._memory_poller is not None:
            self._memory_poller.close()
            self._memory_poller = None
        if self._sample_executor is not None:
            self._sample_executor.shutdown(wait=False, cancel_futures=True)
            self._sample_executor = None
        self._memory_last_sample_elapsed = None
        self._memory_stopped_diagnostics = None
        self._light_stopped_diagnostics = None
        self._connection_error = ""
        if self._light_transport is not None:
            self._light_transport.close()
        self._light_transport = None
        self._serial = device.id.partition(":")[2]
        self._package = target.id.rsplit(":", 1)[-1]
        self._enabled = enabled_metrics
        self._cpu_proc_available = None
        self._cpu_proc_retry_elapsed = 0.0
        self._cpu_proc_retry_delay = 5.0
        self._cpu_snapshot = None
        self._thermal_paths = []
        self._background_futures.clear()
        self._background_cache.clear()
        self._background_started.clear()
        try:
            self._cpu_count = max(1, int(device.details.get("cpu_cores", 1)))
        except (TypeError, ValueError):
            self._cpu_count = 1
        pids = self._shell("pidof", self._package).strip().split()
        if self._connection_error:
            raise RuntimeError(self._connection_error)
        self._pid = self._resolve_pid(pids)
        if not self._pid:
            raise RuntimeError("目标应用尚未运行")
        self._sample_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="PerfZY-ADB")
        self._memory_poller = SingleFlightPoller(
            max_age_s=max(5.0, self._sample_interval_ms / 1000.0 * 2))
        self._light_transport = PersistentAdbShell(self._adb, self._serial)
        self._frame_tracking = False
        self._last_frame_completed = 0
        self._last_surface_frames = 0
        self._last_surface_name = ""
        self._last_surface_counts = {}
        self._last_elapsed = 0.0
        self._last_network = None
        self._last_network_elapsed = 0.0
        self._last_pid_refresh_elapsed = -10.0
        self._last_cpu_times = {}
        self._baseline_pss_mb = None
        self._last_gpu_elapsed = 0.0
        self._last_surface_histogram = {}
        self._fps_stabilizer.reset()
        if FRAME_METRICS & enabled_metrics:
            self._enable_frame_tracking()

    def _resolve_pid(self, candidates):
        candidates = [pid for pid in candidates if pid.isdigit()]
        if len(candidates) <= 1:
            return candidates[0] if candidates else ""
        records = self._shell("dumpsys", "activity", "processes", timeout=5.0)
        matches = re.findall(r"ProcessRecord\{[^}]*?\s(\d+):" + re.escape(self._package) + r"/", records)
        registered = set(matches) & set(candidates)
        if len(registered) == 1:
            return registered.pop()
        raise RuntimeError("目标包名对应多个进程，无法唯一确认 Android 登记的主进程")

    def _refresh_pid(self, elapsed: float) -> None:
        """Recover all process-backed metrics, at most once every five seconds.

        Refresh between rounds so prefetched responses from the previous PID
        cannot be interpreted against the replacement process's baselines.
        """
        process_metrics = {
            "app_cpu", "total_cpu", "app_rss", "app_threads", "app_gpu_memory",
            "memory", "java_heap", "native_heap", "graphics_memory", "swap_memory",
            "app_uss", "memory_growth", "network_send", "network_recv",
            *FRAME_METRICS,
        }
        if (not process_metrics & self._enabled or not self._package
                or elapsed - self._last_pid_refresh_elapsed < 5.0):
            return
        self._last_pid_refresh_elapsed = elapsed
        new_pid = self._resolve_pid(self._shell("pidof", self._package).strip().split())
        if not new_pid or new_pid == self._pid:
            return
        self._pid = new_pid
        self._cpu_snapshot = None
        self._cpu_proc_available = None
        self._cpu_proc_retry_elapsed = 0.0
        self._cpu_proc_retry_delay = 5.0
        self._last_network = None
        self._last_network_elapsed = elapsed
        self._baseline_pss_mb = None
        self._reset_frame_baselines('target_pid_changed')

    def _reset_frame_baselines(self, reason):
        self._present_jank.reset()
        self._present_layer = ''
        """Never compare restarted frame sources with the preceding run."""
        self._last_frame_completed = 0
        self._last_surface_frames = 0
        self._last_surface_name = ''
        self._last_surface_counts = {}
        self._last_surface_histogram = {}
        self._last_elapsed = 0.
        self._fps_stabilizer.reset()
        self._frame_baseline_resets += 1
        self._frame_reset_reason = reason

    def activate_pending_sources(self) -> None:
        if FRAME_METRICS & self._enabled:
            self._enable_frame_tracking()
        else:
            with self._frame_lock:
                if self._frame_tracking:
                    self._shell("dumpsys", "SurfaceFlinger", "--timestats", "-disable", timeout=3.0)
                    self._frame_tracking = False
                    self._reset_frame_baselines('frame_source_disabled')

    def _enable_frame_tracking(self) -> None:
        """Turn on SurfaceFlinger/gfxinfo frame statistics, once, on demand.

        Frame metrics can be selected after collection has begun, and without
        this the device would never have been asked to record timestats.
        """
        with self._frame_lock:
            if self._frame_tracking or not self._serial:
                return
            self._shell("dumpsys", "gfxinfo", self._package, "reset", timeout=4.0)
            self._shell("dumpsys", "SurfaceFlinger", "--timestats", "-clear", timeout=4.0)
            self._shell("dumpsys", "SurfaceFlinger", "--timestats", "-enable", timeout=4.0)
            display = self._shell("dumpsys", "display", timeout=5.0)
            refresh = re.search(r"renderFrameRate\s+([\d.]+)", display)
            if not refresh:
                refresh = re.search(r"mActiveSfDisplayMode=.*?(?:fps|peakRefreshRate)=([\d.]+)", display)
            self._display_refresh_rate = float(refresh.group(1)) if refresh else 60.0
            self._reset_frame_baselines('frame_source_enabled')
            self._frame_tracking = True

    def _background_output(self, name: str, *arguments: str,
                           refresh_seconds: float, timeout: float) -> str:
        """Refresh one slow diagnostic without blocking the main sample."""
        executor = self._sample_executor
        if executor is None:
            return self._shell(*arguments, timeout=timeout)
        future = self._background_futures.get(name)
        if future is not None and future.done():
            try:
                self._background_cache[name] = future.result()
            except Exception:
                self._background_cache[name] = ""
            self._background_futures.pop(name, None)
            future = None
        now = time.monotonic()
        if (future is None and
                now - self._background_started.get(name, -refresh_seconds) >= refresh_seconds):
            self._background_started[name] = now
            self._background_futures[name] = executor.submit(
                self._read_thermal if name == "thermal" else self._shell, *arguments, timeout=timeout)
        return self._background_cache.pop(name, "")

    @staticmethod
    def _parse_framestats(text: str, last_completed: int) -> list[tuple[int, float]]:
        """Parse complete, plausible gfxinfo frames across Android schemas."""
        frames: list[tuple[int, float]] = []
        inside = False
        indexes: tuple[int, int, int] | None = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line == "---PROFILEDATA---":
                inside = not inside
                indexes = None
                continue
            if not inside or not line:
                continue
            columns = [part.strip() for part in line.split(",")]
            normalized = [re.sub(r"[^a-z0-9]", "", part.casefold())
                          for part in columns]
            if normalized and normalized[0] == "flags":
                try:
                    flags_index = normalized.index("flags")
                    intended_index = normalized.index("intendedvsync")
                    completed_index = normalized.index("framecompleted")
                except ValueError:
                    indexes = None
                else:
                    indexes = (flags_index, intended_index, completed_index)
                continue
            if not line[0].isdigit():
                continue
            # Header-aware parsing handles both the classic 16-column schema
            # and newer Android releases. Keep the former index-16 fallback
            # for vendor dumps that omit the header entirely.
            row_indexes = indexes or ((0, 1, 16) if len(columns) >= 17 else None)
            if row_indexes is None or max(row_indexes) >= len(columns):
                continue
            try:
                flags = int(columns[row_indexes[0]])
                intended = int(columns[row_indexes[1]])
                completed = int(columns[row_indexes[2]])
            except ValueError:
                continue
            # Zero is Android's incomplete-frame sentinel. Also reject absurd
            # timestamp sentinels while retaining complete long frames for review.
            if (flags != 0 or intended <= 0 or completed <= intended or
                    completed >= (1 << 63) - 1 or completed <= last_completed):
                continue
            milliseconds = (completed - intended) / 1_000_000.0
            if not 0.0 < milliseconds <= MAX_FRAME_TIME_MS:
                continue
            frames.append((completed, milliseconds))
        return frames

    @staticmethod
    def _cpu_hardware_query(enabled):
        parts = []
        if "cpu_online_cores" in enabled:
            parts.append("online=; IFS= read -r online < /sys/devices/system/cpu/online; echo ONLINE=$online")
        aggregate = bool({"cpu_frequency", "cpu_throttling"} & enabled)
        cores = sorted({int(match.group(1)) for key in enabled
                        if (match := re.fullmatch(r"cpu_core_([0-9]+)_clock", key))})
        if aggregate or cores:
            paths = ("/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq" if aggregate else
                     " ".join(f"/sys/devices/system/cpu/cpu{core}/cpufreq/scaling_cur_freq" for core in cores))
            maximum = ("if [ -r ${f%/*}/cpuinfo_max_freq ]; then IFS= read -r m < ${f%/*}/cpuinfo_max_freq; fi; "
                       if "cpu_throttling" in enabled else "")
            parts.append("for f in " + paths + "; do c=; m=; "
                         "if [ -r $f ]; then IFS= read -r c < $f; fi; " + maximum +
                         "echo $f:$c:$m; done")
        return "; ".join(parts) or "true"

    @staticmethod
    def _gpu_query(enabled):
        parts = []
        if {"gpu", "gpu_busy_time"} & enabled:
            parts.append("busy=; if [ -r /sys/class/kgsl/kgsl-3d0/gpubusy ]; then IFS= read -r busy < /sys/class/kgsl/kgsl-3d0/gpubusy; fi; echo busy=$busy")
        for key, node, needed in (
            ("cur", "cur_freq", {"gpu_frequency", "gpu_clock_load"}),
            ("max", "max_freq", {"gpu_max_frequency", "gpu_clock_load"}),
        ):
            if needed & enabled:
                parts.append(f"{key}=; for f in /sys/class/kgsl/kgsl-3d0/devfreq/{node} /sys/class/devfreq/*gpu*/{node}; do if [ -r $f ]; then IFS= read -r {key} < $f; break; fi; done; echo {key}=${key}")
        return "; ".join(parts) or "true"

    def _read_thermal(self, *arguments, timeout=8.0):
        if not self._thermal_paths:
            raw = self._shell("sh", "-c", "for z in /sys/class/thermal/thermal_zone*; do t=; IFS= read -r t < $z/type; case $t in *cpu*|*CPU*|*cpuss*|*CPUSS*|*gpu*|*GPU*|*gpuss*|*GPUSS*) v=; IFS= read -r v < $z/temp; echo $z/temp:$t:$v;; esac; done", timeout=timeout)
            rows = []
            paths = []
            for line in raw.splitlines():
                match = re.fullmatch(r"(/sys/class/thermal/thermal_zone[0-9]+/temp):([^:]+):(-?[0-9]+)", line)
                if match:
                    path, kind, value = match.groups()
                    paths.append((path, kind)); rows.append(f"{kind}:{value}")
            self._thermal_paths = paths
            return "\n".join(rows)
        script = "; ".join("v=; IFS= read -r v < " + shlex.quote(path) + "; echo " + shlex.quote(kind + ":") + "$v" for path, kind in self._thermal_paths)
        raw = self._shell("sh", "-c", script, timeout=timeout)
        if any(not re.search(r":-?[0-9]+$", line) for line in raw.splitlines()) or not raw.strip():
            self._thermal_paths = []
        return raw

    def _light_batch(self, requests):
        """One ADB connection per tick for read-only procfs/sysfs counters."""
        requests = dict(requests)
        shared_cpu = "cpu_snapshot" in requests and "proc_stat" in requests
        if shared_cpu:
            args = requests["cpu_snapshot"]
            requests["cpu_snapshot"] = ("sh", "-c", args[2].replace("head -n 1 /proc/stat", "cat /proc/stat"))
            requests.pop("proc_stat")
        fragments = []
        for name, arguments in requests.items():
            command = arguments[2] if arguments[:2] == ("sh", "-c") else shlex.join(arguments)
            fragments.append("printf '\\n__PERFZY_" + name + "__\\n'; { " + command + "; } 2>/dev/null")
        script = "; ".join(fragments) + "; true"
        transport = self._light_transport
        if transport is not None:
            try:
                raw = transport.execute(script, timeout=4.0)
            except (OSError, ValueError, TimeoutError, RuntimeError):
                # Discard the incomplete round. Use the old transport next
                # tick, avoiding a second read of reset-on-read GPU counters.
                transport.close()
                if self._light_transport is transport: self._light_transport = None
                raise TelemetryReadError("ADB 轻量查询连接失效，本轮无数据，下轮回退") from None
        else:
            raw = self._shell("sh", "-c", script, timeout=4.0)
        parts = re.split(r"(?:^|\n)__PERFZY_([a-z_]+)__\r?\n", raw)
        result = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
        if shared_cpu: result["proc_stat"] = result.get("cpu_snapshot", "")
        return result

    @staticmethod
    def _parse_cpu_snapshot(text):
        """Whole-device CPU shares from kernel ticks; exclude guest double counts."""
        try:
            lines = text.splitlines()
            system = next(line for line in lines if line.startswith("cpu "))
            ticks = [int(value) for value in system.split()[1:9]]
            process = next(line for line in lines if ")" in line and not line.startswith("cpu"))
            fields = process.rsplit(")", 1)[1].split()
            identity = (int(process.split("(", 1)[0]), int(fields[19]))
            return sum(ticks), ticks[3] + ticks[4], int(fields[11]) + int(fields[12]), identity
        except (ValueError, IndexError, StopIteration):
            return None

    def sample(self, elapsed: float) -> Sample:
        try:
            return self._sample_impl(elapsed)
        except TelemetryReadError:
            self._metric_sources = {}
            # A transport gap must not turn the next counter delta into a
            # deceptively normal window spanning missing time.
            self._reset_frame_baselines('transport_read_gap')
            return Sample(elapsed, {key: None for key in self._enabled})

    def _sample_impl(self, elapsed: float) -> Sample:
        if self._jank_reset_pending:
            self._reset_frame_baselines('capture_pause_or_resume')
            self._present_jank.layer = ''
            self._jank_reset_pending = False
        tick_monotonic = time.monotonic()
        raw_frames = []
        raw_frame_values = {}
        metric_sources = {}
        if self._connection_error:
            raise RuntimeError(self._connection_error)
        # A frame metric can be enabled while a collection is running. Ensure
        # the device source is ready before the first corresponding sample;
        # the lock makes this safe beside the controller's activation worker.
        if FRAME_METRICS & self._enabled and not self._frame_tracking:
            self._enable_frame_tracking()
        values: dict[str, float | None] = {}
        values.update({key: None for key in self._enabled & JANK_METRICS})
        observed_elapsed: dict[str, float] = {}
        memory_checked = False
        memory_reading = None
        futures: dict[str, Future[str]] = {}
        planned = {}
        light = {}
        batch_future = None
        light_names = {"cpu_snapshot", "proc_stat", "status", "current", "cpu_hardware", "gpu", "network"}

        def memory_output() -> str:
            nonlocal memory_checked, memory_reading
            poller = self._memory_poller
            if poller is None:
                return self._shell("dumpsys", "meminfo", "--local", self._package, timeout=4.0)
            if not memory_checked:
                memory_checked = True
                package = self._package
                memory_reading = poller.poll(
                    lambda: self._shell("dumpsys", "meminfo", "--local", package, timeout=4.0),
                    (self._run_generation, self._serial, package, self._pid))
                if memory_reading is not None:
                    self._memory_last_sample_elapsed = max(
                        0.0, elapsed + memory_reading.completed - tick_monotonic)
            return memory_reading.value if memory_reading is not None else ""

        def prefetch(name: str, *arguments: str, timeout: float = 3.0) -> None:
            planned[name] = (arguments, timeout)
            executor = self._sample_executor
            if executor is not None:
                if name == "meminfo" and self._memory_poller is not None:
                    memory_output()
                elif name in light_names:
                    light[name] = arguments
                else:
                    futures[name] = executor.submit(self._shell, *arguments, timeout=timeout)

        def output(name: str, *arguments: str, timeout: float = 3.0) -> str:
            nonlocal batch_future
            if name == "meminfo" and self._memory_poller is not None:
                return memory_output()
            if light and batch_future is None:
                batch_future = self._sample_executor.submit(self._light_batch, dict(light))
            if name in light:
                try:
                    return batch_future.result().get(name, "")
                except TelemetryReadError:
                    for pending in futures.values():
                        try: pending.result()
                        except Exception: pass
                    raise
            future = futures.get(name)
            if future is not None:
                return future.result()
            arguments, timeout = planned.get(name, (arguments, timeout))
            return self._shell(*arguments, timeout=timeout)

        needs_distribution = bool((FRAME_METRICS - {"fps", "frame_time"} - JANK_METRICS) & self._enabled)
        if FRAME_METRICS & self._enabled:
            prefetch("surface", "dumpsys", "SurfaceFlinger", "--timestats", "-dump", timeout=5.0)
            if needs_distribution:
                prefetch("framestats", "dumpsys", "gfxinfo", self._package, "framestats", timeout=5.0)
        probe_cpu = (self._cpu_proc_available is not False
                     or elapsed >= self._cpu_proc_retry_elapsed)
        if {"app_cpu", "total_cpu"} & self._enabled:
            if probe_cpu:
                prefetch("cpu_snapshot", "sh", "-c", f"head -n 1 /proc/stat; cat /proc/{self._pid}/stat")
            else:
                prefetch("top", "top", "-b", "-n", "1", "-p", self._pid, timeout=4.0)
        if any(key.startswith("cpu_core_") and key.endswith("_usage") for key in self._enabled):
            prefetch("proc_stat", "cat", "/proc/stat")
        memory_keys = {"memory", "java_heap", "native_heap", "graphics_memory", "swap_memory", "app_uss", "memory_growth"}
        if (memory_keys | {"app_gpu_memory"}) & self._enabled:
            if self._enabled & {"memory", "java_heap", "native_heap", "graphics_memory", "swap_memory", "app_uss", "memory_growth"}:
                prefetch("meminfo", "dumpsys", "meminfo", "--local", self._package, timeout=4.0)
            if "app_gpu_memory" in self._enabled:
                prefetch("gpu_dump", "dumpsys", "gpu", timeout=4.0)
        if {"app_rss", "app_threads"} & self._enabled:
            prefetch("status", "cat", f"/proc/{self._pid}/status")
        if {"temperature", "power", "battery_level", "battery_current", "battery_voltage"} & self._enabled:
            prefetch("battery", "dumpsys", "battery")
            if {"power", "battery_current"} & self._enabled:
                prefetch("current", "cat", "/sys/class/power_supply/battery/current_now")
        cpu_hardware_enabled = (bool({"cpu_frequency", "cpu_online_cores", "cpu_throttling"}
                                     & self._enabled) or
                                any(key.startswith("cpu_core_") and key.endswith("_clock")
                                    for key in self._enabled))
        if cpu_hardware_enabled:
            prefetch("cpu_hardware", "sh", "-c", self._cpu_hardware_query(self._enabled))
        if {"gpu", "gpu_frequency", "gpu_max_frequency", "gpu_clock_load", "gpu_busy_time"} & self._enabled:
            prefetch("gpu", "sh", "-c", self._gpu_query(self._enabled))
        if {"network_send", "network_recv"} & self._enabled:
            prefetch("network", "cat", f"/proc/{self._pid}/net/dev")

        if FRAME_METRICS & self._enabled:
            surface = output("surface", "dumpsys", "SurfaceFlinger", "--timestats", "-dump", timeout=5.0)
            blocks = re.split(r"(?=layerName\s*=)", surface)
            surface_rows: list[tuple[str, int, float | None, str]] = []
            for block in blocks:
                name = re.search(r"layerName\s*=\s*(.+)", block)
                if not name or self._package.casefold() not in name.group(1).casefold():
                    continue
                count = re.search(r"totalFrames\s*=\s*(\d+)", block)
                average = re.search(r"averageFPS\s*=\s*([\d.]+)", block)
                if count:
                    surface_rows.append((name.group(1).strip(), int(count.group(1)),
                                         float(average.group(1)) if average else None, block))
            active_rows = [
                row for row in surface_rows
                if row[1] > self._last_surface_counts.get(row[0], row[1])
            ]
            if active_rows:
                surface_name, surface_frames, surface_average, surface_block = max(
                    active_rows,
                    key=lambda row: row[1] - self._last_surface_counts.get(row[0], row[1]))
            else:
                previous_row = next(
                    (row for row in surface_rows if row[0] == self._last_surface_name), None)
                surface_name, surface_frames, surface_average, surface_block = (
                    previous_row or max(surface_rows, default=("", 0, None, ""),
                                        key=lambda row: row[1]))
            if self._last_surface_name and surface_name and surface_name != self._last_surface_name:
                self._frame_layer_switches += 1
                self._fps_stabilizer.reset()
            previous_count = self._last_surface_counts.get(surface_name)
            if previous_count is not None and surface_frames < previous_count:
                self._frame_counter_resets += 1
                self._reset_frame_baselines('surface_counter_reset')
            framestats = output("framestats", "dumpsys", "gfxinfo", self._package, "framestats", timeout=5.0) if (not surface_rows or needs_distribution) else ""
            frames = self._parse_framestats(framestats, self._last_frame_completed)
            if self._last_elapsed == 0.0 or (surface_rows and not self._last_surface_counts):
                # The first dump contains pre-capture history, not this window.
                if frames: self._last_frame_completed = max(item[0] for item in frames)
                frames = []
            window = max(.001, elapsed - self._last_elapsed)
            frame_times = [item[1] for item in frames]
            same_surface = bool(surface_name and surface_name == self._last_surface_name)
            histogram_match = re.search(r"present2present histogram is as below:\s*(.*?)(?=latch2present histogram|desired2present histogram|\Z)", surface_block, re.S | re.I)
            current_histogram = ({int(ms): int(count) for ms, count in re.findall(r"(\d+)ms=(\d+)", histogram_match.group(1))}
                                 if histogram_match else {})
            histogram_delta = {}
            histogram_continues = (
                same_surface and current_histogram and self._last_surface_histogram
                and all(current_histogram.get(ms, 0) >= count
                        for ms, count in self._last_surface_histogram.items()))
            if not frame_times and histogram_continues:
                histogram_delta = {
                    ms: count - self._last_surface_histogram.get(ms, 0)
                    for ms, count in current_histogram.items()}
            distribution = (FrameTimeDistribution.from_samples(frame_times) if frame_times
                            else FrameTimeDistribution(histogram_delta))
            previous_surface_frames = self._last_surface_counts.get(surface_name)
            surface_delta = (max(0, surface_frames - previous_surface_frames)
                             if previous_surface_frames is not None else 0)
            # A valid unchanged counter means zero presents in this window.
            # Missing/new/reset counters have no comparable baseline.
            counter_window_valid = (previous_surface_frames is not None and
                                    surface_frames >= previous_surface_frames and
                                    self._last_elapsed > 0.0)
            delta_fps = surface_delta / window if counter_window_valid else None
            if 'fps' in self._enabled:
                raw_frame_values['fps'] = delta_fps
            physical_limit = max(30.0, self._display_refresh_rate) * 1.10
            if delta_fps is not None and delta_fps <= physical_limit:
                measured_fps = delta_fps
            else:
                measured_fps = None
            measured_fps = self._fps_stabilizer.accept(measured_fps)
            if "fps" in self._enabled: values["fps"] = measured_fps
            if measured_fps is not None:
                metric_sources["fps"] = "surfaceflinger_counter"
            distribution_source = ("gfxinfo_framestats" if frame_times else
                                   "surface_histogram" if distribution.count else "")
            for key in FRAME_METRICS - {"fps", "frame_time"}:
                if key in self._enabled and distribution_source:
                    metric_sources[key] = distribution_source
            if "frame_time" in self._enabled:
                raw_frame_values['frame_time'] = (distribution.mean if distribution.count
                    else (1000.0 / delta_fps if delta_fps else None))
                values["frame_time"] = (distribution.mean if distribution.count
                                        else (1000.0 / measured_fps if measured_fps else None))
                if values["frame_time"] is not None:
                    metric_sources["frame_time"] = distribution_source or "fps_estimate"
            if distribution.count:
                if "small_jank" in self._enabled: values["small_jank"] = float(distribution.between(16.67, 33.34))
                if "jank" in self._enabled: values["jank"] = float(distribution.between(33.34, 50.01))
                if "big_jank" in self._enabled: values["big_jank"] = float(distribution.between(50.01))
                if "frame_time_p95" in self._enabled: values["frame_time_p95"] = distribution.percentile(.95)
                if "frame_time_p99" in self._enabled: values["frame_time_p99"] = distribution.percentile(.99)
                if "jank_rate" in self._enabled:
                    values["jank_rate"] = distribution.between(33.34) / distribution.count * 100.0
                if "dropped_frames" in self._enabled:
                    refresh_budget = 1000.0 / max(1.0, self._display_refresh_rate)
                    values["dropped_frames"] = distribution.dropped_frames(refresh_budget)
            if frames: self._last_frame_completed = max(item[0] for item in frames)
            if frames:
                newest = max(item[0] for item in frames)
                raw_frames = [dict(x=max(0., elapsed-(newest-stamp)/1e9), frame_ms=ms,
                    source='Android gfxinfo framestats', source_time_s=stamp/1e9,
                    clock='device_relative_latest_frame_receipt_estimate', pid=int(self._pid or 0))
                    for stamp, ms in frames]
            if surface_rows:
                self._last_surface_frames = surface_frames
                self._last_surface_name = surface_name
                self._last_surface_histogram = current_histogram
            self._last_surface_counts = {row[0]: row[1] for row in surface_rows}
            if self._enabled & JANK_METRICS:
                layer = surface_name or self._present_layer
                if not layer:
                    listing = self._background_output('present_layers', 'dumpsys', 'SurfaceFlinger', '--list', timeout=2.0, refresh_seconds=5.0)
                    candidates = [name.strip() for name in listing.splitlines()
                                  if self._package.casefold() in name.casefold()]
                    layer = max(candidates, key=lambda name: ('SurfaceView' in name, len(name)), default='')
                self._present_layer = layer
                if layer:
                    if layer != self._present_jank.layer:
                        self._background_cache.pop('present_latency', None)
                        old = self._background_futures.pop('present_latency', None)
                        if old is not None:
                            old.cancel()
                        self._background_started.pop('present_latency', None)
                    text = self._background_output('present_latency', 'sh', '-c',
                        'dumpsys SurfaceFlinger --latency ' + shlex.quote(layer), timeout=2.0, refresh_seconds=.5)
                    presents, jank_values = self._present_jank.read(text, layer, elapsed)
                    raw_frames.extend(presents)
                    for key in self._enabled & JANK_METRICS:
                        values[key] = jank_values[key]
                        metric_sources[key] = PRESENT_SOURCE
            else:
                self._present_jank.reset()
            self._last_elapsed = elapsed
        if {"app_cpu", "total_cpu"} & self._enabled:
            snapshot = None
            if probe_cpu:
                raw = output("cpu_snapshot", "sh", "-c", f"head -n 1 /proc/stat; cat /proc/{self._pid}/stat")
                snapshot = self._parse_cpu_snapshot(raw)
                self._cpu_proc_available = snapshot is not None
                if snapshot is None:
                    self._cpu_proc_retry_elapsed = elapsed + self._cpu_proc_retry_delay
                    self._cpu_proc_retry_delay = min(60.0, self._cpu_proc_retry_delay * 2.0)
                else:
                    self._cpu_proc_retry_elapsed = 0.0
                    self._cpu_proc_retry_delay = 5.0
            if snapshot is not None:
                total, idle, used, identity = snapshot
                previous = self._cpu_snapshot
                if "app_cpu" in self._enabled: values["app_cpu"] = None
                if "total_cpu" in self._enabled: values["total_cpu"] = None
                if previous is not None and previous[3] == identity and total > previous[0]:
                    delta = total - previous[0]
                    if "app_cpu" in self._enabled and used >= previous[2]:
                        values["app_cpu"] = min(100.0, (used - previous[2]) * 100.0 / delta)
                    if "total_cpu" in self._enabled and idle >= previous[1]:
                        values["total_cpu"] = max(0.0, min(100.0, (delta - idle + previous[1]) * 100.0 / delta))
                self._cpu_snapshot = snapshot
            else:
                top = output("top", "top", "-b", "-n", "1", "-p", self._pid, timeout=4.0)
                process_line = next((line for line in top.splitlines() if re.search(rf"\b{re.escape(self._pid)}\b", line)), "")
                columns = process_line.split()
                cpu_line = next((line for line in top.splitlines() if "%cpu" in line.lower()), "")
                capacity = re.search(r"(\d+(?:\.\d+)?)%cpu", cpu_line, re.I)
                reported_cores = float(capacity.group(1)) / 100.0 if capacity else 0.0
                normalization_cores = max(1.0, float(self._cpu_count), reported_cores)
                if "app_cpu" in self._enabled:
                    try:
                        values["app_cpu"] = min(100.0, max(0.0, float(columns[8]) / normalization_cores))
                    except (IndexError, ValueError):
                        values["app_cpu"] = None
                if "total_cpu" in self._enabled:
                    idle = re.search(r"(\d+(?:\.\d+)?)%(?:idle|id)", cpu_line, re.I)
                    if idle:
                        total = float(capacity.group(1)) if capacity else 100.0
                        values["total_cpu"] = max(0.0, min(100.0, (total - float(idle.group(1))) / total * 100.0))
                    else:
                        values["total_cpu"] = None

        core_usage_enabled = any(key.startswith("cpu_core_") and key.endswith("_usage") for key in self._enabled)
        if core_usage_enabled:
            stat = output("proc_stat", "cat", "/proc/stat", timeout=3.0)
            current_cpu_times: dict[int, tuple[int, int]] = {}
            for line in stat.splitlines():
                match = re.match(r"cpu(\d+)\s+(.+)", line)
                if not match:
                    continue
                numbers = [int(value) for value in match.group(2).split() if value.isdigit()]
                if len(numbers) < 4:
                    continue
                total = sum(numbers[:8])
                idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
                core = int(match.group(1))
                current_cpu_times[core] = (total, idle)
                previous = self._last_cpu_times.get(core)
                if previous:
                    total_delta = total - previous[0]
                    idle_delta = idle - previous[1]
                    values[f"cpu_core_{core}_usage"] = (max(0.0, min(100.0, (total_delta - idle_delta) / total_delta * 100.0))
                                                               if total_delta > 0 else None)
            self._last_cpu_times = current_cpu_times

        if {"memory", "java_heap", "native_heap", "graphics_memory", "app_gpu_memory", "swap_memory", "app_uss", "memory_growth"} & self._enabled:
            meminfo = (output("meminfo", "dumpsys", "meminfo", "--local", self._package, timeout=4.0)
                       if self._enabled & {"memory", "java_heap", "native_heap", "graphics_memory", "swap_memory", "app_uss", "memory_growth"} else "")
            total = re.search(r"TOTAL\s+(\d+)", meminfo)
            if "memory" in self._enabled:
                values["memory"] = float(total.group(1)) / 1024 if total else None
            total_pss_mb = float(total.group(1)) / 1024 if total else None
            if total_pss_mb is not None and self._baseline_pss_mb is None:
                self._baseline_pss_mb = total_pss_mb
            if "memory_growth" in self._enabled:
                values["memory_growth"] = total_pss_mb - self._baseline_pss_mb if total_pss_mb is not None and self._baseline_pss_mb is not None else None
            if "app_uss" in self._enabled:
                total_row = re.search(r"^\s*TOTAL\s+(\d+)\s+(\d+)\s+(\d+)", meminfo, re.M)
                values["app_uss"] = (float(total_row.group(2)) + float(total_row.group(3))) / 1024 if total_row else None
            rows = {}
            for label, amount in re.findall(r"^\s*(Native Heap|Dalvik Heap|Graphics)\s+(\d+)", meminfo, re.M):
                rows[label] = float(amount) / 1024
            if "java_heap" in self._enabled: values["java_heap"] = rows.get("Dalvik Heap")
            if "native_heap" in self._enabled: values["native_heap"] = rows.get("Native Heap")
            if "graphics_memory" in self._enabled: values["graphics_memory"] = rows.get("Graphics")
            if "app_gpu_memory" in self._enabled:
                gpu_dump = output("gpu_dump", "dumpsys", "gpu", timeout=4.0)
                gpu_process = re.search(rf"^Proc\s+{re.escape(self._pid)}\s+total:\s*(\d+)", gpu_dump, re.M)
                if gpu_process is None and not meminfo:
                    fallback = output("meminfo", "dumpsys", "meminfo", "--local", self._package, timeout=4.0)
                    graphics = re.search(r"^\s*Graphics\s+(\d+)", fallback, re.M)
                    if graphics: rows["Graphics"] = float(graphics.group(1)) / 1024
                values["app_gpu_memory"] = (float(gpu_process.group(1)) / 1024 / 1024
                                             if gpu_process else rows.get("Graphics"))
                if values["app_gpu_memory"] is not None:
                    metric_sources["app_gpu_memory"] = "dumpsys_gpu_pid" if gpu_process else "graphics_pss_fallback"
                if (gpu_process is None and memory_reading is not None
                        and values["app_gpu_memory"] is not None):
                    observed_elapsed["app_gpu_memory"] = self._memory_last_sample_elapsed
            if "swap_memory" in self._enabled:
                swap = re.search(r"TOTAL SWAP PSS:\s*(\d+)", meminfo)
                values["swap_memory"] = float(swap.group(1)) / 1024 if swap else None
            if memory_reading is not None:
                for key in memory_keys & self._enabled:
                    if values.get(key) is not None:
                        observed_elapsed[key] = self._memory_last_sample_elapsed

        if {"app_rss", "app_threads"} & self._enabled:
            status = output("status", "cat", f"/proc/{self._pid}/status", timeout=3.0)
            if "app_rss" in self._enabled:
                rss = re.search(r"^VmRSS:\s+(\d+)\s+kB", status, re.M)
                values["app_rss"] = float(rss.group(1)) / 1024 if rss else None
            if "app_threads" in self._enabled:
                threads = re.search(r"^Threads:\s+(\d+)", status, re.M)
                values["app_threads"] = float(threads.group(1)) if threads else None

        if {"temperature", "power", "battery_level", "battery_current", "battery_voltage"} & self._enabled:
            battery = output("battery", "dumpsys", "battery", timeout=3.0)
            temperature = re.search(r"temperature:\s*(-?\d+)", battery)
            voltage = re.search(r"^\s*voltage:\s*(\d+)", battery, re.M)
            level = re.search(r"level:\s*(\d+)", battery)
            current_raw = (output("current", "cat", "/sys/class/power_supply/battery/current_now").strip()
                           if {"power", "battery_current"} & self._enabled else "")
            if "temperature" in self._enabled:
                values["temperature"] = float(temperature.group(1)) / 10 if temperature else None
            if "power" in self._enabled:
                try:
                    current_a = abs(float(current_raw)) / 1_000_000
                    voltage_v = float(voltage.group(1)) / 1000 if voltage else 0
                    values["power"] = current_a * voltage_v if voltage_v else None
                except ValueError:
                    values["power"] = None
            if "battery_level" in self._enabled:
                values["battery_level"] = float(level.group(1)) if level else None
            if "battery_current" in self._enabled:
                try: values["battery_current"] = abs(float(current_raw)) / 1000
                except ValueError: values["battery_current"] = None
            if "battery_voltage" in self._enabled:
                values["battery_voltage"] = float(voltage.group(1)) / 1000 if voltage else None

        cpu_hardware = (output("cpu_hardware", "sh", "-c", "true")
                        if cpu_hardware_enabled else "")
        clock_rows = [(int(core), float(current), float(maximum) if maximum else 0.0)
                      for core, current, maximum in re.findall(
                          r"cpu(\d+)/cpufreq/scaling_cur_freq:(\d+):(\d*)", cpu_hardware)]
        if "cpu_frequency" in self._enabled:
            frequencies = [current / 1000.0 for _core, current, _maximum in clock_rows]
            values["cpu_frequency"] = sum(frequencies) / len(frequencies) if frequencies else None
        if {"cpu_online_cores", "cpu_throttling"} & self._enabled:
            online_match = re.search(r"^ONLINE=(.*)$", cpu_hardware, re.M)
            online = online_match.group(1).strip() if online_match else ""
            if "cpu_online_cores" in self._enabled:
                count = 0
                for part in online.split(","):
                    if "-" in part:
                        try:
                            start, end = map(int, part.split("-", 1)); count += max(0, end - start + 1)
                        except ValueError: pass
                    elif part.isdigit(): count += 1
                values["cpu_online_cores"] = float(count) if count else None
            if "cpu_throttling" in self._enabled:
                ratios = [current / maximum for _core, current, maximum in clock_rows
                          if maximum > 0]
                values["cpu_throttling"] = (1.0 - sum(ratios) / len(ratios)) * 100.0 if ratios else None
        core_clock_enabled = any(key.startswith("cpu_core_") and key.endswith("_clock") for key in self._enabled)
        if core_clock_enabled:
            for key in self._enabled:
                if re.fullmatch(r"cpu_core_[0-9]+_clock", key): values[key] = None
            for core, current, _maximum in clock_rows:
                key = f"cpu_core_{core}_clock"
                if key in self._enabled: values[key] = current / 1000.0
        thermal_text = ""
        if {"cpu_temperature", "gpu_temperature"} & self._enabled:
            thermal_text = self._background_output("thermal",
                "sh", "-c",
                "for z in /sys/class/thermal/thermal_zone*; do t=; IFS= read -r t < $z/type; case $t in *cpu*|*CPU*|*cpuss*|*CPUSS*|*gpu*|*GPU*|*gpuss*|*GPUSS*) v=; IFS= read -r v < $z/temp; echo $t:$v;; esac; done",
                refresh_seconds=5.0, timeout=8.0)
        gpu_metrics = {"gpu", "gpu_frequency", "gpu_max_frequency", "gpu_clock_load", "gpu_busy_time"}
        if gpu_metrics & self._enabled:
            gpu_text = output("gpu", "sh", "-c", self._gpu_query(self._enabled))
            busy_match = re.search(r"busy=\s*(\d+)\s+(\d+)", gpu_text)
            current_match = re.search(r"^cur=(\d+)", gpu_text, re.M)
            maximum_match = re.search(r"^max=(\d+)", gpu_text, re.M)
            utilization = (min(100.0, float(busy_match.group(1)) / float(busy_match.group(2)) * 100.0)
                           if busy_match and float(busy_match.group(2)) > 0 else None)
            current_hz = float(current_match.group(1)) if current_match else None
            maximum_hz = float(maximum_match.group(1)) if maximum_match else None
            if "gpu" in self._enabled: values["gpu"] = utilization
            if "gpu_frequency" in self._enabled: values["gpu_frequency"] = current_hz / 1_000_000 if current_hz else None
            if "gpu_max_frequency" in self._enabled: values["gpu_max_frequency"] = maximum_hz / 1_000_000 if maximum_hz else None
            if "gpu_clock_load" in self._enabled:
                values["gpu_clock_load"] = min(100.0, current_hz / maximum_hz * 100.0) if current_hz and maximum_hz else None
            if "gpu_busy_time" in self._enabled:
                interval = max(.001, elapsed - self._last_gpu_elapsed)
                values["gpu_busy_time"] = utilization / 100.0 * interval * 1000.0 if utilization is not None else None
            self._last_gpu_elapsed = elapsed
        if "gpu_temperature" in self._enabled:
            candidates = []
            for kind, raw in re.findall(r"^([^:]*(?:gpu|gpuss)[^:]*):(\-?\d+)$", thermal_text, re.I | re.M):
                value = float(raw); candidates.append(value / 1000.0 if abs(value) > 200 else value)
            values["gpu_temperature"] = max((value for value in candidates if -20 < value < 150), default=None)
        if "cpu_temperature" in self._enabled:
            temperatures = []
            for kind, raw in re.findall(r"^([^:]*(?:cpu|cpuss)[^:]*):(\-?\d+)$", thermal_text, re.I | re.M):
                value = float(raw)
                value = value / 1000.0 if abs(value) > 200 else value
                if -20 < value < 150:
                    temperatures.append(value)
            values["cpu_temperature"] = max(temperatures, default=None)

        if {"network_send", "network_recv"} & self._enabled:
            traffic = output("network", "cat", f"/proc/{self._pid}/net/dev", timeout=3.0)
            received = sent = 0
            valid_interfaces = 0
            for line in traffic.splitlines():
                if ":" not in line or line.lstrip().startswith("lo:"): continue
                columns = line.split(":", 1)[1].split()
                if len(columns) >= 9:
                    try:
                        received += int(columns[0]); sent += int(columns[8])
                        valid_interfaces += 1
                    except ValueError:
                        continue
            if valid_interfaces and self._last_network is not None:
                interval = max(.001, elapsed - self._last_network_elapsed)
                if "network_recv" in self._enabled: values["network_recv"] = max(0, received - self._last_network[0]) / 1024 / interval
                if "network_send" in self._enabled: values["network_send"] = max(0, sent - self._last_network[1]) / 1024 / interval
            if valid_interfaces:
                self._last_network = (received, sent)
                self._last_network_elapsed = elapsed
            else:
                for key in {"network_send", "network_recv"} & self._enabled:
                    values[key] = None
        with self._timeout_lock:
            self._metric_sources = {key: source for key, source in metric_sources.items()
                                    if values.get(key) is not None}
            for key, source in self._metric_sources.items():
                counts = self._metric_source_counts.setdefault(key, {})
                counts[source] = counts.get(source, 0) + 1
        self._refresh_pid(elapsed)
        if self._connection_error:
            raise RuntimeError(self._connection_error)
        long_frame = max((row.get('frame_ms', 0) for row in raw_frames), default=0)
        if long_frame > 5000:
            raw_frame_values['long_frame_max_ms'] = long_frame
        return self.audited_sample(elapsed, values, 'Android proc / sysfs / dumpsys',
            observed_elapsed=observed_elapsed, frames=tuple(raw_frames),
            raw_values=raw_frame_values,
            sources={key: metric_sources.get(key, 'Android frame source unavailable')
                     for key in FRAME_METRICS if key in values},
            adjustments={key: 'long_complete_frame_needs_review'
                         for key in ('frame_time', 'frame_time_p95', 'frame_time_p99')
                         if key in values and long_frame > 5000})

    def diagnostics(self) -> dict:
        """A bounded snapshot for the UI/API; reading it never talks to ADB."""
        with self._timeout_lock:
            shell = dict(self._shell_stats)
            metric_sources = dict(self._metric_sources)
            source_counts = {key: dict(counts) for key, counts in self._metric_source_counts.items()}
        poller = self._memory_poller
        memory = poller.diagnostics() if poller is not None else dict(self._memory_stopped_diagnostics or {
            "submitted": 0, "completed": 0, "busy_polls": 0,
            "stale_discarded": 0, "context_discarded": 0, "failures": 0,
            "in_flight": False, "pending_result": False,
            "max_age_s": max(5.0, self._sample_interval_ms / 1000.0 * 2),
            "last_received_age_s": None, "read_duration_s": None,
        })
        memory.update({"source": "dumpsys meminfo --local",
                       "refresh_interval_s": self._sample_interval_ms / 1000.0,
                       "refresh_policy": "each_sample_tick_single_flight",
                       "last_sample_elapsed": self._memory_last_sample_elapsed,
                       "mode": "asynchronous" if poller is not None or self._memory_stopped_diagnostics is not None else "synchronous"})
        transport = self._light_transport
        channel = (transport.diagnostics() if transport is not None
                   and hasattr(transport, "diagnostics") else dict(self._light_stopped_diagnostics or {"connected": False}))
        return {"shell": shell, "memory": memory, "light_channel": channel,
                "jank_contract": JANK_CONTRACT, "present_frame_source": PRESENT_SOURCE,
                "present_frame_layer": self._present_layer, "present_frame_error": self._present_jank.error,
                "present_frame_lost_windows": self._present_jank.lost_windows,
                "unsupported_metrics": {key: self._present_jank.error for key in self._enabled & JANK_METRICS}
                                       if self._present_jank.error else {},
                "sample_interval_ms": self._sample_interval_ms,
                "metric_sources": metric_sources,
                "metric_source_counts": source_counts,
                "frame_integrity": {'baseline_resets': self._frame_baseline_resets,
                    'last_reset_reason': self._frame_reset_reason,
                    'layer_switches': self._frame_layer_switches,
                    'counter_resets': self._frame_counter_resets,
                    'selected_layer': self._last_surface_name,
                    'note': '源重启、读取中断后先建立基线，不补零、不跨断档推算帧率。'},
                "connected": bool(self._pid and not self._connection_error),
                "pid": self._pid}

    def stop(self) -> None:
        with self._timeout_lock:
            self._run_generation += 1
        poller, self._memory_poller = self._memory_poller, None
        if poller is not None:
            poller.close()
            self._memory_stopped_diagnostics = poller.diagnostics()
        transport, self._light_transport = self._light_transport, None
        if transport is not None:
            transport.close()
            if hasattr(transport, "diagnostics"):
                self._light_stopped_diagnostics = transport.diagnostics()
        if self._serial and self._frame_tracking:
            self._shell("dumpsys", "SurfaceFlinger", "--timestats", "-disable", timeout=3.0)
        self._frame_tracking = False
        executor = self._sample_executor
        self._sample_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        self._background_futures.clear()
        self._background_cache.clear()
        self._background_started.clear()
        self._pid = ""; self._enabled = frozenset(); self._last_frame_completed = 0
        self._last_surface_frames = 0; self._last_surface_name = ""
        self._last_surface_counts = {}
        self._last_cpu_times = {}
        self._baseline_pss_mb = None
        self._last_gpu_elapsed = 0.0
        self._last_surface_histogram = {}
        self._last_pid_refresh_elapsed = -10.0
