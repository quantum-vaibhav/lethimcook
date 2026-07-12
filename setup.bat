@echo off
rem One-click setup for lethimcook (Windows)
setlocal
set "SCRIPT=%~dp0setup.py"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%SCRIPT%" %*
    goto :end
)
where python >nul 2>nul
if %errorlevel%==0 (
    python "%SCRIPT%" %*
    goto :end
)
echo Python 3 is required. Install it from https://www.python.org/downloads/
echo (Check "Add python.exe to PATH" during install, then run this again.)

:end
echo.
pause
