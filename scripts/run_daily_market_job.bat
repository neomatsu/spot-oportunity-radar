@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"

rem Usar siempre el python del .venv para evitar conflictos con el PATH del sistema
set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo ERROR: No se encontro .venv en %PROJECT_ROOT%
    echo Ejecuta: python3.13 -m venv .venv ^&^& .venv\Scripts\pip install -e .[dev]
    exit /b 1
)

if not exist "logs" mkdir "logs"

set "STAMP=%DATE:~6,4%-%DATE:~3,2%-%DATE:~0,2%_%TIME:~0,2%-%TIME:~3,2%"
set "STAMP=%STAMP: =0%"
set "LOG_FILE=logs\\daily_market_run_%STAMP%.log"

"%PYTHON_EXE%" -m jobs.daily_market_run %* >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

echo Log guardado en %LOG_FILE%
exit /b %EXIT_CODE%
