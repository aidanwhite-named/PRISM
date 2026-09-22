@echo off
if exist "%~dp0unins000.exe" (
  start "" powershell.exe -NoLogo -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0scripts\install-window.ps1" -Managed
) else (
  start "" powershell.exe -NoLogo -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0scripts\install-window.ps1"
)
