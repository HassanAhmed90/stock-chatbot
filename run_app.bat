@echo off
title Enterprise Multi-Store Inventory & Order Allocation System
echo =====================================================================
echo    Enterprise Multi-Store Inventory & Order Allocation System
echo =====================================================================
echo.

set VENV_PYTHON=.venv\Scripts\python.exe
set VENV_STREAMLIT=.venv\Scripts\streamlit.exe

if exist "%VENV_PYTHON%" (
    echo [INFO] Found virtual environment in .venv.
    echo.
    echo [1/3] Installing Dependencies into .venv...
    "%VENV_PYTHON%" -m pip install -r requirements.txt

    echo.
    echo [2/3] Checking data folder...
    if not exist "data" mkdir data
    if exist "data\*.xlsx" (
        echo [INFO] Stock report found in data folder.
    ) else (
        echo [INFO] No stock report yet - upload it in the Stock tab once the app opens.
    )

    echo.
    echo [3/3] Launching Web Application...
    echo Opening application at http://localhost:8501...
    "%VENV_STREAMLIT%" run app.py
) else (
    echo [INFO] .venv not found, attempting system python...
    echo [1/3] Installing Python Dependencies...
    python -m pip install -r requirements.txt

    echo.
    echo [2/3] Checking data folder...
    if not exist "data" mkdir data
    if exist "data\*.xlsx" (
        echo [INFO] Stock report found in data folder.
    ) else (
        echo [INFO] No stock report yet - upload it in the Stock tab once the app opens.
    )

    echo.
    echo [3/3] Launching Web Application...
    streamlit run app.py
)

pause