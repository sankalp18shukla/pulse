@echo off
REM Starts Pulse on http://localhost:8100
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
if not exist .env ( copy .env.example .env >nul )
echo Open http://localhost:8100
uvicorn app.main:app --port 8100 --reload
