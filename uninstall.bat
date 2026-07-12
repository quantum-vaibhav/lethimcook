@echo off
rem One-click uninstall for lethimcook (Windows)
setlocal
set "SCRIPT=%~dp0setup.py"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%SCRIPT%" --uninstall
    goto :end
)
where python >nul 2>nul
if %errorlevel%==0 (
    python "%SCRIPT%" --uninstall
    goto :end
)
echo Python 3 is required to run the uninstaller.

:end
echo.
pause
