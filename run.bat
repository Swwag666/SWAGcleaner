@echo off
rem SWAGcleaner launcher - runs the app with the project venv
cd /d "%~dp0"
if not exist venv\Scripts\pythonw.exe (
    echo venv not found. Run: python -m venv venv ^&^& venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
start "" venv\Scripts\pythonw.exe -m swagcleaner
