@echo off
setlocal
cd /d "%~dp0"
echo Installing PerfZY local Python API...
set "PERFZY_INSTALL_PYTHON="
if exist "%~dp0.venv\Scripts\python.exe" set "PERFZY_INSTALL_PYTHON=%~dp0.venv\Scripts\python.exe"
for /f "delims=" %%P in ('where py.exe 2^>nul') do if not defined PERFZY_INSTALL_PYTHON set "PERFZY_INSTALL_PYTHON=%%P"
if not defined PERFZY_INSTALL_PYTHON for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined PERFZY_INSTALL_PYTHON set "PERFZY_INSTALL_PYTHON=%%P"
if not defined PERFZY_INSTALL_PYTHON goto no_python

"%PERFZY_INSTALL_PYTHON%" -m pip install -c "%~dp0requirements-freeze.txt" -e .
if errorlevel 1 (
    echo.
    echo Installation failed. Try: python -m pip install -e "%~dp0"
    pause
    exit /b 1
)
echo.
echo PerfZY API installed. It can now be imported from any Python script.
"%PERFZY_INSTALL_PYTHON%" -c "from perfzy import PerfZY; print('PerfZY import OK:', PerfZY)"
pause
exit /b 0

:no_python
echo.
echo Installation failed: Python was not found in PATH.
echo Install Python 3.11 or newer, then run this file again.
pause
exit /b 1
