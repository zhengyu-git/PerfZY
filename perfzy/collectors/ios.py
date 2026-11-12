from __future__ import annotations

import json
import math
import subprocess
import threading
import time
from collections import deque
from statistics import median
from typing import Any

from ..domain.models import Device, Sample, Target
from ..domain.ios_identity import DECLARED_GAME_FPS_LIMITS, ios_bundle_id
from ..domain.jank import FrameJankTracker, JANK_CONTRACT, JANK_METRICS
from ..domain.validation import MAX_FRAME_TIME_MS
from .ios_frames import FRAME_SOURCE, FPS_CONTRACT, TRACE_METRICS, fps_from_intervals
from .base import Collector, FpsStabilizer
from .discovery import discover_ios
from .ios_runtime import command, run_json
from .targets import ios_targets


def _number(data: dict[str, Any], *names: str) -> float | None:
    def normalized(value: str) -> str:
        return "".join(character for character in value.casefold() if character.isalnum())
    wanted = {normalized(name) for name in names}
    for key, value in data.items():
        clean_key = normalized(str(key).rsplit(".", 1)[-1])
        if clean_key not in wanted or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _process_activity_state(data: dict[str, Any]) -> bool | None:
    """Return an explicit running/suspended state when DVT exposes one.

    Sysmontap schemas vary between iOS releases.  Some include a boolean
    suspension flag, while others expose a textual run state and older
    versions expose neither.  Unknown values deliberately stay ``None`` so a
    schema change cannot incorrectly hide valid foreground samples.
    """
    suspended_names = {"issuspended", "suspended"}
    running_names = {"isrunning", "running", "foregroundrunning"}
    state_names = {"state", "processstate", "runstate", "executionstate"}
    for key, value in data.items():
        clean_key = "".join(character for character in str(key).rsplit(".", 1)[-1].casefold()
                            if character.isalnum())
        if clean_key in suspended_names:
            if isinstance(value, bool):
                return not value
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return not bool(value)
        if clean_key in running_names and isinstance(value, bool):
            return value
        if clean_key in state_names and isinstance(value, str):
            state = value.casefold()
            if any(word in state for word in ("suspend", "background", "inactive", "stopped")):
                return False
            if any(word in state for word in ("foreground", "active", "running")):
                return True
    return None


