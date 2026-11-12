import os
from pathlib import Path
import subprocess
import sys


def test_disabled_controls_show_explanations_without_becoming_clickable():
    directory = Path(__file__).resolve().parent
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QSG_RENDER_LOOP='basic', PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    result = subprocess.run([sys.executable, '-B', str(directory / 'qml_disabled_hover_probe.py'),
                             str(directory.parents[1])], env=environment,
                            capture_output=True, encoding='utf-8', errors='replace', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
