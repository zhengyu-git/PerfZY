"""Offline report appearance shared by the viewer and comparison documents."""
from configparser import ConfigParser, Error
from pathlib import Path

from .runtime_paths import RESOURCE_ROOT


def report_theme(preferences_path: Path | None = None, override: str | None = None) -> str:
    if override in ('light','dark'):
        return override
    if preferences_path and preferences_path.is_file():
        settings = ConfigParser(interpolation=None)
        try:
            settings.read(preferences_path, encoding='utf-8-sig')
            return 'light' if settings.get('appearance', 'mode', fallback='dark') == 'light' else 'dark'
        except (Error, OSError, UnicodeError):
            pass
    return 'light'


def report_theme_assets() -> tuple[str, str]:
    assets = RESOURCE_ROOT / 'assets'
    return ((assets / 'report_theme.css').read_text(encoding='utf-8'),
            (assets / 'report_theme.js').read_text(encoding='utf-8'))
