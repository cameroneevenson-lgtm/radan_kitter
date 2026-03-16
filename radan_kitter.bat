@echo off
setlocal EnableExtensions EnableDelayedExpansion

set APPDIR=C:\Tools\radan_kitter
set "PYC="
set "PYC_ARGS="
set "PYW="
set "PYW_ARGS="
if exist "C:\Tools\.venv\Scripts\python.exe" set "PYC=C:\Tools\.venv\Scripts\python.exe"
if not defined PYC if exist "%APPDIR%\.venv\Scripts\python.exe" set "PYC=%APPDIR%\.venv\Scripts\python.exe"
if not defined PYC if exist "C:\Tools\radan_venv\Scripts\python.exe" set "PYC=C:\Tools\radan_venv\Scripts\python.exe"
if not defined PYC for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYC set "PYC=%%~fI"
if not defined PYC for /f "delims=" %%I in ('where py.exe 2^>nul') do if not defined PYC (
  set "PYC=%%~fI"
  set "PYC_ARGS=-3"
)
if exist "C:\Tools\.venv\Scripts\pythonw.exe" set "PYW=C:\Tools\.venv\Scripts\pythonw.exe"
if not defined PYW if exist "%APPDIR%\.venv\Scripts\pythonw.exe" set "PYW=%APPDIR%\.venv\Scripts\pythonw.exe"
if not defined PYW if exist "C:\Tools\radan_venv\Scripts\pythonw.exe" set "PYW=C:\Tools\radan_venv\Scripts\pythonw.exe"
if not defined PYW for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%~fI"
if not defined PYW (
  set "PYW=%PYC%"
  set "PYW_ARGS=%PYC_ARGS%"
)
set LOG=%APPDIR%\radan_kitter_launch.log
set DEV_HOT_RELOAD=1
set HOT_RELOAD_INTERVAL=0.6
set HOT_RELOAD_DEBOUNCE=5.0
set HOT_RELOAD_MIN_UPTIME=1.2
set HOT_RELOAD_DECISION_TIMEOUT=10.0
set FORCE_CONSOLE_SCREEN2=1
if not "%RADAN_KITTER_HOT_RELOAD%"=="" set DEV_HOT_RELOAD=%RADAN_KITTER_HOT_RELOAD%
if not "%RADAN_KITTER_HOT_INTERVAL%"=="" set HOT_RELOAD_INTERVAL=%RADAN_KITTER_HOT_INTERVAL%
if not "%RADAN_KITTER_HOT_DEBOUNCE%"=="" set HOT_RELOAD_DEBOUNCE=%RADAN_KITTER_HOT_DEBOUNCE%
if not "%RADAN_KITTER_HOT_MIN_UPTIME%"=="" set HOT_RELOAD_MIN_UPTIME=%RADAN_KITTER_HOT_MIN_UPTIME%
if not "%RADAN_KITTER_HOT_DECISION_TIMEOUT%"=="" set HOT_RELOAD_DECISION_TIMEOUT=%RADAN_KITTER_HOT_DECISION_TIMEOUT%
if not "%RADAN_KITTER_CONSOLE_SCREEN2%"=="" set FORCE_CONSOLE_SCREEN2=%RADAN_KITTER_CONSOLE_SCREEN2%

echo.
echo [RADAN Kitter] Starting launcher...
echo   APPDIR: %APPDIR%
echo   LOG:    %LOG%
echo.

cd /d "%APPDIR%" || exit /b 1

if "%FORCE_CONSOLE_SCREEN2%"=="1" (
  echo [RADAN Kitter] Moving console to screen 2...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%APPDIR%\move_console_to_screen2.ps1"
)

echo ===== %date% %time% ===== > "%LOG%"
echo BAT: %~f0 >> "%LOG%"
echo PythonW: %PYW% %PYW_ARGS% >> "%LOG%"
echo Python: %PYC% %PYC_ARGS% >> "%LOG%"
echo Args: %* >> "%LOG%"

