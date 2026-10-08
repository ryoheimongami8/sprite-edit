@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "ESORA_INDEX=http://192.168.92.3:30503/simple/"
set "UV_BIN=%USERPROFILE%\.local\bin"
set "PATH=%UV_BIN%;%PATH%"

echo [1/5] Checking uv...
where uv >nul 2>&1
if errorlevel 1 (
    echo uv not found. Installing...
    powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    if errorlevel 1 goto :fail
)
where uv >nul 2>&1
if errorlevel 1 (
    echo uv still not found on PATH.
    goto :fail
)

echo [2/5] Installing / upgrading esora-api-cli...
uv tool install esora-api-cli@latest --index "%ESORA_INDEX%" --python 3.12
if errorlevel 1 goto :fail
esora-api upgrade

echo [3/5] Checking esora login...
esora-api me >nul 2>&1
if errorlevel 1 (
    echo Not signed in. Opening browser for login...
    esora-api auth login
    if errorlevel 1 goto :fail
)
esora-api me

for /f "delims=" %%i in ('where esora-api 2^>nul') do (
    if not defined ESORA_CLI_PATH set "ESORA_CLI_PATH=%%i"
)

echo [4/5] Preparing app environment...
cd /d "%~dp0app"
if not exist ".venv\Scripts\python.exe" (
    uv venv .venv --python 3.12
    if errorlevel 1 goto :fail
)
uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
if errorlevel 1 goto :fail

echo [5/5] Starting app...
".venv\Scripts\python.exe" app.py --open
if errorlevel 1 goto :fail
goto :end

:fail
echo.
echo ERROR: setup failed. See messages above.
pause
exit /b 1

:end
endlocal
