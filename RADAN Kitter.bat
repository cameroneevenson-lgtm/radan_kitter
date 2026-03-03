@echo off
setlocal

set APPDIR=C:\Tools\radan_kitter_controlled_migration
set PYC=%APPDIR%\.venv\Scripts\python.exe
set PYW=%APPDIR%\.venv\Scripts\pythonw.exe
if not exist "%PYC%" set PYC=C:\Tools\radan_venv\Scripts\python.exe
if not exist "%PYW%" set PYW=C:\Tools\radan_venv\Scripts\pythonw.exe
set LOG=%APPDIR%\radan_kitter_launch.log
set DEV_HOT_RELOAD=1
set HOT_RELOAD_INTERVAL=0.6
set HOT_RELOAD_DEBOUNCE=2.5
set HOT_RELOAD_MIN_UPTIME=1.2
if not "%RADAN_KITTER_HOT_RELOAD%"=="" set DEV_HOT_RELOAD=%RADAN_KITTER_HOT_RELOAD%
if not "%RADAN_KITTER_HOT_INTERVAL%"=="" set HOT_RELOAD_INTERVAL=%RADAN_KITTER_HOT_INTERVAL%
if not "%RADAN_KITTER_HOT_DEBOUNCE%"=="" set HOT_RELOAD_DEBOUNCE=%RADAN_KITTER_HOT_DEBOUNCE%
if not "%RADAN_KITTER_HOT_MIN_UPTIME%"=="" set HOT_RELOAD_MIN_UPTIME=%RADAN_KITTER_HOT_MIN_UPTIME%

cd /d "%APPDIR%" || exit /b 1

echo ===== %date% %time% ===== > "%LOG%"
echo BAT: %~f0 >> "%LOG%"
echo PythonW: %PYW% >> "%LOG%"
echo Python: %PYC% >> "%LOG%"
echo Args: %* >> "%LOG%"

rem Kill stale prior instances for this app path before relaunch.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { ($_.Name -match '^pythonw?\.exe$') -and (($_.CommandLine -like '*radan_kitter_controlled_migration\\main.py*') -or ($_.CommandLine -like '*radan_kitter_controlled_migration\\dev_hot_restart.py*')) }; " ^
  "foreach ($p in $procs) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {} }"

if "%DEV_HOT_RELOAD%"=="1" (
  echo MODE=hot_reload >> "%LOG%"
  echo HOT_RELOAD_INTERVAL=%HOT_RELOAD_INTERVAL% >> "%LOG%"
  echo HOT_RELOAD_DEBOUNCE=%HOT_RELOAD_DEBOUNCE% >> "%LOG%"
  echo HOT_RELOAD_MIN_UPTIME=%HOT_RELOAD_MIN_UPTIME% >> "%LOG%"
  "%PYC%" "%APPDIR%\dev_hot_restart.py" --interval %HOT_RELOAD_INTERVAL% --debounce %HOT_RELOAD_DEBOUNCE% --min-uptime %HOT_RELOAD_MIN_UPTIME% %*
  set EXITCODE=%ERRORLEVEL%
  if not "%EXITCODE%"=="0" (
    echo.
    echo Hot reload launcher exited with code %EXITCODE%.
    echo See %LOG% for startup details.
    pause
  )
  endlocal & exit /b %EXITCODE%
)

echo MODE=stable >> "%LOG%"

"%PYW%" "%APPDIR%\main.py" %* >> "%LOG%" 2>&1

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
