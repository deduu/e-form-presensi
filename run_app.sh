#!/usr/bin/env bash

echo "================================="
echo "1) Checking if Python3 is installed..."
echo "================================="

if ! command -v python3 &> /dev/null
then
    echo "Python3 not found. Attempting to install via Homebrew..."
    if ! command -v brew &> /dev/null
    then
        echo "Homebrew is not installed. Please install Homebrew first (https://brew.sh/) then re-run."
        exit 1
    else
        brew update
        brew install python
        if [ $? -ne 0 ]; then
            echo "Failed to install Python via Homebrew. Exiting."
            exit 1
        fi
    fi
else
    echo "Python3 is installed."
fi

echo "================================="
echo "2) Setting up pip environment..."
echo "================================="
python3 -m ensurepip --upgrade
pip3 install --upgrade pip

echo "================================="
echo "3) Installing dependencies from requirements.txt..."
echo "================================="
pip3 install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "Failed to install Python packages from requirements.txt"
    exit 1
fi

echo "================================="
echo "4) Starting FastAPI application..."
echo "================================="

# Launch uvicorn in the background
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
UVICORN_PID=$!

# Open the default browser to http://127.0.0.1:8000
open "http://127.0.0.1:8000"

echo "Server running (PID $UVICORN_PID). Press Ctrl+C to stop or close this terminal."

# Wait for uvicorn to exit
wait $UVICORN_PID

echo "Server stopped. The app.on_event('shutdown') hook in FastAPI should now sync data to Google Sheets."
