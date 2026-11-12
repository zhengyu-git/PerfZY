"""Build a Windows x64 Python SDK wheel and a shareable examples bundle."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--output', type=Path, default=ROOT / 'exports' / 'sdk')
            args = parser.parse_args()
                output = args.output.resolve()
                    output.mkdir(parents=True, exist_ok=True)
                        version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
                            wheel_dir = output / 'wheels'
                                wheel_dir.mkdir(exist_ok=True)
                                    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--no-index',
                                                        '--no-cache-dir', '--wheel-dir', str(wheel_dir), str(ROOT)], cwd=ROOT, check=True)
                                                            wheel = wheel_dir / f'perfzy_local-{version}-py3-none-win_amd64.whl'
                                                                if not wheel.is_file():
                                                                        raise FileNotFoundError(f'Expected Windows SDK wheel: {wheel.name}')
                                                                            bundle = output / f'PerfZY-Python-SDK-{version}'
                                                                                bundle.mkdir(exist_ok=True)
                                                                                    files = [(wheel, bundle / wheel.name),
                                                                                                 (ROOT / 'docs/PythonSDK使用说明.md', bundle / '使用说明.md'),
                                                                                                              (ROOT / 'LICENSE', bundle / 'LICENSE')]
                                                                                                                  files.extend((ROOT / 'examples' / name, bundle / 'examples' / name) for name in
                                                                                                                                   ('python_automation.py', 'pytest_perfzy_example.py', 'merge_and_report.py'))
                                                                                                                                       for source, destination in files:
                                                                                                                                               destination.parent.mkdir(parents=True, exist_ok=True)
                                                                                                                                                       shutil.copy2(source, destination)
                                                                                                                                                           install = bundle / 'INSTALL_SDK.cmd'
                                                                                                                                                               install.write_bytes((f'@echo off\r\ncd /d "%~dp0"\r\n'
                                                                                                                                                                       f'python -m pip install "%~dp0{wheel.name}"\r\n'
                                                                                                                                                                               'if errorlevel 1 exit /b 1\r\n'
                                                                                                                                                                                       'python -c "import perfzy; print(perfzy.__version__, perfzy.__file__)"\r\n').encode('ascii'))
                                                                                                                                                                                           paths = [destination for _, destination in files] + [install]
                                                                                                                                                                                               manifest = bundle / 'SHA256SUMS.txt'
                                                                                                                                                                                                   manifest.write_text(''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(bundle).as_posix()}\n'
                                                                                                                                                                                                                                   for path in paths), encoding='utf-8')
                                                                                                                                                                                                                                       archive = output / (bundle.name + '.zip')
                                                                                                                                                                                                                                           with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
                                                                                                                                                                                                                                                   for path in paths + [manifest]:
                                                                                                                                                                                                                                                               zipped.write(path, arcname=(Path(bundle.name) / path.relative_to(bundle)).as_posix())
                                                                                                                                                                                                                                                                   print(archive)


                                                                                                                                                                                                                                                                   if __name__ == '__main__':
                                                                                                                                                                                                                                                                       main()
                                                                                                                                                                                                                                                                       