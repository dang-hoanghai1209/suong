@echo off
REM Tella v1 run wrapper (Windows). Activates .venv then dispatches CLI args.
REM ASCII-only.

setlocal

if not exist .venv (
  echo .venv not found. Run SETUP.bat first.
  exit /b 1
)

call .venv\Scripts\activate.bat

REM Force UTF-8 stdout for Vietnamese diacritics on Windows
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

REM Default to the interactive wizard when no args supplied
if "%~1"=="" (
  python -m tella
) else (
  python -m tella %*
)

endlocal
