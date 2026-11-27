@echo off
title Enterprise Multi-Store Inventory & Order Allocation System
echo =====================================================================
echo    Enterprise Multi-Store Inventory & Order Allocation System
echo =====================================================================
echo.

if not exist "data" mkdir data
if exist "data\*.xlsx" (
    echo [INFO] Stock report found in data folder.
) else (
    echo [INFO] No stock report yet - upload it in the Stock tab once the app opens.
)

echo.
if exist ".venv\Scripts\uvicorn.exe" (
    echo [INFO] Launching with .venv uvicorn at http://127.0.0.1:8000...
    start "" http://127.0.0.1:8000
    ".venv\Scripts\uvicorn.exe" app:app --host 127.0.0.1 --port 8000
) else (
    echo [INFO] Launching with system Python uvicorn at http://127.0.0.1:8000...
    start "" http://127.0.0.1:8000
    python -m uvicorn app:app --host 127.0.0.1 --port 8000
)

pause
