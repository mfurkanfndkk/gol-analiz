@echo off
title GOL Analiz Sistemi
cd /d "C:\Users\FURKAN\Desktop\goll"

echo ===================================================
echo   GOL Analiz Sistemi Baslatiliyor...
echo ===================================================
echo.

:: Sunucunun acilmasi icin 2 saniye bekleyip tarayiciyi acar
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:5000"

:: Python uygulamasini calistir
python app.py

pause
