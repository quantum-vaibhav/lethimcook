@echo off
rem Open the lethimcook control panel (Windows) - no console window.
setlocal
set "GUI=%~dp0scripts\gui.py"

rem pythonw runs the GUI without a lingering console window.
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%GUI%"
    goto :eof
)
where py >nul 2>nul
if %errorlevel%==0 (
    start "" py -3 "%GUI%"
    goto :eof
)
where python >nul 2>nul
if %errorlevel%==0 (
    start "" python "%GUI%"
    goto :eof
)
echo Python 3 is required. Install it from https://www.python.org/downloads/
pause
