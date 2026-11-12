from __future__ import annotations

import csv
import math
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque

import psutil

try:
    import win32pdh
except ImportError:  # pragma: no cover - exercised by portable/runtime checks
    win32pdh = None

from ..domain.models import Device, Sample, Target
from ..domain.validation import MAX_FRAME_TIME_MS
from ..runtime_paths import RESOURCE_ROOT
from .base import Collector, FpsStabilizer
from .discovery import discover_windows
from .targets import windows_targets
from .frame_stream import RenderStreamSelector
from .windows_frame_source import (FrameCandidate, FULL, APPLICATION, DXGI, PROFILES,
    PROBE_SECONDS, csv_frame_rows, presentmon_command, frame_event_time)
from ..domain.jank import (FrameJankTracker, JANK_CONTRACT, JANK_METRICS,
    WINDOWS_PRESENTMON_FRAME_SOURCE, WINDOWS_DXGI_FRAME_SOURCE)
from .process_resources import ProcessTreeResources, PROCESS_KEYS
from .windows_system import WindowsSystemCounters


FRAME_METRICS = frozenset({
    "fps", "fps_1_low", "fps_0_1_low", "frame_time", "frame_time_p95",
    "frame_time_p99", "jank", "big_jank", "small_jank", "jank_rate",
    "dropped_frames", "dropped_frame_rate", "cpu_frame_busy", "cpu_frame_wait",
    "gpu_time", "gpu_busy", "gpu_wait", "gpu_latency",
}) | JANK_METRICS
PRESENTMON_GPU_METRICS = frozenset({"gpu_time", "gpu_busy", "gpu_wait", "gpu_latency"})
PRESENTMON_DISPLAY_METRICS = frozenset({"dropped_frames", "dropped_frame_rate"})
DXGI_PRESENT_METRICS = FRAME_METRICS - (PRESENTMON_GPU_METRICS | PRESENTMON_DISPLAY_METRICS | {'cpu_frame_busy', 'cpu_frame_wait'})
GPU_PROCESS_METRICS = frozenset({"app_gpu_usage", "gpu_memory", "shared_gpu_memory"})
PROCESS_CPU_METRICS = frozenset({"app_cpu", "app_user_cpu", "app_kernel_cpu"})
PROCESS_IO_METRICS = frozenset({"disk_read", "disk_write", "disk_read_iops", "disk_write_iops"})
SYSTEM_CPU_METRICS = frozenset({"total_cpu", "system_cpu_user", "system_cpu_kernel"})
SYSTEM_RAM_METRICS = frozenset({"system_memory_used", "available_memory"})
PER_CORE_USAGE_PATTERN = re.compile(r"^cpu_core_(\d+)_usage$")
FRAME_HISTORY_SECONDS = 60.0

# How long a healthy ETW session may stay silent before we tell the user.
FRAME_EVENT_GRACE_SECONDS = 15.0
PRESENT_QUEUE_CAPACITY = 4096


