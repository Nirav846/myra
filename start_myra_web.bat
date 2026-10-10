@echo off
title MYRA Localhost Launcher

cd /d D:\01screener\Myra

echo.
echo ========================================
echo   MYRA - Starting Local Development
echo ========================================
echo.

:: ---------- FastAPI backend ----------
echo [1/3] Starting FastAPI backend ...
start "MYRA Backend" cmd /k "cd /d D:\01screener\Myra && python run_fastapi.py"

timeout /t 2 /nobreak >nul

:: ---------- Vite frontend ----------
echo [2/2] Starting Vite frontend ...
start /min "MYRA Frontend" cmd /k "cd /d D:\01screener\Myra\myra_web && npm run dev"

:: ---------- Background Pipeline ----------
:: The background scheduler now runs INSIDE the FastAPI process (see
:: run_fastapi.py -> MYRA_EMBED_SCHEDULER and the app lifespan). That gives the
:: frontend one control plane, so Start/Pause/Resume/Cancel operate on the same
:: scheduler that fires the tasks -- no second scheduler, no cross-process race.
:: To run a headless scheduler instead, close the backend window and run:
::     python run_pipeline.py
echo [3/3] Background scheduler runs inside the backend process.

echo.
echo ========================================
echo   All services started
echo.
echo   Backend   : http://localhost:8000
echo   Frontend  : http://localhost:3000
echo   API Docs  : http://localhost:8000/docs
echo.
echo   Pipeline scheduler runs inside the backend. Control it at /data-sync.
echo ========================================
echo.

:: Auto close launcher window
timeout /t 3 /nobreak >nul
exit
