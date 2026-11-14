@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment in agent\.venv...
    python -m venv .venv
    if errorlevel 1 (
        echo [error] Failed to create virtual environment. Make sure Python 3.10+ is installed.
        exit /b 1
    )
    echo [setup] Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    ".venv\Scripts\pip.exe" install -r requirements.txt
)

".venv\Scripts\python.exe" main.py %*
