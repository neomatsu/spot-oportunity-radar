@echo off
setlocal

cd /d %~dp0

set "PYTHON_EXE=python"
if exist "%CD%\.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
)

echo Cerrando instancias anteriores de Spot Opportunity Radar...
echo Iniciando aplicacion...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$projectRoot = '%CD%';" ^
  "$pythonExe = '%PYTHON_EXE%';" ^
  "$targets = Get-CimInstance Win32_Process | Where-Object { ($_.Name -in @('python.exe','streamlit.exe')) -and ($_.CommandLine -like '*spot-oportunity-radar*app/main.py*' -or $_.CommandLine -like '*spot-oportunity-radar*app\\main.py*') };" ^
  "foreach ($p in $targets) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {} };" ^
  "$env:PYTHONPATH = $projectRoot;" ^
  "Start-Process -FilePath $pythonExe -ArgumentList '-m','streamlit','run','app/main.py','--server.headless','true' -WorkingDirectory $projectRoot -WindowStyle Minimized | Out-Null;" ^
  "for ($i = 0; $i -lt 30; $i++) {" ^
  "  try {" ^
  "    $response = Invoke-WebRequest -UseBasicParsing 'http://localhost:8501/_stcore/health' -TimeoutSec 2;" ^
  "    if ($response.StatusCode -eq 200) {" ^
  "      Start-Process 'http://localhost:8501';" ^
  "      Write-Host 'Aplicacion iniciada en http://localhost:8501';" ^
  "      exit 0;" ^
  "    }" ^
  "  } catch {}" ^
  "  Start-Sleep -Seconds 1;" ^
  "}" ^
  "Write-Host 'No se pudo confirmar el arranque de Streamlit.';" ^
  "Write-Host 'Prueba a ejecutar manualmente: python -m streamlit run app/main.py';" ^
  "exit 1;"

endlocal
