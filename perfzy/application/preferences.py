from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

from ..domain.metrics import METRIC_BY_KEY
from ..domain.models import Platform
from .charts import CHART_GROUP_BY_KEY, DEFAULT_CHART_ORDER, DEFAULT_VISIBLE_CHARTS, WINDOWS_VISIBLE_CHARTS, IOS_VISIBLE_CHARTS, ANDROID_VISIBLE_CHARTS


DEFAULT_ENABLED_METRICS: frozenset[str] = frozenset(
    {"fps", "app_cpu", "total_cpu", "network_send", "network_recv"})

WINDOWS_APP_DEFAULT_METRICS = frozenset({"fps", "frame_time", "app_cpu", "memory",
                                         "private_memory", "app_gpu_usage", "gpu_memory"})
WINDOWS_NEW_DEFAULT_METRICS = frozenset({"system_memory_used", "system_gpu_usage",
                                         "system_gpu_dedicated_memory", "system_gpu_shared_memory"})
WINDOWS_DEFAULT_METRICS = WINDOWS_APP_DEFAULT_METRICS | WINDOWS_NEW_DEFAULT_METRICS | {"total_cpu", "available_memory"}
WINDOWS_DEVICE_DEFAULT_CHARTS = frozenset({"system_cpu", "system_memory", "system_gpu", "system_gpu_memory"})
WINDOWS_DEFAULT_VISIBLE_CHARTS = WINDOWS_VISIBLE_CHARTS | WINDOWS_DEVICE_DEFAULT_CHARTS
WINDOWS_LAYOUT_MIGRATION = "migrations/windows_layout_v3"
WINDOWS_DEVICE_METRICS_MIGRATION = "migrations/windows_device_metrics_v1"
IOS_DEFAULT_METRICS = frozenset({"fps", "app_cpu", "ios_physical_footprint", "gpu"})
IOS_LAYOUT_MIGRATION = "migrations/ios_layout_v1"
ANDROID_DEFAULT_METRICS = frozenset({"fps", "app_cpu", "memory", "gpu"})
ANDROID_LAYOUT_MIGRATION = "migrations/android_layout_v1"

MINIMUM_SAMPLE_INTERVAL_MS = 500
MINIMUM_SCREENSHOT_INTERVAL_MS = 500
MAXIMUM_SAMPLE_INTERVAL_MS = 5000
MAXIMUM_SCREENSHOT_INTERVAL_MS = 10000
VALID_SCREENSHOT_WIDTHS = frozenset({0, 854, 1280, 1920})


