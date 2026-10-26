@echo off
setlocal
cd /d "%~dp0"
python "%~dp0app.py"
if errorlevel 1 goto startup_failed
exit /b 0

:startup_failed
echo.
echo Novel Reader failed to start. See the error message above.
pause
exit /b 1
