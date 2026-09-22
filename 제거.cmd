@echo off
if exist "%~dp0unins000.exe" (
  start "" "%~dp0unins000.exe"
) else (
  echo Windows Settings - Apps - PRISM: Uninstall
  pause
)
