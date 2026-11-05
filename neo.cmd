@echo off
rem Agent Neo (agent-neo / neo) CLI launcher.
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python.exe"
"%PY%" "%~dp0neo.py" %*
