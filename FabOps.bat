@echo off
REM FabOps launcher. Double-click this file to start FabOps.
REM
REM It does the two things the README documents, in order:
REM   python -m pip install -e ".[app]"
REM   fabops-app
REM
REM Nothing else. If installation fails, FabOps is not started.

REM Work from the folder this file lives in, so double-clicking it from
REM Explorer and running it from another directory behave the same way.
cd /d "%~dp0"

REM Use the repository's own environment when the checkout has one.
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

echo ========================================
echo  FabOps Launcher
echo ========================================
echo.
echo Installing FabOps...
echo.

python -m pip install -e ".[app]"

if %errorlevel% neq 0 (
    echo.
    echo FabOps installation failed.
    echo FabOps was not started.
    pause
    exit /b 1
)

echo.
echo Installation successful.
echo.
echo Starting FabOps...
echo.

REM This window stays attached to FabOps until the application stops,
REM and the launcher then exits with the application's exit code.
fabops-app

exit /b %errorlevel%
