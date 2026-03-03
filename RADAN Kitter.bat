@echo off
setlocal

set APPDIR=C:\Tools\radan_kitter_controlled_migration
set PY=%APPDIR%\.venv\Scripts\pythonw.exe
if not exist "%PY%" set PY=C:\Tools\radan_venv\Scripts\pythonw.exe
set LOG=%APPDIR%\radan_kitter_launch.log

cd /d "%APPDIR%" || exit /b 1

echo ===== %date% %time% ===== > "%LOG%"
echo Python: %PY% >> "%LOG%"
echo Arg1: %~1 >> "%LOG%"

rem Kill stale prior instances for this app path before relaunch.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { ($_.Name -match '^pythonw?\.exe$') -and ($_.CommandLine -like '*radan_kitter_controlled_migration\\main.py*') }; " ^
  "foreach ($p in $procs) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {} }"

"%PY%" "%APPDIR%\main.py" "%~1" >> "%LOG%" 2>&1

set HAD_ERROR=0
if errorlevel 1 set HAD_ERROR=1
findstr /I /C:"Traceback (most recent call last):" "%LOG%" >nul && set HAD_ERROR=1
findstr /I /C:"Fatal Python error" "%LOG%" >nul && set HAD_ERROR=1

if "%HAD_ERROR%"=="1" (
  echo.
  echo Launch failed. Log:
  type "%LOG%"
  echo.
  pause
)

endlocal
