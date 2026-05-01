@echo off
setlocal

cd /d %~dp0

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo ERROR: No se encontro .venv. Ejecuta primero:
    echo   python3.13 -m venv .venv
    echo   .venv\Scripts\pip install -e .[dev]
    pause
    exit /b 1
)

echo Cerrando instancias anteriores de Spot Opportunity Radar...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$projectRoot = '%CD%';" ^
  "$projectRoot = $projectRoot -replace '\\\\', '\\';" ^
  "$pythonExe = '%PYTHON_EXE%';" ^
  "$targets = Get-CimInstance Win32_Process | Where-Object { ($_.Name -in @('python.exe','streamlit.exe')) -and ($_.CommandLine -like '*spot-oportunity-radar*app/main.py*' -or $_.CommandLine -like '*spot-oportunity-radar*app\\main.py*') };" ^
  "foreach ($p in $targets) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {} };" ^
  "Start-Sleep -Seconds 1;" ^
  "$env:PYTHONPATH = $projectRoot;" ^
  "Write-Host 'Iniciando Spot Opportunity Radar...';" ^
  "Start-Process -FilePath $pythonExe -ArgumentList '-m','streamlit','run','app/main.py','--server.headless','true' -WorkingDirectory $projectRoot -WindowStyle Minimized;" ^
  "Write-Host 'Esperando a que arranque Streamlit (max 60s)...';" ^
  "for ($i = 1; $i -le 60; $i++) {" ^
  "  Start-Sleep -Seconds 1;" ^
  "  try {" ^
  "    $response = Invoke-WebRequest -UseBasicParsing 'http://localhost:8501/_stcore/health' -TimeoutSec 2 -ErrorAction Stop;" ^
  "    if ($response.StatusCode -eq 200) {" ^
  "      Write-Host \"Listo en ${i}s — abriendo navegador...\";" ^
  "      Start-Process 'http://localhost:8501';" ^
  "      exit 0;" ^
  "    }" ^
  "  } catch {}" ^
  "  if ($i %% 10 -eq 0) { Write-Host \"  ... ${i}s\" };" ^
  "}" ^
  "Write-Host 'Streamlit tarda mas de lo esperado. Abriendo navegador de todas formas...';" ^
  "Start-Process 'http://localhost:8501';"

endlocal
