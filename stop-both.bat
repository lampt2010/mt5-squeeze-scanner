@echo off
title Stop MT5 Scanner
echo Stopping Backend (8000) and Frontend (8080)...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000.*LISTENING"') do ( taskkill /PID %%a /F 2>nul )
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080.*LISTENING"') do ( taskkill /PID %%a /F 2>nul )
echo Done.
timeout /t 2 /nobreak >nul
