@echo off
title MYRA Localhost Launcher
cd /d D:\01screener\Myra

echo.
echo ========================================
echo   MYRA - Starting Local Development
echo ========================================
echo.

:: ---------- Pipeline (silent background) ----------
echo [1/3] Starting data pipeline (background, no window) …
start /b pythonw run_pipeline.py

:: Give it a moment to initialise
timeout /t 2 /nobreak >nul

:: ---------- FastAPI backend ----------
echo [2/3] Starting FastAPI backend on http://localhost:8000 …
start "MYRA Backend" cmd /k "cd /d D:\01screener\Myra && python -m uvicorn myra_web.myra_fastapi_server:app --reload --host 0.0.0.0 --port 8000"

timeout /t 2 /nobreak >nul

:: ---------- Vite frontend ----------
echo [3/3] Starting Vite frontend on http://localhost:3000 …
start "MYRA Frontend" cmd /k "cd /d D:\01screener\Myra\myra_web && npm run dev"

echo.
echo ========================================
echo   All services started
echo   Pipeline  : running silently
echo   Backend   : http://localhost:8000
echo   Frontend  : http://localhost:3000
echo   API Docs  : http://localhost:8000/docs
echo ========================================
echo.

:: Close this window automatically after 5 seconds
timeout /t 5 /nobreak >nul
exit