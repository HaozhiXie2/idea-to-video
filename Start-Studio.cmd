@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-studio.ps1" %*
if errorlevel 1 (
  echo.
  echo Studio did not start. Read the message above or docs\INSTALL.md.
  pause
  exit /b 1
)
endlocal
