@echo off
REM Tella v1 setup script (Windows). Creates .venv, installs deps, hints at .env.
REM ASCII-only (no Vietnamese diacritics — cmd.exe parser is unreliable with them).

setlocal

echo === Tella SETUP ===
echo.

REM 1. Check Python 3.12+
where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: python not found on PATH. Install Python 3.12+ from https://www.python.org/
  exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 (
  echo ERROR: Python 3.12 or newer required.
  python --version
  exit /b 1
)

REM 2. Create .venv if missing
if not exist .venv (
  echo Creating .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo ERROR: failed to create .venv
    exit /b 1
  )
)

REM 3. Activate + install
echo Installing dependencies ...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -e .

REM 4. Hint about .env
if not exist .env (
  echo.
  echo NOTE: .env not found.
  echo Copy .env.example to .env and fill in your keys:
  echo   - GEMINI_API_KEY  (https://aistudio.google.com/apikey)
  echo   - CF_ACCOUNTS or CF_ACCOUNT_ID + CF_AI_TOKEN  (AI image mode)
  echo   - PEXELS_API_KEY  (stock photo / video mode)
  echo   - GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_TTS_API_KEY  (TTS)
)

echo.
echo === SETUP DONE ===
echo Run with: RUN.bat
endlocal
