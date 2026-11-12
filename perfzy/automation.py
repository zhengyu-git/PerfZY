from __future__ import annotations

import threading
import time
import json
import math
import re
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .capture.android import capture_android
from .capture.ios import capture_ios
from .capture.windows import capture_process_window
from .collectors.android import AndroidCollector
from .collectors.ios import IOSCollector
from .collectors.discovery import discover_all
from .collectors.targets import discover_targets
from .collectors.windows import WindowsCollector
from .domain.metrics import METRIC_BY_KEY
from .domain.models import Device, Platform, Target
from .domain.validation import metric_value_is_plausible, sample_points
from .domain.health import SamplingHealth
from .domain.collection_quality import collection_quality_notes
from .domain.provenance import metric_provenance
from .reporting import generate_local_report
from .runtime_paths import WORK_ROOT
from .storage.sessions import (METADATA_NAME, SUMMARY_NAME, LiveSessionWriter,
                               discard_incomplete_session, new_session_id,
                               normalize_session_name, save_session,
                               save_session_from_spool, load_session_records)


LIVE_MEMORY_POINTS = 7200


class PerfZYError(RuntimeError):
    """Raised when a local automation operation cannot be completed."""


@dataclass(frozen=True, slots=True)
class SessionResult:
    session_id: str
    name: str
    session_path: Path
    duration: float
    sample_count: int
    screenshot_count: int
    scenes: tuple[dict, ...]


