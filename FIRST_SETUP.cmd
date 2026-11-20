@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0first_setup.ps1"
if errorlevel 1 pause
