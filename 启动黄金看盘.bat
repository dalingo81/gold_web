@echo off
chcp 65001 >nul
title Gold Price Dashboard
cd /d "%~dp0"

rem ---- 可改配置 ----------------------------------------------------------
rem GOLD_HOST 监听地址：
rem   127.0.0.1  = 仅本机访问（默认，最安全）
rem   ::         = IPv4 / IPv6 双栈，局域网与 IPv6 地址都可访问
rem   0.0.0.0    = 仅 IPv4，局域网可访问
rem GOLD_PORT 监听端口
set GOLD_HOST=127.0.0.1
set GOLD_PORT=8765
rem -----------------------------------------------------------------------

set PYBIN=C:\Users\Thomas\.workbuddy\binaries\python\versions\3.13.12\python.exe
if exist "%PYBIN%" goto RUN
set PYBIN=python.exe

:RUN
echo Starting gold quote service ...
"%PYBIN%" gold_server.py
echo.
echo Service stopped. Press any key to close.
pause >nul
