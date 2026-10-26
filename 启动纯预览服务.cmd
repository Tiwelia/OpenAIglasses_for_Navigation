@echo off
setlocal
cd /d "%~dp0"
set COURSE_AI_ENABLED=0
set COURSE_PORT=8081
echo Browser preview: http://127.0.0.1:8081/preview
echo AI is disabled. Keep this window open. Press Ctrl+C to stop.
"%~dp0.venv\Scripts\python.exe" -B -m course.app
pause
