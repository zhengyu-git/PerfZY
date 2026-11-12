"""Window appearance preferences, independent of collection settings."""
from pathlib import Path

from PySide6.QtCore import QObject, Property, QSettings, Signal, Slot


class Appearance(QObject):
    modeChanged = Signal()

        def __init__(self, settings_path: Path) -> None:
                super().__init__()
                        self._settings = QSettings(str(settings_path), QSettings.IniFormat)
                                stored = str(self._settings.value("appearance/mode", "dark"))
                                        self._mode = stored if stored in {"light", "dark"} else "dark"

                                            @Property(str, notify=modeChanged)
                                                def mode(self):
                                                        return self._mode

                                                            @Slot(str)
                                                                def setMode(self, mode):
                                                                        if mode not in {"light", "dark"} or mode == self._mode:
                                                                                    return
                                                                                            self._mode = mode
                                                                                                    self._settings.setValue("appearance/mode", mode)
                                                                                                            self._settings.sync()
                                                                                                                    self.modeChanged.emit()

                                                                                                                        def set_preview_mode(self, mode: str) -> None:
                                                                                                                                """Screenshots must not change the user's saved theme."""
                                                                                                                                        self._mode = mode
                                                                                                                                                self.modeChanged.emit()
                                                                                                                                                