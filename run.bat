@echo off
cd /d "%~dp0"
py backend.py
if errorlevel 1 pause
