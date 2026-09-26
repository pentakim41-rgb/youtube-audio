@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
    echo [1/2] 가상환경 만드는 중...
    py -3 -m venv .venv || python -m venv .venv
    call .venv\Scripts\activate
    python -m pip install -U pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate
)
python main.py
if errorlevel 1 pause
