from __future__ import annotations

import sys
import shutil
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_RESOURCES = Path(__file__).resolve().parent / '_resources'
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", _PACKAGE_RESOURCES
    if (_PACKAGE_RESOURCES / 'assets' / 'report_template_v10.html').is_file() else SOURCE_ROOT)).resolve()
WORK_ROOT = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
             else SOURCE_ROOT if (SOURCE_ROOT / 'app.py').is_file() else Path.cwd())


def find_tool(name: str) -> str | None:
    """Keep an explicitly configured PATH tool; otherwise use SDK resources."""
    located = shutil.which(name)
    if located:
        return located
    bundled = RESOURCE_ROOT / 'tools' / (name + '.exe' if sys.platform == 'win32' else name)
    return str(bundled) if bundled.is_file() else None
