@echo off
setlocal
cd /d "%~dp0"
set "PREVIEW_DEVICE=COM5"
if not "%~1"=="" set "PREVIEW_DEVICE=%~1"
echo ESP32 USB preview on %PREVIEW_DEVICE%. Close Arduino Serial Monitor first.
echo Start the preview server in another window. Press Ctrl+C to stop.
"%~dp0.venv\Scripts\python.exe" -B -u -m course.usb_camera --device "%PREVIEW_DEVICE%" --fps 10
pause
