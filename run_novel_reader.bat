@echo off
setlocal
cd /d "%~dp0"
python app.py
if errorlevel 1 (
    echo.
    echo 启动失败，请确认本机已安装 Python 3。
    pause
)
