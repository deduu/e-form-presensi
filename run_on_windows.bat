@echo off
echo =============================
echo 1) Checking if Python is installed...
echo =============================

where python >nul 2>nul
IF %ERRORLEVEL% NEQ 0 (
    echo Python not found. Attempting to install via winget...
    winget install -e --id Python.Python.3.10
    if %ERRORLEVEL% NEQ 0 (
        echo Failed to install Python automatically. Please install Python manually and re-run.
        pause
        exit /b 1
    )
)

echo =============================
echo 2) Setting up pip environment...
echo =============================
python -m ensurepip --upgrade

echo =============================
echo 3) Installing dependencies from requirements.txt...
echo =============================
pip install --upgrade pip
pip install -r requirements.txt

if %ERRORLEVEL% NEQ 0 (
    echo Failed to install Python packages. Please check errors.
    pause
    exit /b 1
)

echo =============================
echo 4) Starting FastAPI application...
echo =============================

REM Launch the default browser to http://127.0.0.1:8000
start "" http://127.0.0.1:8000

echo Launching uvicorn on port 8000. Press CTRL+C here or close this window to stop.
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

echo Server stopped. Any on_shutdown events should have fired, syncing to Google Sheets (if coded to do so).
pause