class PerfZY:
    """Local Python API for unattended PerfZY collection and reporting."""

    DEFAULT_METRICS = frozenset({
        "fps", "frame_time", "jank", "big_jank", "small_jank", "frame_jank", "frame_big_jank",
        "app_cpu", "total_cpu", "memory", "network_send", "network_recv",
    })

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else WORK_ROOT
        self.devices: list[Device] = []
        self.discovery_errors: dict[str, str] = {}
        self.device: Device | None = None
        self.games: list[Target] = []
        self.game: Target | None = None
        self._collector: WindowsCollector | AndroidCollector | IOSCollector | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._stopped_elapsed: float | None = None
        self._collector_stopped = False
        self._started = 0.0
        self._started_at = ""
        self._session_id = ""
        self._sample_interval = 1.0
        self._screenshot_interval = 3.0
        self._screenshot_width = 1280
        self._capture_screenshots = True
        self._enabled_metrics: frozenset[str] = frozenset()
        self._requested_metrics: frozenset[str] = frozenset()
        self._metric_received_at: dict[str, float] = {}
        self._samples: dict[str, list[dict[str, float]]] = {}
        self._latest_metrics: dict[str, float | None] = {}
        self._screenshots: list[dict] = []
        self._scenes: list[dict] = []
        self._active_mark: dict | None = None
        self._worker_error: BaseException | None = None
        self._last_result: SessionResult | None = None
        self._session_spool: LiveSessionWriter | None = None
        self._sample_count_total = 0
        self._sampling_health = SamplingHealth()
        self._screenshot_executor: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="PerfZY-PythonAPI-Shot")
        self._screenshot_future: Future | None = None
        self.screenshot_window = None

    @property
    def collecting(self) -> bool:
        """Whether a session still needs stop(); use check_health() for failures."""
        return self._collector is not None

    def check_health(self) -> None:
        """Raise if sampling failed or is inactive; never stop or discard data.

        Call periodically from automation waits. Success only means the worker
        has not reported a failure, not that every metric has valid data.
        After an error, call stop() to finalize the existing samples.
        """
        error = self._worker_error
        if error is not None:
            raise PerfZYError(f"采样已中断：{error}；请调用 stop() 保存已有数据") from error
        if not self.collecting:
            raise PerfZYError("尚未开始采集或采集已结束")
        if self._stop_event.is_set():
            raise PerfZYError("采集正在停止，请调用 stop() 完成保存")

    @property
    def elapsed(self) -> float:
        if self._stopped_elapsed is not None:
            return self._stopped_elapsed
        return max(0.0, time.monotonic() - self._started) if self._started else 0.0

    def _freeze_elapsed(self) -> None:
        with self._lock:
            if self._stopped_elapsed is None:
                self._stopped_elapsed = self.elapsed

    def latest_metrics(self) -> dict[str, float | None]:
        """Copy the latest completed sample's enabled metrics without device I/O.

        Missing/nonfinite values are None, never carried forward. Before the
        first sample, enabled keys are None. Before start, returns {}. After
        stop, retains the final snapshot until the next successful start.
        Use check_health() and metric_freshness() to detect stopped updates.
        """
        with self._lock:
            return dict(self._latest_metrics)

    def metric_status(self) -> dict[str, list[str]]:
        """Return a detached snapshot for the current or most recent session.

        enabled means platform-supported, not guaranteed available on hardware.
        no_data means no finite sample received yet, not a zero value or proof
        of unsupported hardware. received does not guarantee fresh data.
        """
        with self._lock:
            received = {key for key in self._enabled_metrics if self._samples.get(key)}
            return {
                "requested": sorted(self._requested_metrics),
                "enabled": sorted(self._enabled_metrics),
                "unsupported": sorted(self._requested_metrics - self._enabled_metrics),
                "received": sorted(received),
                "no_data": sorted(self._enabled_metrics - received),
            }

    def metric_freshness(self, stale_after: float = 5.0) -> dict[str, dict]:
        """Receipt times for enabled metrics while a session awaits stop().

        last_received is seconds since start; age is seconds since receipt.
        Never-received metrics have None times and stale=False. Repeated valid
        values count as receipts; this cannot detect cached device counters.
        Raises outside a session so stopped data is not reported as stale.
        """
        try:
            threshold = float(stale_after)
        except (TypeError, ValueError) as error:
            raise PerfZYError("断更阈值必须是大于 0 的有限秒数") from error
        if not math.isfinite(threshold) or threshold <= 0:
            raise PerfZYError("断更阈值必须是大于 0 的有限秒数")
        if not self.collecting:
            raise PerfZYError("尚未开始采集或采集已结束")
        with self._lock:
            now = time.monotonic()
            result = {}
            for key in sorted(self._enabled_metrics):
                received_at = self._metric_received_at.get(key)
                age = max(0.0, now - received_at) if received_at is not None else None
                result[key] = {
                    "last_received": max(0.0, received_at - self._started) if received_at is not None else None,
                    "age": age,
                    "stale": age is not None and age >= threshold,
                }
            return result

    def refresh_devices(self) -> list[Device]:
        if self.collecting:
            raise PerfZYError("采集中不能刷新设备")
        self.discovery_errors = {}
        self.devices = discover_all(errors=self.discovery_errors)
        return list(self.devices)

    def connect_device(self, query: str | None = None, *, platform: str | Platform | None = None) -> Device:
        """Select a connected device by id, serial, name, model, or platform."""
        devices = self.refresh_devices()
        wanted_platform = Platform(platform) if platform else None
        candidates = [item for item in devices if wanted_platform is None or item.platform is wanted_platform]
        if query:
            needle = str(query).casefold()
            candidates = [item for item in candidates if needle in " ".join(
                (item.id, item.name, item.model, item.os_version)).casefold()]
        elif wanted_platform is None:
            candidates.sort(key=lambda item: item.platform is not Platform.ANDROID)
        if not candidates:
            if wanted_platform is Platform.IOS and "ios" in self.discovery_errors:
                raise PerfZYError(self.discovery_errors["ios"])
            description = query or (wanted_platform.value if wanted_platform else "可用设备")
            hint = "请检查 USB 连接及设备信任状态" if wanted_platform is Platform.IOS else "请检查设备连接"
            raise PerfZYError(f"没有找到设备：{description}。{hint}后重试")
        self.device = candidates[0]
        self.games = []
        self.game = None
        self.list_games()
        return self.device

    def list_games(self, refresh: bool = True) -> list[Target]:
        if self.device is None:
            raise PerfZYError("尚未连接设备，请先调用 connect_device()")
        if refresh:
            try:
                self.games = discover_targets(self.device, strict=True)
            except RuntimeError as error:
                self.games = []
                raise PerfZYError(f"读取应用列表失败：{error}") from error
        return list(self.games)

    def select_game(self, query: str) -> Target:
        """Select an app/game by package, id, display name, or executable name."""
        if self.device is None:
            raise PerfZYError("尚未连接设备，请先调用 connect_device()")
        if self.collecting:
            raise PerfZYError("采集中或等待保存时不能切换游戏，请先完成 stop()")
        games = self.list_games(refresh=True)
        needle = str(query).casefold().strip()
        exact = [item for item in games if needle in {item.id.casefold(), item.name.casefold()} or item.id.casefold().endswith(":" + needle)]
        matches = exact or [item for item in games if needle in f"{item.id} {item.name}".casefold()]
        if not matches:
            raise PerfZYError(f"没有找到应用：{query}。请先启动游戏，再刷新应用列表")
        self.game = matches[0]
        return self.game

    def start(
        self,
        metrics: Iterable[str] | None = None,
        *,
        sample_interval: float = 1.0,
        screenshots: bool = True,
        screenshot_interval: float = 3.0,
        screenshot_width: int = 1280,
        build_label: str = "",
        conditions: str = "",
        ios_fps_limit: float | None = None,
    ) -> str:
        if self.collecting:
            raise PerfZYError("采集已经开始")
        if self.device is None or self.game is None:
            raise PerfZYError("请先连接设备并选择游戏")
        try:
            metric_interval = float(sample_interval)
            shot_interval = float(screenshot_interval)
            shot_width = int(screenshot_width)
        except (TypeError, ValueError) as error:
            raise PerfZYError(f"采样参数无效：{error}") from error
        if not math.isfinite(metric_interval) or metric_interval < 0.5:
            raise PerfZYError("性能指标采样周期必须是不小于 0.5 秒的有限数值")
        if not math.isfinite(shot_interval) or shot_interval < 0.5:
            raise PerfZYError("截图采样周期必须是不小于 0.5 秒的有限数值")
        if shot_width < 0 or shot_width > 7680:
            raise PerfZYError("截图宽度必须在 0–7680 像素之间，0 表示原始分辨率")
        requested = set(self.DEFAULT_METRICS if metrics is None else metrics)
        unknown = sorted(requested - METRIC_BY_KEY.keys())
        if unknown:
            raise PerfZYError(f"未知指标：{', '.join(unknown)}")
        enabled = frozenset(key for key in requested if self.device.platform in METRIC_BY_KEY[key].platforms)
        if not enabled:
            raise PerfZYError("所选指标在当前设备平台均不可采集")
        if self.device.platform is Platform.WINDOWS:
            collector = WindowsCollector()
        elif self.device.platform is Platform.ANDROID:
            collector = AndroidCollector()
        else:
            collector = IOSCollector()
            collector.set_sample_interval_ms(int(metric_interval * 1000))
            if ios_fps_limit is not None:
                try:
                    collector.set_fps_limit(ios_fps_limit)
                except (TypeError, ValueError) as error:
                    raise PerfZYError(str(error)) from error
        try:
            collector.start(self.device, self.game, enabled)
        except Exception as error:
            try:
                collector.stop()
            except Exception:
                pass
            raise PerfZYError(f"无法开始采集：{error}") from error
        self._collector = collector
        self._sample_interval = metric_interval
        self._sampling_health = SamplingHealth(metric_interval)
        self._capture_screenshots = bool(screenshots)
        self._screenshot_interval = shot_interval
        self._screenshot_width = shot_width
        self._session_id = new_session_id()
        self._started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        device_payload = {
            "id": self.device.id, "name": self.device.name,
            "platform": self.device.platform.value, "model": self.device.model,
            "os": self.device.os_version, **dict(self.device.details),
        }
        target_payload = {"id": self.game.id, "name": self.game.name,
                          "pid": self.game.process_id or 0, "icon": self.game.icon_path}
        try:
            self._session_spool = LiveSessionWriter(self.root, self._session_id,sample_interval=metric_interval, manifest={
                "name": "", "started_at": self._started_at,
                "device": device_payload, "target": target_payload,
                "enabled_metrics": sorted(enabled), "scenes": [],
                "capture_settings": dict(requested_metrics=sorted(enabled), sample_interval_ms=metric_interval * 1000,
                    screenshots=bool(screenshots), screenshot_interval_ms=shot_interval * 1000,
                    screenshot_max_width=shot_width, collector_revision="merged-quality-v3",
                    ios_fps_limit=getattr(collector, 'fps_limit', 0) if self.device.platform is Platform.IOS else None,
                    build_label=str(build_label).strip()[:120], conditions=str(conditions).strip()[:300]),
            })
        except (OSError, ValueError) as error:
            try:
                collector.stop()
            except Exception:
                pass
            self._collector = None
            raise PerfZYError(f"无法创建增量采集文件：{error}") from error
        self._stopped_elapsed = None
        self._collector_stopped = False
        self._started = time.monotonic()
        with self._lock:
            self._requested_metrics = frozenset(requested)
            self._enabled_metrics = enabled
            self._samples = {key: [] for key in enabled}
            self._latest_metrics = {key: None for key in sorted(enabled)}
            self._metric_received_at = {}
        self._screenshots = []
        self._scenes = []
        self._active_mark = None
        self._worker_error = None
        self._last_result = None
        self._sample_count_total = 0
        self._screenshot_future = None
        if self._screenshot_executor is None:
            self._screenshot_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="PerfZY-PythonAPI-Shot")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._collect_loop, name="PerfZY-PythonAPI", daemon=True)
        try:
            self._thread.start()
        except Exception as error:
            # Interpreter shutdown, resource exhaustion, or host restrictions
            # can reject a new thread. Leave neither native collectors nor an
            # empty capture that startup recovery would mistake for user data.
            self._thread = None
            spool, self._session_spool = self._session_spool, None
            if spool is not None:
                try:
                    spool.close()
                except Exception:
                    pass
            try:
                discard_incomplete_session(self.root, self._session_id)
            except Exception:
                pass
            active_collector, self._collector = self._collector, None
            if active_collector is not None:
                try:
                    active_collector.stop()
                except Exception:
                    pass
            raise PerfZYError(f"无法启动采样线程：{error}") from error
        return self._session_id

    def _collect_loop(self) -> None:
        next_sample = 0.0
        next_screenshot = self._screenshot_interval
        try:
            while not self._stop_event.is_set():
                elapsed = self.elapsed
                if elapsed >= next_sample:
                    sample_started = time.perf_counter()
                    sample = self._collector.sample(elapsed) if self._collector else None
                    if sample:
                        with self._lock:
                            latest = {key: None for key in sorted(self._enabled_metrics)}
                            for key in getattr(sample, 'held_keys', ()):
                                if key in latest and metric_value_is_plausible(key, sample.values.get(key)):
                                    latest[key] = sample.values[key]
                            valid_values = {}
                            points = sample_points(sample, self._enabled_metrics)
                            for key, point in points.items():
                                latest[key] = point['y']
                                valid_values[key] = point['y']
                                rows = self._samples.setdefault(key, [])
                                self._metric_received_at[key] = time.monotonic()
                                rows.append(point)
                                if len(rows) > LIVE_MEMORY_POINTS:
                                    del rows[:len(rows) - LIVE_MEMORY_POINTS]
                            if self._session_spool is not None:
                                self._session_spool.append(sample.elapsed, dict(sample.values),
                                    observed_elapsed=getattr(sample, 'observed_elapsed', {}),
                                    frame_events=getattr(sample, 'frames', ()),
                                    raw_values=getattr(sample, 'raw_values', {}),
                                    adjustments=getattr(sample, 'adjustments', {}),
                                    sources=getattr(sample, 'sources', {}),
                                    requested=self._enabled_metrics,
                                    collector_ms=(time.perf_counter()-sample_started)*1000,
                                    held_keys=getattr(sample, 'held_keys', ()))
                            self._sampling_health.observe(elapsed, points,
                                (time.perf_counter() - sample_started) * 1000,
                                self._enabled_metrics, held=getattr(sample, 'held_keys', ()))
                            self._latest_metrics = latest
                            self._sample_count_total += 1
                    next_sample += self._sample_interval
                    if next_sample <= elapsed:
                        next_sample = elapsed + self._sample_interval
                if self._capture_screenshots and elapsed >= next_screenshot:
                    if self._screenshot_future is None or self._screenshot_future.done():
                        executor = self._screenshot_executor
                        if executor is not None:
                            self._screenshot_future = executor.submit(
                                self._capture_one, elapsed)
                            # Measure the next deadline from the actual capture
                            # start. If a platform capture is still busy when
                            # that deadline arrives, leave it overdue so the
                            # next capture starts as soon as the worker frees.
                            # Advancing the deadline while busy rounded a slow
                            # 0.7 s capture up to 1.0/1.5 s timer multiples.
                            next_screenshot = elapsed + self._screenshot_interval
                now = self.elapsed
                screenshot_wake = next_screenshot
                if (self._capture_screenshots and
                        self._screenshot_future is not None and
                        not self._screenshot_future.done() and
                        next_screenshot <= now):
                    # Poll an overdue capture at a modest rate without letting
                    # it hold up the independently scheduled metric sample.
                    screenshot_wake = now + 0.05
                wait_for = max(
                    0.01,
                    min(next_sample,
                        screenshot_wake if self._capture_screenshots else next_sample) - now)
                self._stop_event.wait(min(wait_for, 0.2))
        except Exception as error:
            self._worker_error = error
            self._freeze_elapsed()
            self._stop_event.set()

    def diagnostics(self) -> dict:
        """Bounded in-memory collection health and metric provenance."""
        with self._lock:
            health = self._sampling_health.snapshot(self.elapsed)
            collector = self._collector
            details = getattr(collector, 'diagnostics', None)
            health['collector'] = details() if callable(details) else {}
            health['measurement_notes'] = collection_quality_notes(health['collector'])
            platform = self.device.platform if self.device else ''
            health['provenance'] = metric_provenance(platform, self._enabled_metrics,
                process_scope=health['collector'].get('process_scope'), collector_details=health['collector'])
            return health

    def _capture_one(self, elapsed: float) -> None:
        if self.device is None or self.game is None:
            return
        destination = self.root / "data" / "sessions" / self._session_id / "screenshots" / f"{int(elapsed * 1000):09d}.jpg"
        if self.device.platform is Platform.ANDROID:
            path = capture_android(self.device.id.partition(":")[2], destination, self._screenshot_width)
        elif self.device.platform is Platform.IOS:
            path = capture_ios(self.device.id.partition(":")[2], destination, self._screenshot_width)
        else:
            window = self.screenshot_window
            if window:
                if window.get('create_time') is None:return
                from .capture.windows import describe_window
                actual = describe_window(window['hwnd'], expected_pid=window['pid'])
                if not actual or actual.get('create_time') != window.get('create_time'):
                    return
                path = capture_process_window(int(window['pid']), destination, self._screenshot_width,
                                              window_handle=int(window['hwnd']))
                after = describe_window(window['hwnd'], expected_pid=window['pid'])
                if path and (not after or after.get('create_time') != window.get('create_time')):
                    Path(path).unlink(missing_ok=True)
                    return
            else:
                path = capture_process_window(int(self.game.process_id or 0), destination, self._screenshot_width)
        if path:
            minutes, seconds = divmod(int(elapsed), 60)
            with self._lock:
                self._screenshots.append({"image": Path(path).as_uri(), "time": f"{minutes:02d}:{seconds:02d}", "seconds": elapsed})

    def start_mark(self, name: str = "", color: str = "#4269d4") -> float:
        if not self.collecting:
            raise PerfZYError("尚未开始采集")
        if self._active_mark is not None:
            raise PerfZYError("已有一个标记正在进行，请先调用 end_mark()")
        self._active_mark = {"name": name.strip(), "color": color, "start": self.elapsed}
        return float(self._active_mark["start"])

    def end_mark(self, name: str | None = None, color: str | None = None) -> dict:
        if self._active_mark is None:
            raise PerfZYError("当前没有正在进行的标记")
        scene = dict(self._active_mark)
        scene["end"] = self.elapsed
        scene["name"] = (name if name is not None else scene["name"]).strip() or f"标记 {len(self._scenes) + 1}"
        if color is not None:
            scene["color"] = color
        scene["id"] = f"scene-{len(self._scenes) + 1}"
        self._scenes.append(scene)
        self._active_mark = None
        return dict(scene)

    def stop(self, name: str = "") -> SessionResult:
        if self._collector is None:
            if self._last_result:
                return self._last_result
            raise PerfZYError("尚未开始采集")
        self._freeze_elapsed()
        duration = self.elapsed
        if self._active_mark is not None:
            self.end_mark()
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=60)
            if self._thread.is_alive():
                raise PerfZYError("采样线程未在60秒内停止，已保留增量数据，请检查设备连接后重试 stop()")
        if self._screenshot_future is not None:
            try:
                self._screenshot_future.result(timeout=35)
            except FutureTimeoutError as error:
                raise PerfZYError("截图线程未能安全停止，增量数据已保留，请稍后重试 stop()") from error
            except Exception:
                # A screenshot is optional. Its failure must not prevent the
                # already-spooled performance samples from being finalized.
                pass
        screenshot_executor, self._screenshot_executor = self._screenshot_executor, None
        if screenshot_executor is not None:
            screenshot_executor.shutdown(wait=True, cancel_futures=True)
        self._screenshot_future = None
        # Keep the pending session attached until serialization succeeds.
        # Retrying stop() must not stop native resources twice.
        if not self._collector_stopped:
            try:
                self._collector.stop()
            except Exception:
                pass
            finally:
                self._collector_stopped = True
        if self.device is None or self.game is None:
            raise PerfZYError("采集状态不完整，无法保存会话")
        with self._lock:
            samples = {key: list(rows) for key, rows in self._samples.items() if rows}
            screenshots = list(self._screenshots)
            scenes = list(self._scenes)
        device = {
            "id": self.device.id, "name": self.device.name, "platform": self.device.platform.value,
            "model": self.device.model, "os": self.device.os_version, **dict(self.device.details),
        }
        target = {"id": self.game.id, "name": self.game.name, "pid": self.game.process_id or 0, "icon": self.game.icon_path}
        session_name = normalize_session_name(name)
        spool = self._session_spool
        try:
            sample_files = spool.close() if spool is not None else {}
            path = (save_session_from_spool(
                self.root, device, target, sample_files, screenshots,
                set(self._enabled_metrics), self._session_id, scenes,
                self._started_at, duration, session_name,
                telemetry=self.diagnostics(),
                end_reason=f"采样中断：{self._worker_error}" if self._worker_error else "脚本调用 stop() 结束采集")
                    if sample_files else
                    save_session(self.root, device, target, samples, screenshots,
                                 set(self._enabled_metrics), self._session_id, scenes,
                                 self._started_at, duration, session_name,
                                 telemetry=self.diagnostics(),
                                 end_reason=f"采样中断：{self._worker_error}" if self._worker_error else "脚本调用 stop() 结束采集"))
        except (OSError, RuntimeError, ValueError) as error:
            # Keep the live manifest/spool intact so GUI startup recovery can
            # finalize it later instead of losing an unattended run.
            raise PerfZYError(f"保存采集记录失败，增量数据已保留：{error}") from error
        result = SessionResult(self._session_id, session_name, path, duration, self._sample_count_total,
                               len(screenshots), tuple(scenes))
        self._last_result = result
        self._collector = None
        self._session_spool = None
        if self._worker_error:
            raise PerfZYError(f"采样中断，但已保存现有数据：{path}；原因：{self._worker_error}") from self._worker_error
        return result

    def _resolve_session(self, session: SessionResult | str | Path | None) -> tuple[Path, str]:
        if isinstance(session, SessionResult):
            session_path = session.session_path
            session_name = session.name
        elif isinstance(session, Path):
            session_path = session
            session_name = ""
        elif isinstance(session, str):
            candidate = Path(session)
            if candidate.suffix == ".json":
                session_path = candidate
            elif not re.fullmatch(r"[A-Za-z0-9_-]+", session):
                raise PerfZYError("采集记录编号无效；自定义 JSON 路径请使用 pathlib.Path")
            else:
                session_path = self.root / "data" / "sessions" / session / "session.json"
            session_name = ""
        elif self._last_result:
            session_path = self._last_result.session_path
            session_name = self._last_result.name
        else:
            raise PerfZYError("没有可生成报告的会话，请先调用 stop()")
        return session_path, session_name

    def list_records(self) -> list[dict]:
        """Saved records in this SDK root, newest creation/merge first."""
        try:
            return load_session_records(self.root)
        except (OSError, ValueError) as error:
            raise PerfZYError(f"读取采集记录失败：{error}") from error

    def merge_records(self, session_ids: Iterable[str], name: str = "") -> Path:
        """Merge saved same-platform records in exactly the supplied order."""
        if self.collecting:
            raise PerfZYError("请先停止采集，再合并记录")
        if isinstance(session_ids, (str, bytes)):
            raise PerfZYError("请传入至少两个采集记录编号组成的列表")
        from .storage.merging import merge_sessions
        try:
            return merge_sessions(self.root, list(session_ids), name)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            raise PerfZYError(f"记录合并失败：{error}") from error

    def generate_html(self, session: SessionResult | str | Path | None = None, *, screenshot_mode: str = "compressed") -> Path:
        if self.collecting:
            raise PerfZYError("请先停止采集，再生成 HTML 报告")
        session_path, session_name = self._resolve_session(session)
        try:
            if not session_name:
                for sidecar_name in (METADATA_NAME, SUMMARY_NAME):
                    sidecar = session_path.parent / sidecar_name
                    if not sidecar.is_file():
                        continue
                    try:
                        sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
                        session_name = (str(sidecar_payload.get("name") or "")
                                        if isinstance(sidecar_payload, dict) else "")
                    except (OSError, TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if session_name:
                        break
            report_name = self._safe_report_name(session_name) if session_name else ""
            return generate_local_report(session_path, self.root / "exports", report_name, screenshot_mode=screenshot_mode)
        except Exception as error:
            raise PerfZYError(f"HTML 报告生成失败：{error}") from error

    @staticmethod
    def _safe_report_name(name: str) -> str:
        invalid = '<>:"/\\|?*'
        cleaned = "".join("_" if character in invalid or ord(character) < 32 else character for character in name)
        cleaned = cleaned.strip().rstrip(".")
        return cleaned or "PerfZY-性能测试报告"

    def close(self) -> None:
        if self._collector is not None:
            self.stop()
        screenshot_executor, self._screenshot_executor = self._screenshot_executor, None
        if screenshot_executor is not None:
            screenshot_executor.shutdown(wait=True, cancel_futures=True)

    def __enter__(self) -> "PerfZY":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise
