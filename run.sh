#!/usr/bin/env bash
# Starts pulse on http://localhost:8100
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
echo "Open http://localhost:8100"
uvicorn app.main:app --port 8100 --reload
