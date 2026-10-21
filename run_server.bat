@echo off
rem Hidden background launcher for the local web server, with auto-restart watchdog.
rem Called by the Startup shortcut (run_hidden.vbs). Console output goes to server.log.
cd /d "%~dp0"
set "PYEXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

:loop
netstat -ano | findstr /C:"127.0.0.1:8765" | findstr /C:"LISTENING" >nul 2>nul
if not errorlevel 1 goto already_running
echo [%date% %time%] starting server... >> server.log
"%PYEXE%" -u run.py >> server.log 2>&1
echo [%date% %time%] server exited, restarting in 5s >> server.log
timeout /t 5 /nobreak >nul
goto loop

:already_running
timeout /t 30 /nobreak >nul
goto loop