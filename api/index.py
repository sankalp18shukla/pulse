"""Vercel entry point: Vercel runs files in api/ as serverless functions and
serves this ASGI app. vercel.json sends every path here, so the FastAPI app
handles the page, /static files and /api routes exactly as it does locally."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app  # noqa: E402,F401
