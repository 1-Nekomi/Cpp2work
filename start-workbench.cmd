@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0import.ps1" review --workspace "%~dp0imports\validation-final" --open
pause
