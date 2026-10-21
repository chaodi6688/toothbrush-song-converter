@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 优先使用 Codex 运行时内置的 Python;找不到再回退到系统 python
set "PYEXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

echo ============================================================
echo   歌曲 - 声波电动牙刷播放格式 转化程序(本地 Web 应用)
echo   使用的 Python: %PYEXE%
echo   稍后会自动打开浏览器: http://127.0.0.1:8765
echo   请保持本窗口开启;关闭本窗口即停止服务。
echo ============================================================
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8765"
"%PYEXE%" run.py

echo.
echo 服务已停止。按任意键关闭窗口。
pause >nul
