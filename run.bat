@echo off
rem Start Flight Grabber (Windows). First run sets everything up.
cd /d "%~dp0"
if not exist .venv (
  echo First run: setting up ^(this takes a minute^)...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\pip install -q -r requirements.txt
)
start "" http://localhost:8000
.venv\Scripts\python -m flightgrabber --host 0.0.0.0 --port 8000
