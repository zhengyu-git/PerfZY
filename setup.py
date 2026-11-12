"""Bundle the resources used by headless collection and HTML generation."""
from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
SDK_RESOURCES = (
    'assets/report_template_v10.html',
    'assets/report_theme.css', 'assets/report_theme.js',
    'assets/report_workbench.css', 'assets/report_workbench.js', 'assets/perfzy-icon-rounded.png',
    'assets/perfzy-icon-rounded.ico', 'assets/perfzy-logo-source.png',
    'assets/perfzy-icon-rounded-v2.ico',
    'tools/ios_bridge.py', 'tools/PresentMon-LICENSE.txt', 'tools/NOTICE.txt',
)


class SDKBuild(build_py):
    def run(self):
        super().run()
        resource_root = Path(self.build_lib) / 'perfzy' / '_resources'
        for relative in SDK_RESOURCES:
            source, destination = ROOT / relative, resource_root / relative
            if not source.is_file():
                raise FileNotFoundError(f'SDK resource missing: {relative}')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


setup(cmdclass={'build_py': SDKBuild})
