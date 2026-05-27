@echo off
title MYRA Localhost Launcher

cd /d D:\01screener\Myra

echo.
echo ========================================
echo   MYRA - Starting Local Development
echo ========================================
echo.

:: ---------- FastAPI backend ----------
echo [1/2] Starting FastAPI backend ...
echo Pipeline not auto-started. Visit /data-sync to run.
start "MYRA Backend" cmd /k "cd /d D:\01screener\Myra && python run_fastapi.py"

timeout /t 2 /nobreak >nul

:: ---------- Vite frontend ----------
echo [2/2] Starting Vite frontend ...
start /min "MYRA Frontend" cmd /k "cd /d D:\01screener\Myra\myra_web && npm run dev"

echo.
echo ========================================
echo   All services started
echo.
echo   Backend   : http://localhost:8000
echo   Frontend  : http://localhost:3000
echo   API Docs  : http://localhost:8000/docs
echo ========================================
echo.

:: Auto close launcher window
timeout /t 3 /nobreak >nul
exit