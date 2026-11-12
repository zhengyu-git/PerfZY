@echo off
setlocal
cd /d "%~dp0"

set "PERFZY_PYTHONW="
set "PERFZY_PYTHON="

if exist "%~dp0.venv\Scripts\pythonw.exe" if exist "%~dp0.venv\Scripts\python.exe" set "PERFZY_PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
if defined PERFZY_PYTHONW set "PERFZY_PYTHON=%~dp0.venv\Scripts\python.exe"

if not defined PERFZY_PYTHONW for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined PERFZY_PYTHONW set "PERFZY_PYTHONW=%%P"
if not defined PERFZY_PYTHON for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined PERFZY_PYTHON set "PERFZY_PYTHON=%%P"

if not defined PERFZY_PYTHONW for /f "delims=" %%P in ('where pyw.exe 2^>nul') do if not defined PERFZY_PYTHONW set "PERFZY_PYTHONW=%%P"
if not defined PERFZY_PYTHON for /f "delims=" %%P in ('where py.exe 2^>nul') do if not defined PERFZY_PYTHON set "PERFZY_PYTHON=%%P"

if not defined PERFZY_PYTHONW goto no_python
if not defined PERFZY_PYTHON goto no_python

"%PERFZY_PYTHON%" -c "import sys; sys.path.insert(0, r'%~dp0vendor'); import PySide6, psutil, PIL" >nul 2>nul
if errorlevel 1 goto missing_dependencies

start "PerfZY" /d "%~dp0" "%PERFZY_PYTHONW%" "%~dp0app.py"
exit /b 0

:missing_dependencies
echo PerfZY found Python but required packages are missing.
echo Run: "%PERFZY_PYTHON%" -m pip install -r "%~dp0requirements.txt"
pause
exit /b 2

:no_python
echo PerfZY could not find Python. Please install Python or add it to PATH.
pause
exit /b 1
