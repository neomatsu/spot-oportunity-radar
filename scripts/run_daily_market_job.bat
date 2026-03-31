@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"

if exist ".venv\\Scripts\\activate.bat" (
    call ".venv\\Scripts\\activate.bat"
)

if not exist "logs" mkdir "logs"

set "STAMP=%DATE:~6,4%-%DATE:~3,2%-%DATE:~0,2%_%TIME:~0,2%-%TIME:~3,2%"
set "STAMP=%STAMP: =0%"
set "LOG_FILE=logs\\daily_market_run_%STAMP%.log"

python -m jobs.daily_market_run %* >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

echo Log guardado en %LOG_FILE%
exit /b %EXIT_CODE%
