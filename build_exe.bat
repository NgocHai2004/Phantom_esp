@echo off
echo ============================================
echo  PHANTOM - Build EXE
echo ============================================

:: Check / install PyInstaller
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [*] Installing PyInstaller...
    pip install pyinstaller
)

:: Remove incompatible pathlib backport if present
pip show pathlib >nul 2>&1
if not errorlevel 1 (
    echo [*] Removing incompatible 'pathlib' backport...
    pip uninstall pathlib -y
)

:: Check / install dependencies
echo [*] Installing dependencies...
pip install customtkinter cryptography zstandard

:: Build
echo [*] Building PHANTOM.exe ...
pyinstaller --onefile --windowed ^
  --name PHANTOM ^
  --add-data "project_nen/zipfolder;project_nen/zipfolder" ^
  en_de.py

echo.
if exist dist\PHANTOM.exe (
    echo [OK] Build SUCCESS!
    echo      File: dist\PHANTOM.exe
    echo      Copy this file to any Windows PC - no Python needed.
) else (
    echo [!!] Build FAILED - check errors above.
)
echo ============================================
pause
