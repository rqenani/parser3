@echo off
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.server:app --host 0.0.0.0 --port 8000
