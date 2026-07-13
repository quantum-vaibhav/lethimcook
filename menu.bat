@echo off
rem Open the lethimcook terminal menu (Windows).
setlocal
set "SCRIPT=%~dp0scripts\menu.py"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%SCRIPT%"
    goto :eof
)
where python >nul 2>nul
if %errorlevel%==0 (
    python "%SCRIPT%"
    goto :eof
)
echo Python 3 is required. Install it from https://www.python.org/downloads/
pause
