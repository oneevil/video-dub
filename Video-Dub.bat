@echo off
REM Ярлык запуска для Windows. Всю работу делает bootstrap.ps1 — здесь только
REM обход политики выполнения скриптов, иначе PowerShell откажется его запускать
REM на машине с настройками по умолчанию.
title Video-Dub
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1"
if errorlevel 1 pause
