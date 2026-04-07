@echo off
:: dongbo/install_task.bat
:: Them auto_sync.py vao Windows Startup Folder
:: -> Tu dong chay khi dang nhap, khong hien cua so, khong can Admin
::
:: Chay 1 lan de cai dat. Double-click la duoc.
:: ================================================================

setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "SCRIPT=%SCRIPT_DIR%\auto_sync.py"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS=%STARTUP%\ESP32_AutoSync.vbs"
set "SHORTCUT=%STARTUP%\ESP32_AutoSync.lnk"

echo.
echo  ============================================
echo   ESP32 Auto-Sync -- Startup Setup
echo  ============================================
echo.

:: Kiem tra script ton tai
if not exist "%SCRIPT%" (
    echo [ERR] Khong tim thay: %SCRIPT%
    pause & exit /b 1
)

:: Tim Python
for /f "tokens=*" %%i in ('where python 2^>nul') do (
    set "PYTHON_EXE=%%i"
    goto :found_python
)
echo [ERR] Khong tim thay Python trong PATH.
pause & exit /b 1

:found_python
echo [OK] Python : %PYTHON_EXE%
echo [OK] Script : %SCRIPT%
echo [OK] Startup: %STARTUP%
echo.

:: Xoa file cu neu co
if exist "%VBS%" del /F /Q "%VBS%"
if exist "%SHORTCUT%" del /F /Q "%SHORTCUT%"

:: Tao file .vbs de chay Python an (khong hien cua so CMD)
:: wscript.exe chay .vbs -> WScript.Shell.Run -> an hoan toan
(
echo Set WshShell = CreateObject^("WScript.Shell"^)
echo WshShell.Run Chr^(34^) ^& "%PYTHON_EXE%" ^& Chr^(34^) ^& " " ^& Chr^(34^) ^& "%SCRIPT%" ^& Chr^(34^), 0, False
echo Set WshShell = Nothing
) > "%VBS%"

if exist "%VBS%" (
    echo [OK] Da tao: %VBS%
    echo.
    echo  auto_sync.py se tu dong chay an khi dang nhap Windows.
    echo  Khi bat WiFi ESP32-Node-1 hoac ESP32-Node-2 se tu dong sync.
    echo.
    echo  Quan ly:
    echo    Xoa startup: del "%VBS%"
    echo    Xem process: tasklist ^| findstr python
    echo    Dung script: taskkill /F /FI "WINDOWTITLE eq ESP32*"
    echo.
) else (
    echo [ERR] Tao file that bai. Kiem tra quyen ghi vao Startup folder.
    pause & exit /b 1
)

:: Hoi co muon mo Startup folder khong
set /p "OPEN_FOLDER=Mo thu muc Startup de kiem tra? (y/n): "
if /i "%OPEN_FOLDER%"=="y" explorer "%STARTUP%"

:: Hoi co muon chay ngay khong
set /p "RUN_NOW=Chay auto_sync.py ngay bay gio? (y/n): "
if /i "%RUN_NOW%"=="y" (
    echo.
    echo [INFO] Dang chay auto_sync.py ... ^(Ctrl+C de dung^)
    echo.
    "%PYTHON_EXE%" "%SCRIPT%"
)

echo.
echo  Hoan tat! Lan dang nhap tiep theo se tu dong sync ESP32.
pause
