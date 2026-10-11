@echo off
rem Run with a console window (shows logging, useful for troubleshooting).
cd /d "%~dp0"
python main.py %*
if errorlevel 1 pause