class IOSCollector(Collector):
    """Real-device iOS collector backed by Apple's Instruments DVT services."""

    def __init__(self) -> None:
        super().__init__()
        self._configuration_lock = threading.Lock()
        self._sample_interval_ms = 1000
        self._sent_configuration = None
        self._watchdog_seconds = 5.0
        self._subscription = {}
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest_process: dict[str, Any] = {}
        self._latest_system: dict[str, Any] = {}
        self._latest_graphics: dict[str, Any] = {}
        self._latest_battery: dict[str, Any] = {}
        self._field_received_at: dict[str, dict[str, float]] = {
            source: {} for source in ("process", "system", "graphics", "battery")
        }
        self._counter_baselines: dict[str, tuple[float, float]] = {}
        self._last_observation = {}
        self._last_fps_observation = None
        self._last_fps_result = None
        self._held_observations = 0
        self._observation_generation = 0
        self._fps_stabilizer = FpsStabilizer()
        self._jank_tracker = FrameJankTracker()
        self._trace_frames = deque(maxlen=20000)
        self._trace_anchor = None
        self._trace_error = ''
        self._trace_received = self._trace_dropped = 0
        self._trace_paused = False
        self._trace_epoch = 0
        self._frame_fps_observation = None
        self._metric_sources = {}
        self._metric_source_counts = {}
        self._fps_limit = None
        self._fps_limit_explicit = False
        self._fps_above_limit = 0
        self._fps_source_observations = self._fps_integer_observations = 0
        self._last_fps_source_token = None
        self._fps_limit_warning = False
        # Compatibility aliases retained for diagnostics and regression tests.
        self._fps_history = self._fps_stabilizer.history
        self._fps_high_candidates = self._fps_stabilizer.high_candidates
        self._ready = threading.Event()
        self._seen_target = threading.Event()
        self._error = ""
        self._last_sysmon_at: float | None = None
        self._stream_closed = threading.Event()
        self._status = ""
        # Graphics is a device-wide CoreAnimation stream, not a PID-scoped
        # stream.  These guards keep SpringBoard's frame rate from being
        # attributed to the game while it is suspended or in the app switcher.
        self._target_process_active: bool | None = None
        self._target_missing_streak = 0
        self._active_cpu_history: deque[float] = deque(maxlen=12)
        self._graphics_mismatch_streak = 0
        self._graphics_suppressed = False

    @property
    def status(self) -> str:
        return self._status

    def discover_devices(self): return discover_ios()
    def discover_targets(self, device: Device): return ios_targets(device)

    def set_sample_interval_ms(self, interval: int) -> None:
        self._sample_interval_ms = max(500, int(interval))

    @property
    def fps_limit(self) -> float:
        return float(self._fps_limit or 0)

    def set_fps_limit(self, value) -> None:
        if isinstance(value, bool):
            raise ValueError("iOS FPS 上限必须为 0 或 4–240 之间的有限数值")
        try:
            limit = float(value)
        except (TypeError, ValueError, OverflowError):
            limit = math.nan
        if not math.isfinite(limit) or (limit != 0 and not 4 <= limit <= 240):
            raise ValueError("iOS FPS 上限必须为 0 或 4–240 之间的有限数值")
        self._fps_limit, self._fps_limit_explicit = limit, True

    def activate_pending_sources(self) -> None:
        with self._configuration_lock:
            configuration = (tuple(sorted(self._enabled)), self._sample_interval_ms)
            process = self._process
            if configuration == self._sent_configuration or not process or process.poll() is not None:
                return
            try:
                process.stdin.write(json.dumps({"metrics": configuration[0], "interval": configuration[1]}) + "\n")
                process.stdin.flush()
                self._sent_configuration = configuration
            except (OSError, ValueError):
                self._status = "iOS 采样设置发送失败，请重新开始采集"

    def set_capture_paused(self, paused):
        with self._lock:
            self._trace_paused = bool(paused)
            self._trace_epoch += 1
            self._observation_generation += 1
            self._trace_frames.clear()
            self._trace_anchor = None
            self._frame_fps_observation = None
            self._jank_tracker.reset()

    def start(self, device: Device, target: Target, enabled_metrics: frozenset[str]) -> None:
        self.stop()
        run_json("preflight", timeout=20, strict=True)
        if not self._fps_limit_explicit:
            self._fps_limit = float(DECLARED_GAME_FPS_LIMITS.get(ios_bundle_id({"id": target.id}), 0))
        self._fps_above_limit = 0
        self._fps_source_observations = self._fps_integer_observations = 0
        self._last_fps_source_token = None
        self._fps_limit_warning = False
        self._enabled = enabled_metrics
        self._trace_received = self._trace_dropped = 0
        self._trace_paused = False
        self._frame_fps_observation = None
        self._metric_sources.clear()
        self._metric_source_counts.clear()
        self._device_cpu_count = _number(device.details, "cpu_cores")
        with self._lock:
            self._trace_frames.clear()
            self._trace_anchor = None
            self._trace_error = ''
            self._trace_epoch += 1
            self._jank_tracker.reset()
            self._latest_process = {}
            self._subscription = {}
            self._latest_system = {}
            self._latest_graphics = {}
            self._latest_battery = {}
            self._last_sysmon_at = None
            for timestamps in self._field_received_at.values():
                timestamps.clear()
            self._counter_baselines.clear()
            self._last_observation.clear()
            self._last_fps_observation = None
            self._last_fps_result = None
            self._held_observations = 0
            self._observation_generation += 1
        self._stream_closed.clear()
        self._fps_stabilizer.reset()
        self._target_process_active = None
        self._target_missing_streak = 0
        self._active_cpu_history.clear()
        self._graphics_mismatch_streak = 0
        self._graphics_suppressed = False
        udid = device.id.partition(":")[2]
        bundle = target.id.split(":", 2)[-1]
        self._ready.clear(); self._seen_target.clear(); self._error = ""; self._status = "正在连接 iOS Instruments"
        self._process = subprocess.Popen(
            command("stream", "--udid", udid, "--bundle", bundle, "--interval", str(self._sample_interval_ms),
                    "--metrics", ",".join(sorted(enabled_metrics))),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._sent_configuration = (tuple(sorted(enabled_metrics)), self._sample_interval_ms)
        self._reader = threading.Thread(target=self._read_loop, name="PerfZY-iOS-DVT", daemon=True)
        self._reader.start()
        # iOS 17+ uses an RSD userspace tunnel. Its first connection after a
        # cable attach can legitimately take over 30 seconds on Windows.
        if not self._ready.wait(60):
            process = self._process
            if process and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill()
            error = self._read_error() or self._error
            self.stop()
            raise RuntimeError(self._friendly_error(error or "连接 iOS 开发者服务超时"))
        if not self._seen_target.wait(18):
            error = self._read_error()
            self.stop()
            raise RuntimeError(self._friendly_error(error or "目标应用尚未运行，或进程尚未被 iOS Instruments 发现"))
        self._status = "正在采集 iOS 真机指标"

    def _read_loop(self) -> None:
        process = self._process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            self._accept_stream_event(event)
        self._stream_closed.set()
        if process.poll() not in (None, 0):
            self._error = self._read_error()
            self._ready.set()
            self._seen_target.set()

    def _accept_stream_event(self, event: dict[str, Any]) -> None:
        """Merge partial events, retaining each field only for a bounded time.

        Receipt times use the host monotonic clock. An empty event or an update
        to another field must not renew an old reading's lifetime.
        """
        kind = event.get("type")
        if kind == "subscription":
            with self._lock:
                self._subscription = dict(event)
            return
        if kind == "configuration":
            with self._lock:
                self._trace_frames.clear()
                self._trace_anchor = None
                self._frame_fps_observation = None
                self._trace_error = ''
                self._jank_tracker.reset()
                self._observation_generation += 1
                self._subscription = {}
                self._latest_process.clear()
                self._latest_system.clear()
                self._latest_graphics.clear()
                self._latest_battery.clear()
                for timestamps in self._field_received_at.values():
                    timestamps.clear()
                self._counter_baselines.clear()
                self._last_observation.clear()
                self._last_fps_observation = None
                self._last_fps_result = None
                interval = event.get("interval", 1000) if event.get("sysmon") else event.get("guard_interval", 2000)
                self._watchdog_seconds = max(5.0, float(interval) / 1000 * 3)
                self._last_sysmon_at = time.monotonic()
                self._target_missing_streak = 0
                self._target_process_active = None
                self._fps_stabilizer.reset()
                self._active_cpu_history.clear()
                self._graphics_mismatch_streak = 0
                self._graphics_suppressed = False
            return
        if kind == "ready":
            self._ready.set()
            return
        if kind == 'frame_status':
            with self._lock:
                self._trace_error = '' if event.get('available') else str(event.get('message') or 'iOS 逐帧通道不可用')
                if self._trace_error:
                    self._frame_fps_observation = None
                    self._trace_frames.clear()
                    self._jank_tracker.reset()
                    self._status = self._trace_error
            return
        if kind == 'frames':
            with self._lock:
                if not self._enabled & TRACE_METRICS or self._trace_error or self._trace_paused:
                    return
                received = time.monotonic()
                for row in event.get('frames') or []:
                    if not isinstance(row, dict) or row.get('source') != FRAME_SOURCE:
                        continue
                    cost, moment = _number(row, 'frame_ms'), _number(row, 'source_time_s')
                    if cost is None or moment is None or not 0 < cost <= MAX_FRAME_TIME_MS:
                        continue
                    if len(self._trace_frames) == self._trace_frames.maxlen:
                        self._trace_dropped += 1
                        self._trace_error = 'iOS 逐帧缓冲区丢帧，逐帧 FPS / Jank 不可用；请停止后重新采集'
                        self._frame_fps_observation = None
                        self._trace_frames.clear()
                        self._jank_tracker.reset()
                        break
                    self._trace_received += 1
                    self._trace_frames.append((received, dict(row)))
            return
        if kind == "status":
            self._status = str(event.get("message") or self._status)
            return
        if kind == "sysmon":
            with self._lock:
                now = time.monotonic()
                self._last_sysmon_at = now
                incoming_system = event.get("system")
                if isinstance(incoming_system, dict) and incoming_system:
                    self._latest_system.update(incoming_system)
                    self._field_received_at["system"].update(dict.fromkeys(incoming_system, now))
                    if self._status.startswith("iOS 系统指标暂时无数据"):
                        self._status = "正在采集 iOS 真机指标"
                elif self._latest_system:
                    self._status = "iOS 系统指标暂时无数据，正在等待新读数"
                incoming_process = event.get("process")
                if isinstance(incoming_process, dict) and incoming_process:
                    # A sysmontap process row is a complete per-process
                    # snapshot, unlike the independently emitted system keys.
                    previous_pid = _number(self._latest_process, "pid")
                    current_pid = _number(incoming_process, "pid")
                    if previous_pid is not None and current_pid is not None and previous_pid != current_pid:
                        self._trace_epoch += 1
                        self._trace_frames.clear()
                        self._trace_anchor = None
                        self._frame_fps_observation = None
                        self._jank_tracker.reset()
                        self._observation_generation += 1
                        for metric in ("disk_read", "disk_write", "ios_context_switches", "ios_interrupt_wakeups"):
                            self._counter_baselines.pop(metric, None)
                        self._last_observation.clear()
                        self._last_fps_observation = None
                        self._last_fps_result = None
                        self._fps_stabilizer.reset()
                        # Do not attribute the preceding process's device
                        # graphics cache to its replacement before a new event.
                        self._latest_graphics.clear()
                        self._field_received_at['graphics'].clear()
                    self._latest_process = incoming_process
                    self._field_received_at["process"] = dict.fromkeys(incoming_process, now)
                    explicit_state = _process_activity_state(incoming_process)
                    self._target_process_active = True if explicit_state is None else explicit_state
                    self._target_missing_streak = 0
                    self._seen_target.set()
                else:
                    # One empty DVT update is common during a channel refresh;
                    # two consecutive misses mean the selected process is no
                    # longer present in the live process snapshot.
                    self._target_missing_streak += 1
                    if self._target_missing_streak >= 2:
                        self._target_process_active = False
            return
        if kind == "graphics":
            incoming = event.get("data")
            if isinstance(incoming, dict) and incoming:
                with self._lock:
                    self._latest_graphics.update(incoming)
                    self._field_received_at["graphics"].update(dict.fromkeys(incoming, time.monotonic()))
            return
        if kind == "battery":
            incoming = event.get("data")
            if isinstance(incoming, dict) and incoming:
                with self._lock:
                    self._latest_battery.update(incoming)
                    self._field_received_at["battery"].update(dict.fromkeys(incoming, time.monotonic()))

    def _fresh_snapshot(self, source: str, now: float, lifetime: float) -> tuple[dict, dict]:
        """Called with the lock held; CPU core counts are static metadata."""
        cache = getattr(self, f"_latest_{source}")
        timestamps = self._field_received_at[source]
        fresh = {}
        for key, value in cache.items():
            received_at = timestamps.get(key)
            static = source == "system" and _number({key: value}, "CPUCount", "EnabledCPUs") is not None
            if static or (received_at is not None and now - received_at < lifetime):
                fresh[key] = value
        return fresh, {key: timestamps[key] for key in fresh if key in timestamps}

    def _counter_rate(self, metric: str, source: str, data: dict, timestamps: dict,
                      *names: str, divisor: float = 1.0) -> float | None:
        """Difference successive valid observations, never repeated cache reads."""
        for key, raw in data.items():
            value = _number({key: raw}, *names)
            received_at = timestamps.get(key)
            if value is None or value < 0 or received_at is None:
                continue
            with self._lock:
                # A configuration change or newer event may have overtaken the
                # sample snapshot. Do not restore its discarded old baseline.
                if self._field_received_at[source].get(key) != received_at:
                    return None
                previous = self._counter_baselines.get(metric)
                if previous is not None and received_at <= previous[1]:
                    return None
                self._counter_baselines[metric] = (value, received_at)
            if previous is None or value < previous[0]:
                return None
            return (value - previous[0]) / (received_at - previous[1]) / divisor
        return None

    def _read_error(self) -> str:
        process = self._process
        if not process or not process.stderr or process.poll() is None:
            return ""
        try:
            return process.stderr.read().strip()
        except OSError:
            return ""

    @staticmethod
    def _friendly_error(error: str) -> str:
        folded = error.casefold()
        if "pair" in folded or "trust" in folded:
            return "iPhone 尚未信任此电脑，请解锁设备并点按“信任”"
        if "developer mode" in folded:
            return "iPhone 未开启开发者模式，请在“设置 → 隐私与安全性 → 开发者模式”中开启"
        if "developer disk image" in folded or "personalized" in folded or "tunnel" in folded:
            return "iOS 开发者服务不可用，请保持设备解锁并确认开发者镜像与 USB 隧道可用"
        return error.splitlines()[-1].removeprefix("IOS_ERROR: ")[:350]

    def diagnostics(self) -> dict:
        """Cached subscription evidence only; never contact a device here."""
        from .ios_sampling import PROCESS_FIELDS, SYSTEM_FIELDS
        with self._lock:
            subscription = dict(self._subscription)
            now = time.monotonic()
            channel_age = {source: max(0., now-max(stamps.values())) if stamps else None
                           for source, stamps in self._field_received_at.items()}
            metric_sources = dict(self._metric_sources)
            metric_source_counts = {key: dict(counts) for key, counts in self._metric_source_counts.items()}
        unsupported = {}
        if subscription:
            for key in self._enabled:
                if key in {'app_cpu', 'ios_physical_footprint'}:
                    # These can also be produced through Sysmontap config flags.
                    continue
                fields, actual = (PROCESS_FIELDS.get(key), subscription.get('process_fields', ()))
                if key in SYSTEM_FIELDS:
                    fields, actual = SYSTEM_FIELDS[key], subscription.get('system_fields', ())
                if fields and not fields <= set(actual):
                    unsupported[key] = '本轮 Sysmontap 订阅未提供字段：' + ', '.join(sorted(fields - set(actual)))
        if self._trace_error:
            unsupported.update({key: self._trace_error for key in self._enabled & JANK_METRICS})
        return {'subscription': subscription, 'unsupported_metrics': unsupported,
                'observation_policy': 'host-monotonic per-field receipt; cached values not resampled',
                'channel_age_s': channel_age, 'held_observations': self._held_observations,
                'frame_source': 'device CoreAnimation, activity guarded; not PID-isolated',
                'fps_method': FPS_CONTRACT,
                'fps_method_description': '1000 * interval count / sum(actual CoreProfile frame_ms); CoreAnimation counter fallback',
                'metric_sources': metric_sources,
                'metric_source_counts': metric_source_counts,
                'frame_time_method': '1000 / smoothed FPS, estimated only',
                'raw_frame_events': bool(self._trace_received), 'gpu_deep_counters': False,
                'jank_contract': JANK_CONTRACT, 'frame_trace_source': FRAME_SOURCE,
                'frame_trace_scope': 'device CoreAnimation, activity guarded; not PID-isolated',
                'frame_trace_received': self._trace_received, 'frame_trace_dropped': self._trace_dropped,
                'frame_trace_error': self._trace_error,
                'fps_limit': self.fps_limit,
                'fps_above_limit_observations': self._fps_above_limit,
                'fps_source_observations': self._fps_source_observations,
                'fps_integer_observations': self._fps_integer_observations,
                'fps_limit_policy': 'configured cap validates observations; exceeding values are missing, raw readings retained',
                'gpu_scope': 'device Graphics, not PID-isolated',
                'network_scope': 'device system.netBytesOut/netBytesIn',
                'sample_interval_ms': self._sample_interval_ms}

    def sample(self, elapsed: float) -> Sample:
        if self._process and (self._process.poll() is not None or self._stream_closed.is_set()):
            # Never block the sampling worker reading stderr from a live pipe.
            raise RuntimeError(self._friendly_error(self._error or "iOS 采集数据流已断开，请检查 USB 连接"))
        with self._lock:
            now = time.monotonic()
            observation_generation = self._observation_generation
            if self._last_sysmon_at is not None and now - self._last_sysmon_at >= self._watchdog_seconds:
                raise RuntimeError(f"iOS 已超过 {self._watchdog_seconds:g} 秒未收到系统采样数据，请检查 USB 连接或 Instruments 服务")
            proc, proc_times = self._fresh_snapshot("process", now, self._watchdog_seconds)
            system, system_times = self._fresh_snapshot("system", now, self._watchdog_seconds)
            graphics, graphics_times = self._fresh_snapshot("graphics", now, 5.0)
            # Battery diagnostics refresh at most every five seconds, or at
            # the configured interval when it is longer. Allow three updates.
            battery, battery_times = self._fresh_snapshot(
                "battery", now, max(5.0, self._sample_interval_ms / 1000) * 3)
            target_process_active = self._target_process_active
            target_missing_streak = self._target_missing_streak
            trace_rows = list(self._trace_frames)
            self._trace_frames.clear()
            trace_error = self._trace_error
        values: dict[str, float | None] = {}
        cpu = _number(proc, "cpuUsage", "cpuPercent")
        # Instruments reports percent of ONE core, including values above 100.
        # The shared app_cpu metric is percent of whole-device capacity.
        cpu_count = _number(system, "CPUCount", "EnabledCPUs")
        if cpu_count is None or cpu_count < 1:
            cpu_count = getattr(self, "_device_cpu_count", None) or 1.0
        counter_fps = _number(graphics, "framesPerSecond", "fps", "coreAnimationFramesPerSecond")
        graphics_valid = self._target_graphics_are_valid(
            counter_fps, cpu, target_process_active, target_missing_streak)
        self._update_graphics_status(graphics_valid)
        raw_frames = []
        if graphics_valid and not trace_error and self._enabled & TRACE_METRICS:
            if trace_rows and self._trace_anchor is None:
                self._trace_anchor = (trace_rows[-1][1]['source_time_s'], trace_rows[-1][0])
            for received, row in trace_rows:
                device_anchor, host_anchor = self._trace_anchor
                moment = elapsed + (row['source_time_s'] - device_anchor) + host_anchor - now
                if moment >= 0:
                    raw_frames.append({**row, 'x': moment, 'clock': 'device_mach_clock_host_receipt_anchor',
                                       'stream_epoch': str(row.get('stream_epoch', '')) + ':' + str(self._trace_epoch),
                                       'target_pid': _number(proc, 'pid'),
                                       'scope': 'device CoreAnimation, activity guarded; not PID-isolated'})
            raw_frames, jank_values = self._jank_tracker.annotate(raw_frames)
            measured = fps_from_intervals(raw_frames)
            if measured is not None:
                last = raw_frames[-1]
                self._frame_fps_observation = (measured, trace_rows[-1][0],
                    ('coreprofile', last['stream_epoch'], last['source_time_s']))
        else:
            self._jank_tracker.reset()
            self._trace_anchor = None
            self._frame_fps_observation = None
            jank_values = {key: None for key in JANK_METRICS}
        values.update({key: jank_values[key] for key in self._enabled & JANK_METRICS})

        fps_source = 'coreanimation_counter_fallback'
        fps_stamp = self._field_stamp(graphics, graphics_times, 'framesPerSecond','fps','coreAnimationFramesPerSecond')
        fps_identity = ('coreanimation', fps_stamp)
        raw_fps = counter_fps
        original_fps = raw_fps
        # CoreAnimation briefly reports 0/1 FPS while an Instruments stream is
        # attaching or resetting. It is not an observed one-second frame and
        # must not be converted into a fake 1000 ms frame-time spike.
        if raw_fps is not None and not 4 <= raw_fps <= 240: raw_fps = None
        frame_observation = self._frame_fps_observation
        if frame_observation and now - frame_observation[1] < max(5., self._sample_interval_ms / 1000 * 3):
            raw_fps, fps_stamp, fps_identity = frame_observation
            original_fps = raw_fps
            fps_source = 'coreprofile_frame_intervals'
        above_limit = bool({'fps', 'frame_time'} & self._enabled and raw_fps is not None
                           and self.fps_limit and raw_fps > self.fps_limit)
        source_token = (fps_identity, original_fps)
        if original_fps is not None and fps_stamp is not None and source_token != self._last_fps_source_token:
            self._last_fps_source_token = source_token
            self._fps_source_observations += 1
            self._fps_integer_observations += int(float(original_fps).is_integer())
            self._fps_above_limit += int(above_limit)
        fps_token = (fps_identity, raw_fps)
        if not graphics_valid or raw_fps is None or above_limit:
            fps = self._stabilize_fps(None)
            self._last_fps_observation = None
        elif fps_token != self._last_fps_observation:
            fps = self._stabilize_fps(raw_fps, observation_id=fps_identity)
            self._last_fps_observation, self._last_fps_result = fps_token, fps
        else:
            fps = self._last_fps_result
        if above_limit and graphics_valid:
            self._fps_limit_warning = True
            self._status = f"iOS 原始 FPS {original_fps:.2f} 超过所设上限 {self.fps_limit:g}；该帧率读数标为无效，原始值保留在审计中"
        elif self._fps_limit_warning:
            self._fps_limit_warning = False
            if graphics_valid:
                self._status = "正在采集 iOS 真机指标"
        if "fps" in self._enabled: values["fps"] = fps
        if "frame_time" in self._enabled: values["frame_time"] = 1000.0 / fps if fps and fps > 0 else None
        if "app_cpu" in self._enabled: values["app_cpu"] = cpu / cpu_count if cpu is not None else None
        if "total_cpu" in self._enabled:
            total = None
            for key, value in system.items():
                if not str(key).casefold().endswith("systemcpuusage.cpu_totalload"):
                    continue
                try:
                    candidate = float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(candidate):
                    total = candidate
                    break
            if total is not None:
                total /= cpu_count
            values["total_cpu"] = total
        footprint = _number(proc, "physFootprint", "physicalFootprint")
        rss = _number(proc, "memResidentSize", "residentSize")
        if "ios_physical_footprint" in self._enabled:
            values["ios_physical_footprint"] = footprint / 1024 / 1024 if footprint is not None else None
        if "app_rss" in self._enabled: values["app_rss"] = rss / 1024 / 1024 if rss is not None else None
        private = _number(proc, "memRPrvt")
        compressed = _number(proc, "memCompressed")
        virtual = _number(proc, "memVirtualSize")
        if "ios_private_memory" in self._enabled: values["ios_private_memory"] = private / 1048576 if private is not None else None
        if "ios_compressed_memory" in self._enabled: values["ios_compressed_memory"] = compressed / 1048576 if compressed is not None else None
        if "ios_virtual_memory" in self._enabled: values["ios_virtual_memory"] = virtual / 1048576 if virtual is not None else None
        if "app_threads" in self._enabled: values["app_threads"] = _number(proc, "threadCount", "numThreads")
        if "ios_mach_ports" in self._enabled: values["ios_mach_ports"] = _number(proc, "machPortCount")
        # Renderer is a separate pipeline counter, not a substitute for total GPU.
        gpu = _number(graphics, "deviceUtilization", "gpuUtilization", "gpuUsage")
        if gpu is not None and gpu <= 1: gpu *= 100
        if not graphics_valid: gpu = None
        if "gpu" in self._enabled: values["gpu"] = gpu if gpu is None else max(0.0, min(100.0, gpu))
        renderer = _number(graphics, "Renderer Utilization %")
        tiler = _number(graphics, "Tiler Utilization %")
        allocated_gpu = _number(graphics, "Alloc system memory")
        in_use_gpu = _number(graphics, "In use system memory")
        if not graphics_valid:
            renderer = tiler = allocated_gpu = in_use_gpu = None
        if "ios_gpu_renderer" in self._enabled: values["ios_gpu_renderer"] = renderer
        if "ios_gpu_tiler" in self._enabled: values["ios_gpu_tiler"] = tiler
        if "ios_gpu_allocated_memory" in self._enabled: values["ios_gpu_allocated_memory"] = allocated_gpu / 1048576 if allocated_gpu is not None else None
        if "ios_gpu_in_use_memory" in self._enabled: values["ios_gpu_in_use_memory"] = in_use_gpu / 1048576 if in_use_gpu is not None else None
        for core in range(32):
            key = f"cpu_core_{core}_usage"
            if key in self._enabled:
                raw_core = system.get(f"PerCPUUsage.{core}.CPU_TotalLoad")
                try: values[key] = float(raw_core) if raw_core is not None else None
                except (TypeError, ValueError): values[key] = None
        for metric, names, divisor in (
                ("disk_read", ("diskBytesRead", "bytesRead"), 1024.0),
                ("disk_write", ("diskBytesWritten", "bytesWritten"), 1024.0),
                ("ios_context_switches", ("ctxSwitch",), 1.0),
                ("ios_interrupt_wakeups", ("intWakeups",), 1.0)):
            if metric in self._enabled:
                values[metric] = self._counter_rate(metric, "process", proc, proc_times, *names, divisor=divisor)
        for metric, name in (("network_send", "netBytesOut"), ("network_recv", "netBytesIn")):
            if metric in self._enabled:
                values[metric] = self._counter_rate(metric, "system", system, system_times, name, divisor=1024.0)
        level = _number(battery, "CurrentCapacity", "BatteryCurrentCapacity")
        current = _number(battery, "InstantAmperage", "Amperage")
        voltage = _number(battery, "Voltage")
        temp = _number(battery, "Temperature")
        if temp is not None and temp > 200: temp /= 100
        if "battery_level" in self._enabled: values["battery_level"] = level
        if "battery_current" in self._enabled: values["battery_current"] = abs(current) if current is not None else None
        if "battery_voltage" in self._enabled: values["battery_voltage"] = voltage / 1000 if voltage and voltage > 20 else voltage
        if "temperature" in self._enabled: values["temperature"] = temp
        if "power" in self._enabled:
            values["power"] = abs(current) / 1000 * voltage / 1000 if current is not None and voltage is not None else None
        from .ios_sampling import PROCESS_FIELDS, SYSTEM_FIELDS
        sources = {key: ('process', tuple(fields)) for key, fields in PROCESS_FIELDS.items()}
        sources.update({key: ('system', tuple(fields)) for key, fields in SYSTEM_FIELDS.items()})
        sources.update({
            'app_cpu': ('process', ('cpuUsage','cpuPercent')),
            'ios_physical_footprint': ('process', ('physFootprint','physicalFootprint')),
            'app_rss': ('process', ('memResidentSize','residentSize')),
            'app_threads': ('process', ('threadCount','numThreads')),
            'fps': ('graphics', ('framesPerSecond','fps','coreAnimationFramesPerSecond')),
            'frame_time': ('graphics', ('framesPerSecond','fps','coreAnimationFramesPerSecond')),
            'gpu': ('graphics', ('deviceUtilization','gpuUtilization','gpuUsage')),
            'ios_gpu_renderer': ('graphics', ('Renderer Utilization %',)),
            'ios_gpu_tiler': ('graphics', ('Tiler Utilization %',)),
            'ios_gpu_allocated_memory': ('graphics', ('Alloc system memory',)),
            'ios_gpu_in_use_memory': ('graphics', ('In use system memory',)),
            'battery_level': ('battery', ('CurrentCapacity','BatteryCurrentCapacity')),
            'battery_current': ('battery', ('InstantAmperage','Amperage')),
            'battery_voltage': ('battery', ('Voltage',)), 'temperature': ('battery', ('Temperature',)),
        })
        snapshots = {'process': (proc,proc_times), 'system': (system,system_times),
                     'graphics': (graphics,graphics_times), 'battery': (battery,battery_times)}
        observed, held = {}, set()
        for key, value in values.items():
            if value is None:
                continue
            if key in {'fps', 'frame_time'}:
                stamp = fps_stamp
            elif key == 'power':
                stamps = [self._field_stamp(battery,battery_times,*names)
                          for names in [('InstantAmperage','Amperage'),('Voltage',)]]
                stamp = min(stamps) if all(s is not None for s in stamps) else None
            elif key == 'total_cpu':
                stamp = next((system_times[k] for k in system if str(k).casefold().endswith('systemcpuusage.cpu_totalload') and k in system_times), None)
            elif key.startswith('cpu_core_'):
                stamp = system_times.get(f"PerCPUUsage.{key.split('_')[2]}.CPU_TotalLoad")
            else:
                source, names = sources.get(key, ('process', (key,)))
                data, stamps = snapshots[source]
                stamp = self._field_stamp(data, stamps, *names)
            if stamp is not None:
                observed[key] = max(0., float(elapsed)-max(0., now-stamp))
                identity = fps_identity if key in {'fps', 'frame_time'} else stamp
                if self._last_observation.get(key) == identity:
                    held.add(key)
                self._last_observation[key] = identity
        with self._lock:
            if observation_generation != self._observation_generation:
                # Configuration/PID replacement overtook this snapshot. Never
                # publish pre-change values or restore their smoothing state.
                self._last_observation.clear()
                self._last_fps_observation = None
                self._last_fps_result = None
                self._fps_stabilizer.reset()
                self._jank_tracker.reset()
                self._frame_fps_observation = None
                return Sample(elapsed, {key: None for key in self._enabled})
            self._held_observations += len(held)
            for key in {'fps', 'frame_time'} & self._enabled:
                self._metric_sources[key] = fps_source
                if values.get(key) is not None and key not in held:
                    counts = self._metric_source_counts.setdefault(key, {})
                    counts[fps_source] = counts.get(fps_source, 0) + 1
        return self.audited_sample(elapsed, values, 'iOS Instruments / diagnostics',
            adjustments={key: 'exceeds_declared_fps_limit' for key in ('fps', 'frame_time')
                         if above_limit and graphics_valid and key in values},
            observed_elapsed=observed, held_keys=frozenset(held), frames=raw_frames,
            raw_values={key: value for key, value in
                {'fps': original_fps,
                 'frame_time': 1000 / original_fps if original_fps and original_fps > 0 else None,
                 'gpu': gpu}.items() if key in values},
            sources={key: ('iOS 1000 / adopted FPS; sampled mean, not raw per-frame timing; ' if key == 'frame_time' else '')
                + ('iOS CoreProfile frame intervals; 1000 * count / sum(frame_ms)' if fps_source == 'coreprofile_frame_intervals'
                   else 'iOS Instruments CoreAnimation FPS (counter fallback)')
                for key in ('fps', 'frame_time') if key in values})

    @staticmethod
    def _field_stamp(data, timestamps, *names):
        return next((timestamps[key] for key, value in data.items()
                     if key in timestamps and _number({key:value}, *names) is not None), None)

    def _target_graphics_are_valid(
            self, fps: float | None, cpu: float | None,
            process_active: bool | None, missing_streak: int) -> bool:
        """Decide whether device-wide graphics counters still describe the app.

        An explicit suspension or two missing process snapshots is conclusive.
        For the app-switcher transition, where the process may remain listed
        briefly, require two consecutive samples combining a large FPS uplift
        with a collapse in the target process's CPU.  The first candidate is
        still handled by ``FpsStabilizer``, so no false 60 FPS point escapes.
        """
        if process_active is False or missing_streak >= 2:
            self._graphics_mismatch_streak = 0
            return False

        fps_baseline = float(median(self._fps_history)) if len(self._fps_history) >= 3 else None
        cpu_baseline = (float(median(self._active_cpu_history))
                        if len(self._active_cpu_history) >= 4 else None)
        upward_jump = (fps is not None and fps_baseline is not None and
                       fps > fps_baseline + max(3.0, fps_baseline * .35))
        cpu_collapse = (cpu is not None and cpu_baseline is not None and cpu_baseline >= 10.0 and
                        cpu <= max(4.0, cpu_baseline * .18))
        suspicious = upward_jump and (missing_streak > 0 or cpu_collapse)
        if suspicious:
            self._graphics_mismatch_streak += 1
        else:
            self._graphics_mismatch_streak = 0
            if cpu is not None:
                self._active_cpu_history.append(cpu)
        return self._graphics_mismatch_streak < 2

    def _update_graphics_status(self, graphics_valid: bool) -> None:
        if not graphics_valid:
            self._graphics_suppressed = True
            self._status = "目标应用疑似已进入后台，已暂停 iOS 帧率和 GPU 统计"
        elif self._graphics_suppressed:
            self._graphics_suppressed = False
            self._status = "正在采集 iOS 真机指标"

    def _stabilize_fps(self, fps: float | None, *, observation_id=None) -> float | None:
        """Suppress isolated upward telemetry spikes without hiding FPS drops.

        CoreAnimation's one-second counter can occasionally overshoot a stable
        30/60 FPS regime. A clearly higher three-sample run, or a moderately
        higher six-sample run, establishes a genuine refresh-rate/cap change.
        """
        return self._fps_stabilizer.accept(fps, observation_id=observation_id)

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process and process.poll() is None:
            # Give the bridge time to stop its owned CoreProfile tap before
            # closing DVT. Never stop or take over another profiler's session.
            try:
                if getattr(process, 'stdin', None) is not None:
                    process.stdin.write('{"stop":true}\n')
                    process.stdin.flush()
                    process.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
        reader, self._reader = self._reader, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1)
        self._enabled = frozenset()
        self._target_process_active = None
        self._target_missing_streak = 0
        self._active_cpu_history.clear()
        self._graphics_mismatch_streak = 0
        self._graphics_suppressed = False
