@echo off
title MT5 Squeeze Scanner - Launcher
cd /d "%~dp0"

echo Stopping old processes on 8000 and 8080...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000.*LISTENING"') do taskkill /PID %%a /F 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080.*LISTENING"') do taskkill /PID %%a /F 2>nul
timeout /t 2 /nobreak >nul

echo Starting Backend (port 8000)...
start "MT5 Backend" cmd /k "cd /d "%~dp0" &&  c:\Users\lampt\AppData\Local\Programs\Python\Python38\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"

echo Waiting for backend to start...
timeout /t 4 /nobreak >nul

echo Starting Frontend (port 8080)...
start "MT5 Frontend" cmd /k "cd /d "%~dp0\frontend" &&  c:\Users\lampt\AppData\Local\Programs\Python\Python38\python.exe -m http.server 8080"

echo.
echo Backend: http://127.0.0.1:8000
echo Frontend: http://127.0.0.1:8080
echo.
echo Open browser to http://127.0.0.1:8080
start "" "http://127.0.0.1:8080"

echo Done. Close the Backend and Frontend windows to stop.
pause