if not defined PYC (
  echo.
  echo [RADAN Kitter] ERROR: Python executable not found.
  echo   Expected one of:
  echo   - C:\Tools\.venv\Scripts\python.exe
  echo   - %APPDIR%\.venv\Scripts\python.exe
  echo   - C:\Tools\radan_venv\Scripts\python.exe
  echo   Or python/py on PATH.
  echo.
  echo Install Python or create .venv at %APPDIR%\.venv
  pause
  endlocal & exit /b 9009
)
echo [RADAN Kitter] Python resolved:
echo   python : %PYC% %PYC_ARGS%
echo   pythonw: %PYW% %PYW_ARGS%
echo   args   : %*
echo [RADAN Kitter] Hot-reload settings:
echo   enabled          = %DEV_HOT_RELOAD%
echo   interval         = %HOT_RELOAD_INTERVAL%s
echo   debounce         = %HOT_RELOAD_DEBOUNCE%s
echo   min_uptime       = %HOT_RELOAD_MIN_UPTIME%s
echo   decision_timeout = %HOT_RELOAD_DECISION_TIMEOUT%s
echo.

rem Kill stale prior instances for this app path before relaunch.
echo [RADAN Kitter] Terminating stale RADAN Kitter Python processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { ($_.Name -match '^pythonw?\.exe$') -and (($_.CommandLine -like '*radan_kitter\\main.py*') -or ($_.CommandLine -like '*radan_kitter\\dev_hot_restart.py*')) }; " ^
  "foreach ($p in $procs) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {} }"

if "%DEV_HOT_RELOAD%"=="1" (
  echo [RADAN Kitter] Mode: HOT RELOAD
  echo   interval=%HOT_RELOAD_INTERVAL% debounce=%HOT_RELOAD_DEBOUNCE% min_uptime=%HOT_RELOAD_MIN_UPTIME% decision_timeout=%HOT_RELOAD_DECISION_TIMEOUT%
  echo [RADAN Kitter] Launching dev_hot_restart.py...
  echo MODE=hot_reload >> "%LOG%"
  echo HOT_RELOAD_INTERVAL=%HOT_RELOAD_INTERVAL% >> "%LOG%"
  echo HOT_RELOAD_DEBOUNCE=%HOT_RELOAD_DEBOUNCE% >> "%LOG%"
  echo HOT_RELOAD_MIN_UPTIME=%HOT_RELOAD_MIN_UPTIME% >> "%LOG%"
  echo HOT_RELOAD_DECISION_TIMEOUT=%HOT_RELOAD_DECISION_TIMEOUT% >> "%LOG%"
  "%PYC%" %PYC_ARGS% "%APPDIR%\dev_hot_restart.py" --interval %HOT_RELOAD_INTERVAL% --debounce %HOT_RELOAD_DEBOUNCE% --min-uptime %HOT_RELOAD_MIN_UPTIME% --decision-timeout %HOT_RELOAD_DECISION_TIMEOUT% %* >> "%LOG%" 2>&1
  set "EXITCODE=!ERRORLEVEL!"
  if not "!EXITCODE!"=="0" (
    echo.
    echo [RADAN Kitter] Hot reload launcher exited with code !EXITCODE!.
    echo [RADAN Kitter] See %LOG% for startup details.
    pause
  ) else (
    echo [RADAN Kitter] Hot reload launcher exited cleanly.
  )
  endlocal & exit /b !EXITCODE!
)

echo [RADAN Kitter] Mode: STABLE
echo [RADAN Kitter] Hot reload is disabled. Set RADAN_KITTER_HOT_RELOAD=1 to enable.
echo [RADAN Kitter] Launching main.py with pythonw...
echo MODE=stable >> "%LOG%"

"%PYW%" %PYW_ARGS% "%APPDIR%\main.py" %* >> "%LOG%" 2>&1

set HAD_ERROR=0
if errorlevel 1 set HAD_ERROR=1
findstr /I /C:"Traceback (most recent call last):" "%LOG%" >nul && set HAD_ERROR=1
findstr /I /C:"Fatal Python error" "%LOG%" >nul && set HAD_ERROR=1

if "%HAD_ERROR%"=="1" (
  echo.
  echo [RADAN Kitter] Launch failed. Log:
  type "%LOG%"
  echo.
  pause
)
if "%HAD_ERROR%"=="0" (
  echo [RADAN Kitter] Launch command completed.
)

endlocal
