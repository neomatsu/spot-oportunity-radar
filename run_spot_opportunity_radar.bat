@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: No se encontro el entorno virtual .venv
  pause
  exit /b 1
)

set "PYTHONPATH=%CD%"
".venv\Scripts\python.exe" -m streamlit run app/main.py
endlocal