def _finite_number(row: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        raw = row.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def _percentile_from_ordered(ordered: list[float], ratio: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * ratio
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _low_fps_from_slowest(slowest_first: list[float], ratio: float) -> float | None:
    if not slowest_first:
        return None
    count = max(1, math.ceil(len(slowest_first) * ratio))
    average = sum(slowest_first[:count]) / count
    return min(1000.0, 1000.0 / average) if average > 0 else None


def _cpu_total_and_busy(times) -> tuple[float, float]:
    fields = times._asdict() if hasattr(times, "_asdict") else vars(times)
    total = sum(float(value) for key, value in fields.items()
                if key not in {"guest", "guest_nice"})
    idle = float(fields.get("idle", 0.0)) + float(fields.get("iowait", 0.0))
    return total, max(0.0, total - idle)



def _system_cpu_usage(current, previous, requested) -> dict[str, float]:
    # Windows' busy kernel time already includes interrupt and DPC time.
    # Sum each logical processor's deltas so the denominator is whole-PC capacity.
    user = kernel = idle = 0.0
    for after, before in zip(current, previous):
        du, dk, di = after.user - before.user, after.system - before.system, after.idle - before.idle
        if min(du, dk, di) < 0:
            return {}
        user += du
        kernel += dk
        idle += di
    total = user + kernel + idle
    if total <= 0:
        return {}
    rates = {"total_cpu": (user + kernel) / total * 100.0,
             "system_cpu_user": user / total * 100.0,
             "system_cpu_kernel": kernel / total * 100.0}
    return {key: rates[key] for key in requested}


class _GpuProcessCounters:
    """One reusable PDH query for target-process GPU load and memory.

    Rebuilding a wildcard query every sample is surprisingly expensive, so the
    matching instances are refreshed only when the process tree changes or at
    a short retry interval. Missing counters produce no value, never a fake 0.
    """

    REFRESH_SECONDS = 5.0

    def __init__(self) -> None:
        self._query = None
        self._handles: dict[str, list] = {}
        self._instances: dict[str, set[str]] = {}
        self._pids: frozenset[int] = frozenset()
        self._next_refresh = 0.0
        self._usage_ready = False
        self._requested = frozenset()

    def close(self) -> None:
        if self._query is not None and win32pdh is not None:
            try:
                win32pdh.CloseQuery(self._query)
            except Exception:
                pass
        self._query = None
        self._handles = {}
        self._instances = {}
        self._pids = frozenset()
        self._usage_ready = False

    @staticmethod
    def _matching_instances(object_name: str, pids: frozenset[int]) -> list[str]:
        if win32pdh is None or not pids:
            return []
        try:
            _counters, instances = win32pdh.EnumObjectItems(
                None, None, object_name, win32pdh.PERF_DETAIL_WIZARD)
        except Exception:
            return []
        prefixes = tuple(f"pid_{pid}_" for pid in pids)
        return sorted({str(instance) for instance in instances
                       if str(instance).casefold().startswith(prefixes)})

    def _add(self, metric: str, object_name: str, instances: list[str], counter: str) -> int:
        if win32pdh is None or self._query is None:
            return 0
        known = self._instances.setdefault(metric, set())
        handles = self._handles.setdefault(metric, [])
        added = 0
        for instance in instances:
            if instance in known:
                continue
            try:
                path = win32pdh.MakeCounterPath(
                    (None, object_name, instance, None, -1, counter))
                handles.append(win32pdh.AddCounter(self._query, path))
                known.add(instance)
                added += 1
            except Exception:
                continue
        if not handles:
            self._handles.pop(metric, None)
            self._instances.pop(metric, None)
        return added

    def _discover_and_add(self, pids: frozenset[int]) -> int:
        if self._requested & {"gpu_memory", "shared_gpu_memory"}:
            instances = self._matching_instances("GPU Process Memory", pids)
            if "gpu_memory" in self._requested:
                self._add("gpu_memory", "GPU Process Memory", instances, "Dedicated Usage")
            if "shared_gpu_memory" in self._requested:
                self._add("shared_gpu_memory", "GPU Process Memory", instances, "Shared Usage")
        if "app_gpu_usage" in self._requested:
            return self._add("app_gpu_usage", "GPU Engine", self._matching_instances("GPU Engine", pids),
                             "Utilization Percentage")
        return 0

    def _rebuild(self, pids: frozenset[int], now: float, requested=GPU_PROCESS_METRICS) -> bool:
        self.close()
        self._requested = requested
        self._pids = pids
        self._next_refresh = now + self.REFRESH_SECONDS
        if win32pdh is None or not pids:
            return False
        try:
            self._query = win32pdh.OpenQuery()
            self._discover_and_add(pids)
            if not self._handles:
                self.close()
                self._pids = pids
                self._next_refresh = now + 1.0
                return False
            win32pdh.CollectQueryData(self._query)
            self._usage_ready = False
            return True
        except Exception:
            self.close()
            self._pids = pids
            self._next_refresh = now + 1.0
            return False

    @staticmethod
    def _sum(handles: list, fmt: int) -> float | None:
        if win32pdh is None:
            return None
        values = []
        for handle in handles:
            try:
                value = float(win32pdh.GetFormattedCounterValue(handle, fmt)[1])
            except Exception:
                continue
            if math.isfinite(value) and value >= 0:
                values.append(value)
        return sum(values) if values else None

    def sample(self, pids: set[int], enabled: frozenset[str]) -> dict[str, float]:
        requested = GPU_PROCESS_METRICS & enabled
        if not requested:
            return {}
        now = time.monotonic()
        pid_set = frozenset(int(pid) for pid in pids if int(pid) > 0)
        rebuilt = False
        if pid_set != self._pids or self._query is None or requested != self._requested:
            rebuilt = self._rebuild(pid_set, now, requested)
        elif now >= self._next_refresh:
            had_usage = bool(self._handles.get("app_gpu_usage"))
            added_usage = self._discover_and_add(pid_set)
            self._next_refresh = now + self.REFRESH_SECONDS
            if added_usage and not had_usage:
                self._usage_ready = False
        if self._query is None or win32pdh is None:
            return {}
        if not rebuilt:
            try:
                win32pdh.CollectQueryData(self._query)
            except Exception:
                self._next_refresh = 0.0
                return {}
        values: dict[str, float] = {}
        byte_format = win32pdh.PDH_FMT_LARGE
        for key in ("gpu_memory", "shared_gpu_memory"):
            if key not in requested:
                continue
            raw = self._sum(self._handles.get(key, []), byte_format)
            if raw is not None:
                values[key] = raw / 1048576.0
        if "app_gpu_usage" in requested and self._usage_ready:
            usage = self._sum(self._handles.get("app_gpu_usage", []), win32pdh.PDH_FMT_DOUBLE)
            if usage is not None:
                values["app_gpu_usage"] = min(100.0, usage)
        self._usage_ready = True
        return values


class WindowsCollector(Collector):
    """Fresh Windows process sampler with PerfZY-style normalized App CPU."""

    def __init__(self) -> None:
        super().__init__()
        self._process: psutil.Process | None = None
        self._cpus = max(1, psutil.cpu_count(logical=True) or 1)
        self._presentmon: subprocess.Popen[str] | None = None
        self._presentmon_lock = threading.RLock()
        self._presentmon_unavailable = False
        self._presentmon_tracks_gpu = False
        self._presentmon_tracks_display = True
        self._presentmon_presentation_fallback = False
        self._presentmon_started_at = 0.0
        # PresentMon streams every process and we filter locally. Keep this
        # bounded so a burst from other windows cannot make one sample drain an
        # ever-growing queue forever and freeze every PC curve.
        self._present_rows: queue.Queue[dict[str, str]] = queue.Queue(
            maxsize=PRESENT_QUEUE_CAPACITY)
        self._presentmon_error = ""
        self._presentmon_stderr: list[str] = []
        self._present_frame_count = 0
        self._presentmon_generation = 0
        self._presentmon_owned = False
        self._presentmon_pid = 0
        self._presentmon_restarts = 0
        self._last_valid_frame_at = 0.0
        self._presentmon_session = "PerfZY-PresentMon-" + uuid.uuid4().hex[:12]
        self._dxgi_source = None
        self._dxgi_preferred = False
        self._dxgi_rows = 0
        self._dxgi_details = {}
        self._primary_frame_failure = {}
        self._capture_profile = FULL
        self._active_candidate = None
        self._probing_candidate = None
        self._startup_candidate = None
        self._startup_verified_at = 0.0
        self._startup_last_event_at = 0.0
        self._startup_primary_frame_count = 0
        self._startup_reported_drops = 0
        self._startup_using = False
        self._sample_frame_session = ''
        self._candidate_tried = set()
        self._candidate_failures = []
        self._source_transitions = []
        self._verified_profiles = {FULL}
        self._healthy_frame_since = 0.0
        self._active_candidate_reported_drops = 0
        self._presentmon_read_rows = 0
        self._presentmon_parse_errors = 0
        self._frame_quality_lock = threading.Lock()
        self._queue_overflow_rows = 0
        self._filtered_other_pids = 0
        self._frame_invalid_rows = 0
        self._frame_duplicate_rows = 0
        self._frame_out_of_order_rows = 0
        self._generated_frame_rows = 0
        self._etw_loss_reported = False
        self._frame_pid_filter = None
        self._render_stream = RenderStreamSelector()
        self._jank_tracker = FrameJankTracker()
        self._frame_capture_paused = False
        self._frame_accept_after = 0.0
        self._frame_reset_pending = False
        self._frame_resume_grace_at = 0.0
        self._jank_epoch = 0
        self._jank_reset_pending = False
        self._stream_last_time = {}
        self._last_window_quality = {}
        self._target_pid = 0
        self._recorded_target_pid = 0
        self._target_process_name = ""
        self._last_io = None
        self._last_io_time = 0.0
        self._last_network = None
        self._last_network_time = 0.0
        self._last_cpu_times = None
        self._last_cpu_time = 0.0
        self._last_page_faults: int | None = None
        self._last_page_fault_time = 0.0
        self._last_per_cpu_times = None
        self._process_cpu_active = False
        self._per_core_active = False
        self._io_active = False
        self._network_active = False
        self._page_fault_active = False
        self._frame_history: deque[tuple[float, float]] = deque()
        self._gpu_counters = _GpuProcessCounters()
        self._system_counters = WindowsSystemCounters()
        self._fps_stabilizer = FpsStabilizer()
        self._resource_scope = 'selected'
        self._resource_sampler = ProcessTreeResources(self._cpus)
        self._resource_snapshot = {}
        self._resource_identities = {}
        self._sample_related_children = None

    def set_resource_scope(self, scope: str) -> None:
        """Choose a resource population before collection, never mid-session."""
        if self._process is not None:
            raise RuntimeError('采集中不能改变 Windows 进程统计范围，请先停止采集。')
        if scope not in {'selected', 'tree'}:
            raise ValueError('Windows resource scope must be selected or tree')
        self._resource_scope = scope

    @staticmethod
    def _presentmon_path() -> str:
        bundled = RESOURCE_ROOT / "tools" / "PresentMon.exe"
        return str(bundled) if bundled.exists() else (shutil.which("PresentMon") or "")

    def _read_presentmon(self, stream, generation=None) -> None:
        try:
            def invalid():
                if generation is None or generation == self._presentmon_generation:
                    self._presentmon_parse_errors += 1
            for row in csv_frame_rows(stream, invalid, lambda text: None):
                if generation is not None and generation != self._presentmon_generation:
                    return
                self._presentmon_read_rows += 1
                self._enqueue_present_row(row)
        except (OSError, ValueError, csv.Error):
            return

    def _enqueue_present_row(self, row: dict[str, str]) -> None:
        """Append a frame row, dropping the oldest backlog when at capacity."""
        if self._frame_capture_paused:
            return
        if self._frame_accept_after:
            event_time = frame_event_time(row, self._presentmon_started_at)
            if event_time is None or event_time < self._frame_accept_after:
                return
        if self._frame_pid_filter is not None:
            normalized = {str(k).strip().casefold(): v for k, v in row.items()}
            identity = RenderStreamSelector.identity(normalized)
            if identity and identity[0] not in self._frame_pid_filter:
                self._filtered_other_pids += 1
                return
        try:
            self._present_rows.put_nowait(row)
            return
        except queue.Full:
            pass
        try:
            self._present_rows.get_nowait()
            with self._frame_quality_lock:
                self._queue_overflow_rows += 1
        except queue.Empty:
            return
        try:
            self._present_rows.put_nowait(row)
        except queue.Full:
            pass

    def _read_presentmon_errors(self, stream, generation=None) -> None:
        try:
            for line in stream:
                if generation is not None and generation != self._presentmon_generation:
                    return
                text = line.strip()
                if text:
                    if 'events were lost' in text.casefold():
                        self._etw_loss_reported = True
                    self._presentmon_stderr.append(text)
                    del self._presentmon_stderr[:-8]
        except OSError:
            return

    @staticmethod
    def _stop_stale_etw_sessions() -> None:
        """Compatibility no-op: broad prefix cleanup can stop a live instance."""

    def _stop_owned_etw_session(self) -> None:
        try:
            subprocess.run(["logman", "stop", self._presentmon_session, "-ets"],
                           capture_output=True, timeout=3,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass

    def discover_devices(self): return discover_windows()
    def discover_targets(self, device: Device): return windows_targets(device)

    def set_capture_paused(self, paused):
        self._frame_capture_paused = bool(paused)
        self._frame_accept_after = time.monotonic()
        if not paused:
            self._frame_resume_grace_at = self._frame_accept_after
        self._frame_reset_pending = True
        self._jank_epoch += 1
        self._jank_reset_pending = True
        with self._present_rows.mutex:
            self._present_rows.queue.clear()
        self._startup_verified_at = 0.0
        self._startup_last_event_at = 0.0
        for candidate in (self._active_candidate, self._probing_candidate, self._startup_candidate):
            if candidate is not None:
                candidate.reset_boundary(self._frame_accept_after)

    def start(self, device: Device, target: Target, enabled_metrics: frozenset[str]) -> None:
        self._stop_frame_candidates()
        self._capture_profile = FULL
        self._startup_verified_at = 0.0
        self._startup_last_event_at = 0.0
        self._startup_primary_frame_count = 0
        self._startup_using = False
        self._sample_frame_session = ''
        self._candidate_tried.clear()
        self._candidate_failures.clear()
        self._source_transitions.clear()
        self._verified_profiles = {FULL}
        self._healthy_frame_since = 0.0
        self._dxgi_preferred = False
        self._dxgi_rows = 0
        self._dxgi_details = {}
        self._primary_frame_failure = {}
        self._frame_capture_paused = False
        self._frame_accept_after = 0.0
        self._frame_resume_grace_at = 0.0
        self._frame_reset_pending = False
        self._jank_tracker.reset()
        self._jank_epoch += 1
        if (FRAME_METRICS | PRESENTMON_GPU_METRICS) & enabled_metrics and not self._presentmon_path():
            raise RuntimeError("未找到 PresentMon，无法采集所选帧指标。请安装 PresentMon CLI 或将其加入 PATH。")
        if target.process_id is None:
            raise ValueError("target process id is missing")
        self._process = psutil.Process(target.process_id)
        self._target_pid = int(target.process_id)
        self._recorded_target_pid = self._target_pid
        self._resource_sampler.reset()
        self._resource_snapshot = {}
        self._resource_identities = {}
        self._sample_related_children = None
        try:
            self._target_process_name = self._process.name().casefold().removesuffix(".exe")
        except psutil.Error:
            self._target_process_name = target.name.casefold().removesuffix(".exe")
        self._enabled = enabled_metrics
        self._presentmon_error = ""
        self._presentmon_stderr.clear()
        self._presentmon_presentation_fallback = False
        self._present_frame_count = 0
        self._presentmon_restarts = 0
        self._last_valid_frame_at = 0.0
        self._presentmon_read_rows = 0
        self._presentmon_parse_errors = 0
        self._queue_overflow_rows = 0
        self._filtered_other_pids = 0
        self._frame_invalid_rows = self._frame_duplicate_rows = self._frame_out_of_order_rows = 0
        self._generated_frame_rows = 0
        self._etw_loss_reported = False
        self._frame_pid_filter = None
        self._render_stream.reset()
        self._stream_last_time.clear()
        self._last_window_quality = {}
        self._frame_history.clear()
        self._fps_stabilizer.reset()
        self._gpu_counters.close()
        self._system_counters.close()
        self._process_cpu_active = False
        self._per_core_active = False
        self._io_active = False
        self._network_active = False
        self._page_fault_active = False
        self._last_io = None
        self._last_network = None
        self._last_cpu_times = None
        self._last_page_faults = None
        self._last_per_cpu_times = None
        self._presentmon = None
        self._presentmon_unavailable = False
        self._presentmon_tracks_gpu = False
        self._prime_pending_sources()
        if FRAME_METRICS & enabled_metrics:
            self._ensure_presentmon()

    def activate_pending_sources(self) -> None:
        self._prime_pending_sources()
        if FRAME_METRICS & self._enabled:
            self._ensure_presentmon()
        else:
            with self._presentmon_lock:
                self._stop_frame_candidates()
                self._terminate_presentmon()
                self._frame_history.clear()
                while not self._present_rows.empty():
                    try: self._present_rows.get_nowait()
                    except queue.Empty: break

    def _prime_pending_sources(self) -> None:
        """Reset delta counters when a metric family is enabled mid-capture."""
        if self._process is None:
            return
        now = time.monotonic()
        if self._resource_scope == 'tree':
            self._resource_sampler.configure(self._enabled)
        process_cpu_requested = self._resource_scope == 'selected' and bool(PROCESS_CPU_METRICS & self._enabled)
        if process_cpu_requested and not self._process_cpu_active:
            try:
                self._last_cpu_times = self._process.cpu_times()
                self._last_cpu_time = now
            except (psutil.Error, OSError):
                self._last_cpu_times = None
            self._process_cpu_active = True
        elif not process_cpu_requested:
            self._process_cpu_active = False
            self._last_cpu_times = None

        per_core_requested = (bool(SYSTEM_CPU_METRICS & self._enabled) or
                              any(PER_CORE_USAGE_PATTERN.match(key) for key in self._enabled))
        if per_core_requested and not self._per_core_active:
            try:
                self._last_per_cpu_times = psutil.cpu_times(percpu=True)
            except (OSError, RuntimeError):
                self._last_per_cpu_times = None
            self._per_core_active = True
        elif not per_core_requested:
            self._per_core_active = False
            self._last_per_cpu_times = None

        io_requested = self._resource_scope == 'selected' and bool(PROCESS_IO_METRICS & self._enabled)
        if io_requested and not self._io_active:
            try:
                self._last_io = self._process.io_counters()
                self._last_io_time = now
            except (psutil.Error, OSError):
                self._last_io = None
            self._io_active = True
        elif not io_requested:
            self._io_active = False
            self._last_io = None

        network_requested = bool({"network_send", "network_recv"} & self._enabled)
        if network_requested and not self._network_active:
            self._last_network = psutil.net_io_counters()
            self._last_network_time = now
            self._network_active = True
        elif not network_requested:
            self._network_active = False
            self._last_network = None

        page_fault_requested = self._resource_scope == 'selected' and "page_faults" in self._enabled
        if page_fault_requested and not self._page_fault_active:
            try:
                self._last_page_faults = int(self._process.memory_info().num_page_faults)
                self._last_page_fault_time = now
            except (psutil.Error, OSError, AttributeError):
                self._last_page_faults = None
            self._page_fault_active = True
        elif not page_fault_requested:
            self._page_fault_active = False
            self._last_page_faults = None

    def _ensure_presentmon(self, force=False) -> None:
        """Start the ETW frame stream, once, whenever a frame metric needs it.

        Frame metrics can be switched on long after collection begins, so this
        has to be callable at any point rather than only from ``start``.
        """
        with self._presentmon_lock:
            if self._process is None or self._presentmon_unavailable:
                return
            if self._active_candidate is not None:
                return
            if self._capture_profile == DXGI:
                candidate = self._new_frame_candidate(DXGI)
                try:
                    candidate.start()
                    if DXGI in self._verified_profiles:
                        self._adopt_frame_candidate(candidate, verified_resume=True)
                        return
                except (OSError, RuntimeError, AssertionError) as error:
                    self._candidate_failures.append({'profile': DXGI, 'reason': str(error)})
                candidate.stop()
                self._capture_profile = FULL
                self._dxgi_preferred = False
            # Keep one profile for this source's lifetime. A checkbox changes
            # output gates; it must not change API coverage or restart FPS.
            needs_gpu = needs_display = self._capture_profile == FULL
            if self._presentmon is not None and self._presentmon.poll() is None:
                if (needs_gpu == self._presentmon_tracks_gpu and
                        needs_display == self._presentmon_tracks_display and not force):
                    return
                self._terminate_presentmon()
            executable = self._presentmon_path()
            if not executable:
                self._presentmon_unavailable = True
                self._presentmon_error = "未找到 PresentMon，Windows 帧指标不可用"
                return
            # Every collector owns a unique session. Never stop another live
            # PerfZY instance (or all sessions sharing a broad name prefix).
            # Stream CSV through stdout. PresentMon may buffer --output_file for
            # long periods, which made a healthy ETW session look as though it
            # had produced no frames during live collection.
            #
            # Do not use PresentMon's --process_id filter here. Some games
            # create or hand presentation work to a related process after
            # launch, while the UI still exposes only the original process.
            # Capture the ETW stream once and filter it locally instead.
            command = presentmon_command(executable, self._presentmon_session, self._capture_profile)
            try:
                self._presentmon = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError as error:
                self._presentmon_unavailable = True
                self._presentmon_error = f"PresentMon 启动失败：{error}"
                return
            self._presentmon_started_at = time.monotonic()
            self._presentmon_owned = True
            self._presentmon_pid = getattr(self._presentmon, 'pid', 0)
            self._presentmon_generation += 1
            self._stream_last_time.clear()
            self._frame_history.clear()
            self._render_stream.reset()
            self._fps_stabilizer.reset()
            generation = self._presentmon_generation
            self._presentmon_tracks_gpu = needs_gpu
            self._presentmon_tracks_display = needs_display
            self._presentmon_presentation_fallback = self._capture_profile != FULL
            self._presentmon_error = ""
            if self._presentmon.stdout:
                threading.Thread(target=self._read_presentmon, args=(self._presentmon.stdout, generation),
                                 name="PerfZY-PresentMon-Frames", daemon=True).start()
            if self._presentmon.stderr:
                threading.Thread(target=self._read_presentmon_errors, args=(self._presentmon.stderr, generation),
                                 name="PerfZY-PresentMon-Errors", daemon=True).start()
            self._start_startup_frame_source()

    def _start_startup_frame_source(self):
        """Race real DXGI frames against the full source's initial warmup.

        The full source stays alive and takes priority as soon as target frames
        arrive. Basic FPS need not wait through two serial recovery timeouts.
        No GPU/display timings are inferred from the compatibility source.
        """
        if (self._startup_candidate is not None or self._capture_profile != FULL
                or not (DXGI_PRESENT_METRICS & self._enabled)):
            return
        candidate = self._new_frame_candidate(DXGI)
        try:
            candidate.start()
            if self._frame_accept_after:
                candidate.reset_boundary(self._frame_accept_after)
        except (OSError, RuntimeError, AssertionError) as error:
            candidate.stop()
            self._candidate_failures.append({'profile': DXGI, 'reason': str(error),
                                             'phase': 'startup'})
            del self._candidate_failures[:-8]
            return
        self._startup_candidate = candidate
        self._startup_verified_at = 0.0
        self._startup_primary_frame_count = 0
        self._startup_reported_drops = 0

    def _stop_startup_frame_source(self):
        candidate, self._startup_candidate = self._startup_candidate, None
        if candidate is not None:
            candidate.stop()
            if candidate.native is not None:
                self._dxgi_details = candidate.native.diagnostics()
        self._startup_verified_at = 0.0

    def _startup_frame_rows(self, primary_rows, now):
        """Choose exactly one source per sample, with primary target priority."""
        candidate = self._startup_candidate
        if primary_rows and self._startup_using and self._startup_last_event_at:
            # A warming full source can flush old rows. Do not replay the same
            # time span after real compatibility frames have already been saved.
            primary_rows = [row for row in primary_rows
                            if (stamp := frame_event_time(row, self._presentmon_started_at)) is not None
                            and stamp > self._startup_last_event_at]
        if primary_rows:
            if candidate is not None:
                self._startup_primary_frame_count += len(primary_rows)
            return primary_rows, False
        if candidate is None or self._frame_capture_paused or not candidate.ready:
            return primary_rows, False
        # ready proves unique timestamped target frames, never just a live PID.
        self._dxgi_rows = candidate.valid_rows
        overflow = max(0, candidate.buffer_dropped - self._startup_reported_drops)
        self._startup_reported_drops = candidate.buffer_dropped
        if overflow:
            with self._frame_quality_lock:
                self._queue_overflow_rows += overflow
        return [{str(key).lstrip('\ufeff').strip().casefold(): value
                 for key, value in row.items()} for row in candidate.take_rows()], True

    def _select_sample_frame_session(self, session):
        if session != self._sample_frame_session:
            # A change in source is a real frame-order boundary for Low FPS/Jank.
            self._sample_frame_session = session
            self._stream_last_time.clear()
            self._frame_history.clear()
            self._render_stream.reset()
            self._fps_stabilizer.reset()
            self._jank_epoch += 1
            self._jank_reset_pending = True

    def _terminate_presentmon(self) -> None:
        self._presentmon_generation += 1
        candidate = self._active_candidate
        if candidate is not None:
            candidate.stop()
            if candidate.native is not None:
                self._dxgi_details = candidate.native.diagnostics()
            self._active_candidate = None
            self._dxgi_source = None
        process = self._presentmon
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        self._presentmon = None
        self._presentmon_tracks_gpu = False
        self._presentmon_tracks_display = False
        if self._presentmon_owned and isinstance(self._presentmon_pid, int) and self._presentmon_pid > 0:
            self._stop_owned_etw_session()
        self._presentmon_pid = 0

    def _frame_pids(self):
        return self._frame_pid_filter if self._frame_pid_filter is not None else {self._target_pid}

    def _new_frame_candidate(self, profile):
        return FrameCandidate(profile, self._presentmon_path(), self._frame_pids,
                              accepting=lambda: not self._frame_capture_paused)

    def _begin_frame_candidate(self, profile):
        self._candidate_tried.add(profile)
        candidate = self._new_frame_candidate(profile)
        try:
            candidate.start()
            if self._frame_accept_after:
                candidate.reset_boundary(self._frame_accept_after)
        except (OSError, RuntimeError, AssertionError) as error:
            candidate.stop()
            self._candidate_failures.append({'profile': profile, 'reason': str(error)})
            del self._candidate_failures[:-8]
            return False
        self._probing_candidate = candidate
        self._presentmon_error = '目标帧数据暂未收到，正在验证其他采集通道'
        return True

    def _cancel_frame_probe(self, reason):
        candidate = self._probing_candidate
        if candidate is not None:
            candidate.stop()
            details = candidate.diagnostics()
            details['reason'] = reason
            self._candidate_failures.append(details)
            del self._candidate_failures[:-8]
            self._probing_candidate = None

    def _adopt_frame_candidate(self, candidate, *, verified_resume=False, verified_startup=False):
        if not (verified_resume or verified_startup) and not candidate.ready:
            return False
        previous = self._capture_profile
        self._primary_frame_failure = {
            'source': previous, 'csv_rows': self._presentmon_read_rows,
            'target_rows': self._startup_primary_frame_count if verified_startup else self._present_frame_count, 'stderr': list(self._presentmon_stderr),
            'reason': 'no recent valid target frames'}
        self._probing_candidate = None
        self._terminate_presentmon()
        self._active_candidate = candidate
        self._active_candidate_reported_drops = candidate.buffer_dropped
        self._capture_profile = candidate.profile
        self._verified_profiles.add(candidate.profile)
        self._dxgi_preferred = candidate.profile == DXGI
        self._dxgi_source = candidate.native
        self._presentmon = candidate.process
        self._presentmon_owned = False  # the candidate owns teardown of this process/session
        self._presentmon_session = candidate.session
        self._presentmon_pid = 0
        self._presentmon_tracks_gpu = self._presentmon_tracks_display = candidate.profile == FULL
        self._presentmon_presentation_fallback = candidate.profile != FULL
        self._presentmon_started_at = candidate.started_at
        self._last_valid_frame_at = 0.0
        self._healthy_frame_since = 0.0
        self._presentmon_stderr.clear()
        self._presentmon_read_rows = self._presentmon_parse_errors = 0
        while not self._present_rows.empty():
            try:
                self._present_rows.get_nowait()
            except queue.Empty:
                break
        self._stream_last_time.clear()
        self._frame_history.clear()
        self._render_stream.reset()
        self._fps_stabilizer.reset()
        self._jank_tracker.reset()
        self._jank_epoch += 1
        self._source_transitions.append({'from': previous, 'to': candidate.profile,
            'verified_target_rows': candidate.valid_rows, 'verified_resume': verified_resume,
            'verified_startup': verified_startup,
            'discarded_probe_rows': candidate.buffer_dropped})
        del self._source_transitions[:-16]
        self._presentmon_error = '已验证目标帧，正在接入采集通道'
        return True

    def _stop_frame_candidates(self):
        with self._presentmon_lock:
            self._stop_startup_frame_source()
            self._cancel_frame_probe('collection stopped or all frame metrics disabled')
            candidate = self._active_candidate
            if candidate is not None:
                candidate.stop()
                if candidate.native is not None:
                    self._dxgi_details = candidate.native.diagnostics()
                self._active_candidate = None
                self._dxgi_source = None

    def _recover_silent_frame_source(self, now):
        with self._presentmon_lock:
            self._recover_silent_frame_source_locked(now)

    def _recover_silent_frame_source_locked(self, now):
        if self._frame_capture_paused or not (FRAME_METRICS & self._enabled):
            return
        if not self._presentmon_owned and self._active_candidate is None:
            return
        startup = self._startup_candidate
        if startup is not None:
            if self._startup_primary_frame_count:
                self._stop_startup_frame_source()
            elif now - max(self._presentmon_started_at, self._frame_resume_grace_at) < FRAME_EVENT_GRACE_SECONDS:
                return
            elif startup.running and self._startup_verified_at and now - self._startup_verified_at < 2:
                # Keep the already-proven fast source after the full warmup.
                # Its ready queue was consumed by this sample; proof is retained
                # only across this short window and cleared on every pause.
                self._startup_candidate = None
                self._startup_using = False
                self._adopt_frame_candidate(startup, verified_startup=True)
                return
            else:
                self._stop_startup_frame_source()
        anchor = max(self._presentmon_started_at, self._last_valid_frame_at, self._frame_resume_grace_at)
        if not anchor:
            return
        recent = bool(self._last_valid_frame_at and now - self._last_valid_frame_at < FRAME_EVENT_GRACE_SECONDS)
        if recent:
            self._cancel_frame_probe('active source recovered before a switch')
            if self._healthy_frame_since and now - self._healthy_frame_since >= FRAME_EVENT_GRACE_SECONDS:
                self._candidate_tried.clear()
                self._presentmon_restarts = 0
            return
        if now - anchor < FRAME_EVENT_GRACE_SECONDS:
            return
        candidate = self._probing_candidate
        if candidate is not None:
            if candidate.ready:
                self._adopt_frame_candidate(candidate)
                return
            if candidate.running and now - candidate.probe_started_at < PROBE_SECONDS:
                return
            self._cancel_frame_probe('no verified target frames within the probe window')
        # The same ladder applies to every executable. Global CSV activity is
        # not proof that the selected process is producing valid frames.
        for profile in PROFILES:
            if profile == self._capture_profile or profile in self._candidate_tried:
                continue
            if profile == DXGI and not (DXGI_PRESENT_METRICS & self._enabled):
                continue
            if profile == APPLICATION and not ((FRAME_METRICS - PRESENTMON_GPU_METRICS - PRESENTMON_DISPLAY_METRICS) & self._enabled):
                continue
            if self._begin_frame_candidate(profile):
                return
        if self._presentmon_restarts < 2:
            self._presentmon_restarts += 1
            if self._capture_profile == DXGI:
                # Retain the last proven source until its replacement proves
                # itself. A successful process launch alone changes nothing.
                if self._begin_frame_candidate(DXGI):
                    return
            else:
                self._terminate_presentmon()
                self._ensure_presentmon(force=True)
                self._presentmon_error = f'目标帧源正在重试（{self._presentmon_restarts}/2）'
                return
        self._presentmon_error = '所选进程仍未收到有效帧；FPS 保持缺失，请查看采集诊断中的通道与进程信息'

    def diagnostics(self) -> dict:
        with self._frame_quality_lock:
            overflow = self._queue_overflow_rows
        startup = self._startup_candidate
        source = startup.native if self._startup_using and startup is not None else self._dxgi_source
        dxgi = source.diagnostics() if source is not None else self._dxgi_details
        native = self._dxgi_preferred or self._startup_using
        unavailable = PRESENTMON_GPU_METRICS | PRESENTMON_DISPLAY_METRICS
        if native:
            unavailable = unavailable | {'cpu_frame_busy', 'cpu_frame_wait'}
        return {'collection_revision': 6, 'frame_source': 'DXGI ETW (compatibility)' if native else 'PresentMon / ETW',
                'session': dxgi.get('session', '') if native else self._presentmon_session,
                'dxgi_compatibility': dxgi, 'compatibility_frame_rows': self._dxgi_rows,
                'primary_frame_failure': self._primary_frame_failure,
                'source_policy': {'default_profile': FULL, 'selection': 'verified target frames, never executable names',
                    'metric_switches': 'output/statistics gates; do not reconfigure a running frame source',
                    'profiles': list(PROFILES), 'probe_seconds': PROBE_SECONDS,
                    'startup': 'parallel verified DXGI frames; retain and prefer full target frames'},
                'startup_compatibility': startup.diagnostics() if startup is not None else {},
                'active_frame_source': self._active_candidate.diagnostics() if self._active_candidate is not None else {},
                'source_probe': self._probing_candidate.diagnostics() if self._probing_candidate is not None else {},
                'source_probe_history': list(self._candidate_failures),
                'source_transitions': list(self._source_transitions),
                'jank_contract': JANK_CONTRACT,
                'jank_calculation': 'derived locally from retained ordered frame intervals',
                'render_stream': self._render_stream.snapshot(),
                'integrity': {'queue_overflow_rows': overflow,
                    'filtered_other_pid_rows': self._filtered_other_pids,
                    'invalid_frame_rows': self._frame_invalid_rows,
                    'duplicate_rows': self._frame_duplicate_rows,
                    'out_of_order_rows': self._frame_out_of_order_rows,
                    'excluded_explicit_generated_frame_rows': self._generated_frame_rows,
                    'etw_loss_reported': self._etw_loss_reported or bool(dxgi.get('events_or_buffers_lost')),
                    'last_window': dict(self._last_window_quality),
                    'completeness': 'known_loss' if overflow or self._etw_loss_reported or dxgi.get('events_or_buffers_lost') else 'not_proven',
                    'note': '未报告丢失不代表事件完整；过滤其他进程/交换链不算 ETW 丢失。'},
                'process_scope': {'selected_pid': self._target_pid or self._recorded_target_pid,
                    'resource_scope': self._resource_scope,
                    'cpu_memory_io_threads': 'selected PID and verified descendants' if self._resource_scope == 'tree' else 'selected PID only',
                    'gpu_counters': 'selected PID and verified descendants' if self._resource_scope == 'tree' else 'selected PID only',
                    'frames': 'dominant render stream within selected PID and descendants',
                    'related_pids': sorted(self._frame_pid_filter or {self._target_pid or self._recorded_target_pid}),
                    **self._resource_snapshot},
                'mode': DXGI if self._startup_using else self._capture_profile,
                'fps_definition': '1000 / mean application FrameTime(ms), not displayed FPS',
                'presentation_fallback': self._presentmon_presentation_fallback or self._startup_using,
                'unavailable_frame_metrics': sorted(unavailable)
                    if self._presentmon_presentation_fallback or self._startup_using else [],
                'csv_rows': self._presentmon_read_rows,
                'csv_parse_errors': self._presentmon_parse_errors,
                'target_rows': self._present_frame_count,
                'last_valid_frame_age_s': max(0.0, time.monotonic() - self._last_valid_frame_at)
                    if self._last_valid_frame_at else None,
                'automatic_restarts': self._presentmon_restarts,
                'stderr': list(self._presentmon_stderr), 'status': self.status}

    def _related_pids(self) -> set[int]:
        """PIDs whose present events count as the target's.

        A game may hand presentation to a child process, so the whole subtree
        counts. Resolved once per sample: doing it per frame row made a 60 FPS
        capture walk the process tree sixty times a second.
        """
        pids = {self._target_pid}
        if self._process is not None:
            try:
                self._sample_related_children = tuple(self._process.children(recursive=True))
                pids.update(child.pid for child in self._sample_related_children)
            except psutil.Error:
                pass
        return pids

    def sample(self, elapsed: float) -> Sample:
        if self._process is None:
            raise RuntimeError("collector is stopped")
        if not self._process.is_running():
            raise RuntimeError("目标游戏进程已退出，已停止 Windows 采样")
        if self._frame_reset_pending:
            self._frame_history.clear()
            self._stream_last_time.clear()
            self._fps_stabilizer.reset()
            self._healthy_frame_since = 0.0
            self._last_per_cpu_times = None
            self._system_counters.close()
            self._frame_reset_pending = False
        self._prime_pending_sources()
        now = time.monotonic()
        self._sample_related_children = None
        values: dict[str, float | None] = {}
        frame_times: list[float] = []
        raw_frames = []
        timing_fields = {
            "cpu_frame_busy": ("cpubusy", "mscpubusy"),
            "cpu_frame_wait": ("cpuwait", "mscpuwait"),
            "gpu_time": ("gputime", "msgputime"),
            "gpu_busy": ("gpubusy", "msgpubusy"),
            "gpu_wait": ("gpuwait", "msgpuwait"),
            "gpu_latency": ("gpulatency", "msgpulatency"),
        }
        frame_timings: dict[str, list[float]] = {
            key: [] for key in timing_fields if key in self._enabled}
        target_rows = 0
        dropped_rows = 0
        drop_known_rows = 0
        related_pids: set[int] | None = None
        if FRAME_METRICS & self._enabled:
            # Refresh even in an empty window, otherwise a newly spawned render
            # child would be filtered forever by the reader's old PID set.
            related_pids = self._related_pids()
            self._frame_pid_filter = frozenset(related_pids)
        candidate = self._active_candidate
        if candidate is not None:
            self._presentmon_read_rows = candidate.csv_rows
            self._presentmon_parse_errors = candidate.parse_errors
            if candidate.profile == DXGI:
                self._dxgi_rows = candidate.valid_rows
            self._presentmon_stderr = list(candidate.stderr)
            if any('events were lost' in line.casefold() for line in candidate.stderr):
                self._etw_loss_reported = True
            overflow = max(0, candidate.buffer_dropped - self._active_candidate_reported_drops)
            self._active_candidate_reported_drops = candidate.buffer_dropped
            if overflow:
                with self._frame_quality_lock:
                    self._queue_overflow_rows += overflow
            for row in candidate.take_rows():
                self._enqueue_present_row(row)
        prepared_rows = []
        # Drain only the rows present at the start of this sample. The reader
        # may keep adding rows concurrently; chasing it until an empty instant
        # can otherwise prevent the sample from ever completing.
        pending_rows = self._present_rows.qsize()
        for _ in range(pending_rows):
            try:
                row = self._present_rows.get_nowait()
            except queue.Empty:
                break
            if related_pids is None:
                related_pids = self._related_pids()
            try:
                normalized = {str(key).lstrip('\ufeff').strip().casefold(): value for key, value in row.items()}
                row_pid_text = str(normalized.get("processid") or normalized.get("process id") or "0")
                row_pid = int(float(row_pid_text))
                application = str(normalized.get("application") or "").casefold().removesuffix(".exe")
                if row_pid not in related_pids:
                    continue
                frame_type = str(normalized.get('frametype') or '').strip().casefold()
                if frame_type in {'intel xess-fg', 'amd afmf', 'repeated'}:
                    # Do not turn explicitly identified generated/repeated
                    # display frames into application-rendered FPS. Missing
                    # FrameType does not prove that frame generation is absent.
                    self._generated_frame_rows += 1
                    continue
                candidate_time = _finite_number(normalized, "frametime", "msbetweenpresents")
                if candidate_time is None or not 0 < candidate_time <= MAX_FRAME_TIME_MS:
                    self._frame_invalid_rows += 1
                    continue
                prepared_rows.append(normalized)
            except (TypeError, ValueError):
                self._frame_invalid_rows += 1
        prepared_rows, startup_frames = self._startup_frame_rows(prepared_rows, now)
        startup = self._startup_candidate
        frame_started_at = startup.started_at if startup_frames else self._presentmon_started_at
        frame_session = startup.session if startup_frames else self._presentmon_session
        native_frames = startup_frames or self._dxgi_source is not None
        tracks_gpu = self._presentmon_tracks_gpu and not startup_frames
        tracks_display = self._presentmon_tracks_display and not startup_frames
        presentation_fallback = self._presentmon_presentation_fallback or startup_frames
        if prepared_rows:
            self._startup_using = startup_frames
            self._select_sample_frame_session(frame_session)
        previous_stream = self._render_stream.selected
        selected_stream = self._render_stream.choose(prepared_rows)
        if previous_stream != selected_stream and selected_stream is not None:
            self._frame_history.clear()
            self._fps_stabilizer.reset()
        duplicate_start, order_start = self._frame_duplicate_rows, self._frame_out_of_order_rows
        for normalized in prepared_rows:
            if RenderStreamSelector.identity(normalized) != selected_stream:
                continue
            try:
                row_pid = RenderStreamSelector.identity(normalized)[0]
                # Detect replay/reset using timestamps, not identical durations:
                # consecutive equal FrameTime values are perfectly legitimate.
                source_stamp = _finite_number(normalized, 'cpustarttime')
                if source_stamp is not None:
                    source_stamp /= 1000.
                else:
                    source_stamp = _finite_number(normalized, 'timeinseconds')
                last_stamp = self._stream_last_time.get(selected_stream)
                if source_stamp is not None and last_stamp is not None and source_stamp <= last_stamp:
                    if source_stamp == last_stamp:
                        self._frame_duplicate_rows += 1
                    else:
                        self._frame_out_of_order_rows += 1
                    continue
                if source_stamp is not None:
                    # Bounded by current stream; switching must not grow a map
                    # for every transient overlay or recreated swapchain.
                    self._stream_last_time = {selected_stream: source_stamp}
                target_rows += 1
                dropped_value = normalized.get('dropped')
                if dropped_value in (None, ''):
                    dropped_value = normalized.get('wasdropped')
                dropped_marker = str(dropped_value).strip().casefold()
                displayed_marker = normalized.get("displayedtime")
                if displayed_marker is None:
                    displayed_marker = normalized.get("msdisplayedtime")
                display_text = str(displayed_marker).strip().casefold() if displayed_marker is not None else ''
                numeric_display = _finite_number(normalized, 'displayedtime', 'msdisplayedtime')
                explicit_drop = dropped_marker in {'1','true','yes','0','false','no'}
                # Empty/malformed columns are unknown, not successful displays.
                drop_known = tracks_display and (explicit_drop or
                    display_text in {'na','n/a'} or numeric_display is not None and numeric_display >= 0)
                was_dropped = (dropped_marker in {'1','true','yes'} or
                               not explicit_drop and display_text in {'na','n/a'})
                if drop_known and was_dropped:
                    dropped_rows += 1
                if drop_known:
                    drop_known_rows += 1
                frame_time = _finite_number(normalized, "frametime", "msbetweenpresents")
                if frame_time is not None and 0 < frame_time <= MAX_FRAME_TIME_MS:
                    frame_times.append(frame_time)
                    source_time = _finite_number(normalized, 'cpustarttime')
                    if source_time is not None:
                        source_time /= 1000.0
                    else:
                        source_time = _finite_number(normalized, 'timeinseconds')
                    aligned = (float(elapsed) + source_time - (now-frame_started_at)
                               if source_time is not None and frame_started_at else float(elapsed))
                    raw_frames.append(dict(x=max(0., aligned), frame_ms=frame_time,
                        source=WINDOWS_DXGI_FRAME_SOURCE if native_frames else WINDOWS_PRESENTMON_FRAME_SOURCE,
                        source_time_s=source_time,
                        clock='trace_relative_host_start_estimate' if source_time is not None else 'receipt_only',
                        pid=row_pid, swapchain=str(normalized.get('swapchainaddress') or ''),
                        stream_epoch=f'{self._presentmon_generation}:{self._queue_overflow_rows}:{self._jank_epoch}',
                        cpu_busy=_finite_number(normalized, 'cpubusy', 'mscpubusy'),
                        gpu_busy=(_finite_number(normalized, 'gpubusy', 'msgpubusy')
                                  if tracks_gpu and not presentation_fallback else None),
                        dropped=was_dropped if drop_known else None))
                for key, aliases in timing_fields.items():
                    if key not in frame_timings:
                        continue
                    value = _finite_number(normalized, *aliases)
                    if key in PRESENTMON_GPU_METRICS and (not tracks_gpu or presentation_fallback):
                        continue
                    if value is not None and 0 <= value <= MAX_FRAME_TIME_MS:
                        frame_timings[key].append(value)
            except (TypeError, ValueError):
                continue
        if target_rows:
            self._present_frame_count += target_rows
        self._last_window_quality = {'accepted_frames': target_rows, 'known_display_status_frames': drop_known_rows,
            'unknown_display_status_frames': target_rows-drop_known_rows,
            'duplicate_rows': self._frame_duplicate_rows-duplicate_start,
            'out_of_order_rows': self._frame_out_of_order_rows-order_start}
        if frame_times:
            if startup_frames:
                self._startup_verified_at = now
                self._startup_last_event_at = max(
                    (frame_started_at + frame['source_time_s'] for frame in raw_frames
                     if frame['source_time_s'] is not None), default=self._startup_last_event_at)
            if not self._healthy_frame_since or now - self._last_valid_frame_at >= FRAME_EVENT_GRACE_SECONDS:
                self._healthy_frame_since = now
            self._last_valid_frame_at = now
            if self._active_candidate is not None or self._presentmon_error.startswith(('Windows 帧', '目标帧', '所选进程', '已验证目标帧')):
                self._presentmon_error = ''
            average = sum(frame_times) / len(frame_times)
            measured_fps = self._fps_stabilizer.accept(min(1000.0, 1000.0 / average))
            if "fps" in self._enabled: values["fps"] = measured_fps
            if "frame_time" in self._enabled: values["frame_time"] = average
            small_jank = sum(16.67 < item <= 33.34 for item in frame_times)
            jank = sum(33.34 < item <= 50.01 for item in frame_times)
            big_jank = sum(item > 50.01 for item in frame_times)
            if "small_jank" in self._enabled: values["small_jank"] = float(small_jank)
            if "jank" in self._enabled: values["jank"] = float(jank)
            if "big_jank" in self._enabled: values["big_jank"] = float(big_jank)
            if "jank_rate" in self._enabled:
                values["jank_rate"] = (jank + big_jank) / len(frame_times) * 100.0
            if {"frame_time_p95", "frame_time_p99"} & self._enabled:
                ordered_frame_times = sorted(frame_times)
                if "frame_time_p95" in self._enabled:
                    values["frame_time_p95"] = _percentile_from_ordered(ordered_frame_times, .95)
                if "frame_time_p99" in self._enabled:
                    values["frame_time_p99"] = _percentile_from_ordered(ordered_frame_times, .99)
            self._frame_history.extend((float(elapsed), item) for item in frame_times)
            cutoff = float(elapsed) - FRAME_HISTORY_SECONDS
            while self._frame_history and self._frame_history[0][0] < cutoff:
                self._frame_history.popleft()
            if {"fps_1_low", "fps_0_1_low"} & self._enabled:
                slowest_first = sorted((item for _stamp, item in self._frame_history),
                                       reverse=True)
                if "fps_1_low" in self._enabled:
                    values["fps_1_low"] = _low_fps_from_slowest(slowest_first, .01)
                if "fps_0_1_low" in self._enabled:
                    values["fps_0_1_low"] = _low_fps_from_slowest(slowest_first, .001)
        if drop_known_rows:
            if "dropped_frames" in self._enabled:
                values["dropped_frames"] = float(dropped_rows)
            if "dropped_frame_rate" in self._enabled:
                values["dropped_frame_rate"] = dropped_rows / drop_known_rows * 100.0
        for key, rows in frame_timings.items():
            if rows:
                values[key] = sum(rows) / len(rows)
        if not target_rows and FRAME_METRICS & self._enabled and not self._presentmon_error:
            # The grace period runs from when PresentMon started, not from the
            # start of collection: frame metrics may have been enabled later.
            if self._presentmon is None:
                self._presentmon_error = "Windows 帧数据源正在启动，请稍候"
            elif (self._present_frame_count == 0 and self._presentmon_started_at
                  and time.monotonic() - self._presentmon_started_at >= FRAME_EVENT_GRACE_SECONDS):
                self._presentmon_error = "Windows 帧数据未收到目标进程的有效 ETW 呈现帧；请查看采集诊断"

        resource_enabled = self._enabled if self._resource_scope == 'selected' else frozenset()
        resource_pids = {self._target_pid}
        if self._resource_scope == 'tree':
            self._resource_sampler.cpu_count = self._cpus
            resource_values, resource_pids = self._resource_sampler.sample(
                self._process, now, self._enabled, self._sample_related_children)
            values.update(resource_values)
            self._resource_snapshot = dict(self._resource_sampler.last)
            if self._resource_identities != self._resource_sampler.identities:
                self._gpu_counters.close()
                self._resource_identities = dict(self._resource_sampler.identities)

        if PROCESS_CPU_METRICS & resource_enabled:
            try:
                current_cpu_times = self._process.cpu_times()
                if self._last_cpu_times is not None:
                    cpu_interval = max(.001, now - self._last_cpu_time)
                    user_cpu = max(0.0, current_cpu_times.user - self._last_cpu_times.user) / cpu_interval / self._cpus * 100.0
                    kernel_cpu = max(0.0, current_cpu_times.system - self._last_cpu_times.system) / cpu_interval / self._cpus * 100.0
                    user_cpu, kernel_cpu = min(100.0, user_cpu), min(100.0, kernel_cpu)
                    if "app_user_cpu" in self._enabled: values["app_user_cpu"] = user_cpu
                    if "app_kernel_cpu" in self._enabled: values["app_kernel_cpu"] = kernel_cpu
                    if "app_cpu" in self._enabled: values["app_cpu"] = min(100.0, user_cpu + kernel_cpu)
                self._last_cpu_times = current_cpu_times
                self._last_cpu_time = now
            except (psutil.Error, OSError):
                pass
        system_cpu_keys = SYSTEM_CPU_METRICS & self._enabled
        per_core_keys = {int(match.group(1)): key for key in self._enabled
                         for match in (PER_CORE_USAGE_PATTERN.match(key),) if match}
        if per_core_keys or system_cpu_keys:
            try:
                current_per_cpu = psutil.cpu_times(percpu=True)
                if (self._last_per_cpu_times is not None and
                        len(current_per_cpu) == len(self._last_per_cpu_times)):
                    if system_cpu_keys and len(current_per_cpu) >= self._cpus:
                        values.update(_system_cpu_usage(current_per_cpu, self._last_per_cpu_times, system_cpu_keys))
                    for index, key in per_core_keys.items():
                        if index >= len(current_per_cpu):
                            continue
                        total, busy = _cpu_total_and_busy(current_per_cpu[index])
                        old_total, old_busy = _cpu_total_and_busy(self._last_per_cpu_times[index])
                        total_delta = total - old_total
                        if total_delta > 0:
                            values[key] = max(0.0, min(100.0, (busy - old_busy) / total_delta * 100.0))
                self._last_per_cpu_times = current_per_cpu
            except (OSError, RuntimeError):
                self._last_per_cpu_times = None

        memory_needed = bool({"memory", "private_memory", "virtual_memory", "page_faults"}
                             & resource_enabled)
        try:
            memory = self._process.memory_info() if memory_needed else None
        except (psutil.Error, OSError):
            memory = None
        if "memory" in resource_enabled and memory is not None:
            values["memory"] = memory.rss / 1048576
        if memory is not None and {"private_memory", "virtual_memory"} & resource_enabled:
            if "private_memory" in self._enabled:
                if getattr(memory, "private", None) is not None:
                    values["private_memory"] = float(memory.private) / 1048576
            if "virtual_memory" in self._enabled:
                values["virtual_memory"] = memory.vms / 1048576
        if "page_faults" in resource_enabled and memory is not None:
            try:
                page_faults = int(memory.num_page_faults)
                if self._last_page_faults is not None:
                    fault_interval = max(.001, now - self._last_page_fault_time)
                    values["page_faults"] = max(0, page_faults - self._last_page_faults) / fault_interval
                self._last_page_faults = page_faults
                self._last_page_fault_time = now
            except AttributeError:
                pass
        if SYSTEM_RAM_METRICS & self._enabled:
            try:
                physical = psutil.virtual_memory()
                if physical.total > 0 and 0 <= physical.available <= physical.total:
                    if "available_memory" in self._enabled:
                        values["available_memory"] = physical.available / 1048576
                    if "system_memory_used" in self._enabled:
                        values["system_memory_used"] = (physical.total - physical.available) / 1048576
            except (psutil.Error, OSError):
                pass
        if "thread_count" in resource_enabled:
            values["thread_count"] = float(self._process.num_threads())
        if "handle_count" in resource_enabled:
            values["handle_count"] = float(self._process.num_handles())

        if PROCESS_IO_METRICS & resource_enabled:
            try:
                current_io = self._process.io_counters()
                if self._last_io is not None:
                    io_interval = max(0.001, now - self._last_io_time)
                    if "disk_read" in self._enabled:
                        values["disk_read"] = max(0.0, current_io.read_bytes - self._last_io.read_bytes) / 1024 / io_interval
                    if "disk_write" in self._enabled:
                        values["disk_write"] = max(0.0, current_io.write_bytes - self._last_io.write_bytes) / 1024 / io_interval
                    if "disk_read_iops" in self._enabled:
                        values["disk_read_iops"] = max(0, current_io.read_count - self._last_io.read_count) / io_interval
                    if "disk_write_iops" in self._enabled:
                        values["disk_write_iops"] = max(0, current_io.write_count - self._last_io.write_count) / io_interval
                self._last_io = current_io
                self._last_io_time = now
            except (psutil.Error, OSError):
                pass
        if {"network_send", "network_recv"} & self._enabled:
            current_network = psutil.net_io_counters()
            if self._last_network is not None:
                network_interval = max(0.001, now - self._last_network_time)
                if "network_send" in self._enabled:
                    values["network_send"] = max(0.0, current_network.bytes_sent - self._last_network.bytes_sent) / 1024 / network_interval
                if "network_recv" in self._enabled:
                    values["network_recv"] = max(0.0, current_network.bytes_recv - self._last_network.bytes_recv) / 1024 / network_interval
            self._last_network = current_network
            self._last_network_time = now

        system_pdh = self._system_counters.sample(self._enabled, now)
        values.update(system_pdh)

        if GPU_PROCESS_METRICS & self._enabled:
            # GPU counters follow the same chosen population, but absent PDH
            # instances are not proof that a process consumed zero GPU resources.
            if not self._resource_snapshot.get('resource_discovery_failed'):
                values.update(self._gpu_counters.sample(resource_pids, self._enabled))
            else:
                self._gpu_counters.close()
        else:
            self._gpu_counters.close()
        if not self._process.is_running():
            raise RuntimeError("目标游戏进程已退出，已停止 Windows 采样")
        if self._resource_scope == 'selected':
            requested = self._enabled & PROCESS_KEYS
            self._resource_snapshot = {
                'resource_pids': [self._target_pid], 'resource_candidate_pids': [self._target_pid],
                'resource_failed_pids': [], 'resource_discovery_failed': False,
                'resource_metric_coverage': {key: {
                    'received_pids': [self._target_pid] if key in values else [],
                    'missing_pids': [] if key in values else [self._target_pid],
                    'warming_pids': [], 'complete': key in values} for key in requested},
                'resource_completeness': 'not_proven'}
        if GPU_PROCESS_METRICS & self._enabled:
            self._resource_snapshot['gpu_metric_pids'] = {
                key: sorted({int(match.group(1)) for instance in getattr(self._gpu_counters, '_instances', {}).get(key, ())
                             if (match := re.match(r'^pid_(\d+)_', instance, re.IGNORECASE))})
                for key in self._enabled & GPU_PROCESS_METRICS}
            self._resource_snapshot['gpu_completeness'] = 'not_proven'
        # Ordered frames are the input; Jank is derived locally, never polled
        # from the game. Annotate all retained frames so display toggles do not
        # break the baseline or leave reporting/export without the calculation.
        if self._jank_reset_pending:
            self._jank_tracker.reset()
            self._jank_reset_pending = False
        raw_frames, jank_values = self._jank_tracker.annotate(raw_frames)
        values.update({key: jank_values[key] for key in self._enabled & JANK_METRICS})
        long_frame = max(frame_times, default=0)
        raw_values = {key: value for key, value in {
            'fps': 1000.0 / (sum(frame_times) / len(frame_times)) if frame_times else None,
            'frame_time': sum(frame_times) / len(frame_times) if frame_times else None,
        }.items() if key in values}
        if long_frame > 5000:
            raw_values['long_frame_max_ms'] = long_frame
        frame_source = 'DXGI ETW' if native_frames else 'PresentMon'
        system_sources = {key: 'Windows PDH system counter' for key in system_pdh}
        system_sources.update({key: 'Windows system CPU times' for key in values.keys() & SYSTEM_CPU_METRICS})
        system_sources.update({key: 'Windows physical memory snapshot' for key in values.keys() & SYSTEM_RAM_METRICS})
        sample = self.audited_sample(elapsed, values, f'Windows {frame_source} / process / PDH',
            frames=tuple(raw_frames), sources=system_sources,
            raw_values=raw_values,
            adjustments={key: 'long_complete_frame_needs_review'
                         for key in ('frame_time', 'frame_time_p95', 'frame_time_p99')
                         if key in values and long_frame > 5000})
        # Resource deltas and frame provenance refer to the same snapshot.
        # Source teardown can wait for ETW/process I/O; do it after that snapshot
        # so the wait cannot inflate CPU/IO rates or relabel the previous rows.
        self._recover_silent_frame_source(time.monotonic())
        return sample

    def stop(self) -> None:
        with self._presentmon_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._stop_frame_candidates()
        self._terminate_presentmon()
        self._gpu_counters.close()
        self._system_counters.close()
        self._presentmon_unavailable = False
        self._presentmon_started_at = 0.0
        self._presentmon_owned = False
        self._present_frame_count = 0
        self._frame_history.clear()
        self._target_pid = 0
        self._target_process_name = ""
        self._process = None
        self._enabled = frozenset()
        self._last_io = None
        self._last_network = None
        self._last_cpu_times = None
        self._last_page_faults = None
        self._last_per_cpu_times = None
        self._process_cpu_active = False
        self._per_core_active = False
        self._io_active = False
        self._network_active = False
        self._page_fault_active = False
        self._resource_sampler.reset()
        self._sample_related_children = None

    @property
    def status(self) -> str:
        if not (FRAME_METRICS & self._enabled):
            return ''
        if self._startup_using and self._startup_candidate is not None:
            return '正在采集 FPS，其他帧指标准备中'
        source = self._dxgi_source
        if source is not None:
            if source.error:
                return source.error
            if self._presentmon_error:
                return self._presentmon_error
            return 'DirectX 兼容采集：应用呈现 FPS；不提供显示丢帧或逐帧 GPU 时序'
        if self._presentmon_stderr:
            detail = self._presentmon_stderr[-1]
            if "events were lost" in detail.lower():
                if self._present_frame_count:
                    return "Windows 帧数据已接收，但存在 ETW 事件丢失；请查看采集诊断"
                return "Windows 帧指标不可用：PresentMon 的 ETW 帧事件发生丢失，请关闭其他监控/覆盖层后重试"
            if "access denied" in detail.lower():
                return "Windows 帧指标需要管理员权限，或将当前用户加入 Performance Log Users 组"
            return detail
        if self._presentmon and self._presentmon.poll() not in (None, 0):
            # Only the dedicated reader owns stderr. Reading it here races that
            # thread and can block the GUI during source failure/teardown.
            return "PresentMon 已停止，帧源等待自动重连"
        if self._presentmon_error:
            return self._presentmon_error
        if self._present_frame_count and not self._presentmon_tracks_display:
            return "Windows 应用呈现 FPS 已接收（非屏幕显示帧率）；显示丢帧/GPU逐帧时序未采集"
        return ""
