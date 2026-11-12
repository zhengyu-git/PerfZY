"""Frozen screenshot dispatch must not open Qt or trigger UAC."""
import sys
from unittest.mock import Mock

import app
from perfzy.capture import windows


def test_worker_dispatch_precedes_ui_and_elevation(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['PerfZY.exe', '--capture-window-worker'])
        worker = Mock(return_value=7)
            monkeypatch.setattr(windows, '_capture_worker_main', worker)
                monkeypatch.setattr(app.ctypes.windll.shell32, 'IsUserAnAdmin',
                                        Mock(side_effect=AssertionError('worker must not request elevation')))
                                            assert app.main() == 7
                                                worker.assert_called_once_with()
                                                