@echo off
rem Agent Neo (agent-neo) desktop GUI. Runs windowless with pythonw from the repo .venv.
rem Options are passed through: --workspace DIR, --backend copilot^|gemini^|deepseek^|desktop^|auto^|custom, --browser-mode offscreen^|visible^|headless
setlocal
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"
start "" "%PYW%" "%~dp0neo_gui.py" %*
