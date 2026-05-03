@echo off
title MYRA Localhost Launcher
cd /d D:\01screener\Myra

echo.
echo ========================================
echo   MYRA - Starting Local Development
echo ========================================
echo.

:: Start FastAPI backend in a new window
echo [1/2] Starting FastAPI backend on http://localhost:8000 ...
start "MYRA Backend" cmd /k "cd /d D:\01screener\Myra && python -m uvicorn myra_web.myra_fastapi_server:app --reload --host 0.0.0.0 --port 8000"

:: Wait 2 seconds for backend to initialize
timeout /t 2 /nobreak >nul

:: Start Vite frontend in a new window
echo [2/2] Starting Vite frontend on http://localhost:3000 ...
start "MYRA Frontend" cmd /k "cd /d D:\01screener\Myra\myra_web && npm run dev"

echo.
echo ========================================
echo   Both servers starting in new windows
echo   Backend  : http://localhost:8000
echo   Frontend : http://localhost:3000
echo   API Docs : http://localhost:8000/docs
echo ========================================
echo.
pause