def _string_list(value: object) -> list[str]:
    """QSettings collapses a one-element string list back to a bare string."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [str(item) for item in value]


class Preferences:
    """Persisted workspace preferences.

    Metric selection is tracked separately for each platform: turning FPS off
    while testing a PC build must not silently turn it off for Android too.
    """

    def __init__(self, settings_path: Path) -> None:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._settings = QSettings(str(settings_path), QSettings.IniFormat)
        self._enabled: dict[Platform, set[str]] = {}
        self.chart_order: list[str] = list(DEFAULT_CHART_ORDER)
        self.visible_charts: set[str] = set(DEFAULT_VISIBLE_CHARTS)
        self.windows_visible_charts: set[str] = set(WINDOWS_DEFAULT_VISIBLE_CHARTS)
        self.ios_visible_charts: set[str] = set(IOS_VISIBLE_CHARTS)
        self.screenshot_enabled = True
        self.sample_interval_ms = 1000
        self.screenshot_interval_ms = 3000
        self.screenshot_max_width = 1280
        self.report_screenshot_mode = "compressed"
        self.windows_resource_scope = 'selected'
        self.scene_presets = ['登录', '大世界', '战斗', '传送']
        self.load()

    @staticmethod
    def _platform_key(platform: Platform) -> str:
        return platform.name.lower()

    @staticmethod
    def _defaults_for(platform: Platform) -> set[str]:
        if platform is Platform.WINDOWS:
            return set(WINDOWS_DEFAULT_METRICS)
        if platform is Platform.IOS:
            return set(IOS_DEFAULT_METRICS)
        if platform is Platform.ANDROID:
            return set(ANDROID_DEFAULT_METRICS)
        return {key for key in DEFAULT_ENABLED_METRICS
                if platform in METRIC_BY_KEY[key].platforms}

    def load(self) -> None:
        valid_metrics = set(METRIC_BY_KEY)
        for platform in Platform:
            key = f"metrics/{self._platform_key(platform)}/enabled"
            # A still-open older app may save its former bulk selection on exit.
            # Keep compact selection independent so that cannot undo this upgrade.
            compact_key = f"metrics/{self._platform_key(platform)}/compact_enabled"
            if platform in {Platform.WINDOWS, Platform.IOS, Platform.ANDROID} and self._settings.contains(compact_key):
                key = compact_key
            # Deliberately turning every metric off is a valid saved state, so
            # presence of the key decides, not whether its value is empty.
            if not self._settings.contains(key):
                self._enabled[platform] = self._defaults_for(platform)
            else:
                self._enabled[platform] = {name for name in _string_list(self._settings.value(key))
                                           if name in valid_metrics}

        # Retain the former Windows layout as recoverable settings, then
        # apply the compact layout once.
        if not self._settings.value(WINDOWS_LAYOUT_MIGRATION, False, type=bool):
            previous = self._enabled[Platform.WINDOWS]
            if self._settings.contains("metrics/windows/enabled"):
                self._settings.setValue("migrations/windows_layout_v3_previous_enabled", sorted(previous))
                self._settings.setValue("migrations/windows_layout_v3_previous_visible",
                                        self._settings.value("charts/visible", []))
                if previous:  # An explicitly empty selection stays empty.
                    self._enabled[Platform.WINDOWS] = set(WINDOWS_DEFAULT_METRICS)
            self._settings.setValue("metrics/windows/enabled", sorted(self._enabled[Platform.WINDOWS]))
            self._settings.setValue("metrics/windows/compact_enabled", sorted(self._enabled[Platform.WINDOWS]))
            self._settings.setValue("charts/windows_visible", sorted(WINDOWS_DEFAULT_VISIBLE_CHARTS))
            self._settings.setValue(WINDOWS_LAYOUT_MIGRATION, True)
            self._settings.sync()
        if not self._settings.contains("metrics/windows/compact_enabled"):
            self._settings.setValue("metrics/windows/compact_enabled", sorted(self._enabled[Platform.WINDOWS]))

        if not self._settings.value(IOS_LAYOUT_MIGRATION, False, type=bool):
            previous = self._enabled[Platform.IOS]
            if self._settings.contains("metrics/ios/enabled"):
                self._settings.setValue("migrations/ios_layout_v1_previous_enabled", sorted(previous))
                self._settings.setValue("migrations/ios_layout_v1_previous_visible", self._settings.value("charts/visible", []))
                if previous:
                    self._enabled[Platform.IOS] = set(IOS_DEFAULT_METRICS)
            self._settings.setValue("metrics/ios/enabled", sorted(self._enabled[Platform.IOS]))
            self._settings.setValue("metrics/ios/compact_enabled", sorted(self._enabled[Platform.IOS]))
            self._settings.setValue("charts/ios_visible", sorted(IOS_VISIBLE_CHARTS))
            self._settings.setValue(IOS_LAYOUT_MIGRATION, True)
            self._settings.sync()

        if not self._settings.value(ANDROID_LAYOUT_MIGRATION, False, type=bool):
            previous = self._enabled[Platform.ANDROID]
            if self._settings.contains("metrics/android/enabled"):
                self._settings.setValue("migrations/android_layout_v1_previous_enabled", sorted(previous))
                self._settings.setValue("migrations/android_layout_v1_previous_visible", self._settings.value("charts/visible", []))
                if previous:
                    self._enabled[Platform.ANDROID] = set(ANDROID_DEFAULT_METRICS)
            self._settings.setValue("metrics/android/enabled", sorted(self._enabled[Platform.ANDROID]))
            self._settings.setValue("metrics/android/compact_enabled", sorted(self._enabled[Platform.ANDROID]))
            self._settings.setValue("charts/android_visible", sorted(ANDROID_VISIBLE_CHARTS))
            self._settings.setValue(ANDROID_LAYOUT_MIGRATION, True)
            self._settings.sync()

        stored_order = [key for key in _string_list(self._settings.value("charts/order"))
                        if key in CHART_GROUP_BY_KEY]
        self.chart_order = stored_order + [key for key in DEFAULT_CHART_ORDER
                                           if key not in stored_order]
        # visible_charts is the Android workspace; keep its canonical setting
        # independent from older app versions that still write charts/visible.
        stored_visible = self._settings.value("charts/android_visible", self._settings.value("charts/visible"))
        if stored_visible is not None:
            self.visible_charts = {key for key in _string_list(stored_visible)
                                   if key in CHART_GROUP_BY_KEY}
        self.windows_visible_charts = {key for key in _string_list(
            self._settings.value("charts/windows_visible", sorted(WINDOWS_DEFAULT_VISIBLE_CHARTS)))
            if key in CHART_GROUP_BY_KEY}
        if not self._settings.value(WINDOWS_DEVICE_METRICS_MIGRATION, False, type=bool):
            enabled = self._enabled[Platform.WINDOWS]
            # Add newly available counters without reviving disabled existing
            # metrics or an intentionally empty workspace.
            if enabled and self.windows_visible_charts:
                if enabled == WINDOWS_APP_DEFAULT_METRICS:
                    enabled.update(WINDOWS_DEFAULT_METRICS)
                else:
                    enabled.update(WINDOWS_NEW_DEFAULT_METRICS)
                self.windows_visible_charts.update(WINDOWS_DEVICE_DEFAULT_CHARTS - {"system_cpu"})
                if "total_cpu" in enabled:
                    self.windows_visible_charts.add("system_cpu")
                self._settings.setValue("metrics/windows/enabled", sorted(enabled))
                self._settings.setValue("metrics/windows/compact_enabled", sorted(enabled))
                self._settings.setValue("charts/windows_visible", sorted(self.windows_visible_charts))
            self._settings.setValue(WINDOWS_DEVICE_METRICS_MIGRATION, True)
            self._settings.sync()
        self.ios_visible_charts = {key for key in _string_list(
            self._settings.value("charts/ios_visible", sorted(IOS_VISIBLE_CHARTS))) if key in CHART_GROUP_BY_KEY}

        self.screenshot_enabled = self._settings.value("sampling/screenshots", True, type=bool)
        self.sample_interval_ms = self.set_sample_interval_ms(
            self._settings.value("sampling/metric_ms", 1000, type=int))
        self.screenshot_interval_ms = self.set_screenshot_interval_ms(
            self._settings.value("sampling/screenshot_ms", 3000, type=int))
        stored_width = self._settings.value("sampling/screenshot_width", 1280, type=int)
        self.screenshot_max_width = (stored_width if stored_width in VALID_SCREENSHOT_WIDTHS
                                     else 1280)
        mode = self._settings.value("report/screenshot_mode", "compressed")
        self.report_screenshot_mode = mode if mode in {"none", "compressed", "original"} else "compressed"
        scope = self._settings.value('windows/resource_scope', 'selected')
        self.windows_resource_scope = scope if scope in {'selected', 'tree'} else 'selected'
        if self._settings.contains('scenes/presets'):
            self.scene_presets = self.clean_scene_presets(_string_list(self._settings.value('scenes/presets')))

    def save(self) -> None:
        for platform, keys in self._enabled.items():
            self._settings.setValue(f"metrics/{self._platform_key(platform)}/enabled",
                                    sorted(keys))
        self._settings.setValue("metrics/windows/compact_enabled", sorted(self._enabled[Platform.WINDOWS]))
        self._settings.setValue("metrics/ios/compact_enabled", sorted(self._enabled[Platform.IOS]))
        self._settings.setValue("metrics/android/compact_enabled", sorted(self._enabled[Platform.ANDROID]))
        self._settings.setValue("charts/order", self.chart_order)
        self._settings.setValue("charts/visible", sorted(self.visible_charts))
        self._settings.setValue("charts/android_visible", sorted(self.visible_charts))
        self._settings.setValue("charts/windows_visible", sorted(self.windows_visible_charts))
        self._settings.setValue("charts/ios_visible", sorted(self.ios_visible_charts))
        self._settings.setValue("sampling/screenshots", self.screenshot_enabled)
        self._settings.setValue("sampling/metric_ms", self.sample_interval_ms)
        self._settings.setValue("sampling/screenshot_ms", self.screenshot_interval_ms)
        self._settings.setValue("sampling/screenshot_width", self.screenshot_max_width)
        self._settings.setValue("report/screenshot_mode", self.report_screenshot_mode)
        self._settings.setValue('windows/resource_scope', self.windows_resource_scope)
        self._settings.setValue('scenes/presets', self.scene_presets)
        self._settings.sync()

    @staticmethod
    def clean_scene_presets(values):
        names = []
        for value in values:
            name = str(value).strip()[:40]
            if name and name not in names:
                names.append(name)
        return names[:12]

    def remember_scene_preset(self, name):
        cleaned = self.clean_scene_presets([name])
        if cleaned:
            self.scene_presets = self.clean_scene_presets(cleaned + self.scene_presets)

    def enabled_for(self, platform: Platform) -> set[str]:
        """Live metric selection for one platform; callers may mutate it."""
        return self._enabled.setdefault(platform, self._defaults_for(platform))

    def visible_for(self, platform: Platform) -> set[str]:
        if platform is Platform.IOS:
            return self.ios_visible_charts
        return self.windows_visible_charts if platform is Platform.WINDOWS else self.visible_charts

    def set_metric_enabled(self, platform: Platform, key: str, enabled: bool) -> None:
        selection = self.enabled_for(platform)
        if enabled:
            selection.add(key)
        else:
            selection.discard(key)

    def snapshot_selection(self) -> dict[Platform, set[str]]:
        """Copy of every platform's selection, for temporary view-mode overrides."""
        return {platform: set(keys) for platform, keys in self._enabled.items()}

    def restore_selection(self, snapshot: dict[Platform, set[str]]) -> None:
        self._enabled = {platform: set(keys) for platform, keys in snapshot.items()}

    def set_sample_interval_ms(self, milliseconds: int) -> int:
        self.sample_interval_ms = max(
            MINIMUM_SAMPLE_INTERVAL_MS,
            min(MAXIMUM_SAMPLE_INTERVAL_MS, int(milliseconds)))
        return self.sample_interval_ms

    def set_screenshot_interval_ms(self, milliseconds: int) -> int:
        self.screenshot_interval_ms = max(
            MINIMUM_SCREENSHOT_INTERVAL_MS,
            min(MAXIMUM_SCREENSHOT_INTERVAL_MS, int(milliseconds)))
        return self.screenshot_interval_ms

    def last_target(self, device_id: str) -> str:
        return str(self._settings.value(f"targets/{device_id}", "") or "")

    def remember_target(self, device_id: str, target_id: str) -> None:
        self._settings.setValue(f"targets/{device_id}", target_id)
        self._settings.sync()
