@echo off
rem BP-Reviewer double-click entry. Thin wrapper only: all logic lives in start.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
exit /b %ERRORLEVEL%
