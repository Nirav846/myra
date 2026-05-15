@echo off
title MYRA Localhost Launcher

cd /d D:\01screener\Myra

echo.
echo ========================================
echo   MYRA - Starting Local Development
echo ========================================
echo.

:: ---------- Pipeline ----------
echo [1/3] Starting data pipeline ...
start /min "MYRA Pipeline" cmd /k "cd /d D:\01screener\Myra && python run_pipeline.py"

:: Give it a moment to initialize
timeout /t 2 /nobreak >nul

:: ---------- FastAPI backend ----------
echo [2/3] Starting FastAPI backend ...
start /min "MYRA Backend" cmd /k "cd /d D:\01screener\Myra && python -m uvicorn myra_web.myra_fastapi_server:app --reload --host 0.0.0.0 --port 8000"

timeout /t 2 /nobreak >nul

:: ---------- Vite frontend ----------
echo [3/3] Starting Vite frontend ...
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