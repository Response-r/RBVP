@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem 处理 ROOT 路径，去除末尾的反斜杠，防止产生 \" 转义问题
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"

set "PYTHON=%ROOT_DIR%\venv\Scripts\python.exe"
set "PYINSTALLER=%ROOT_DIR%\venv\Scripts\pyinstaller.exe"
set "ICON=%ROOT_DIR%\assets\RBVP.ico"

echo.
echo ============================================================
echo                    RBVP BUILD
echo ============================================================
echo.

if not exist "%ROOT_DIR%\main.py" (
    echo [ERROR] main.py not found:
    echo %ROOT_DIR%\main.py
    pause
    exit /b 1
)

if not exist "%PYTHON%" (
    echo [ERROR] Project venv Python not found:
    echo %PYTHON%
    pause
    exit /b 1
)

if not exist "%ICON%" (
    echo [ERROR] RBVP.ico not found:
    echo %ICON%
    pause
    exit /b 1
)

if not exist "%PYINSTALLER%" (
    echo [INFO] PyInstaller not found. Installing...
    "%PYTHON%" -m pip install --upgrade pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

echo [CLEAN] Removing old build and dist...
if exist "%ROOT_DIR%\build" rmdir /s /q "%ROOT_DIR%\build"
if exist "%ROOT_DIR%\dist" rmdir /s /q "%ROOT_DIR%\dist"

echo.

echo [BUILD] Running PyInstaller...
echo [ICON]  %ICON%
echo.

rem 修复后的打包命令：使用无末尾斜杠的 %ROOT_DIR%
"%PYINSTALLER%" "%ROOT_DIR%\main.py" --clean --noconfirm --onefile --windowed --name "RBVP" --icon "%ICON%" --paths "%ROOT_DIR%" --collect-submodules "app" --collect-submodules "core" --collect-submodules "ui" --hidden-import "app" --hidden-import "core" --hidden-import "ui" --add-data "%ROOT_DIR%\assets;assets"

if errorlevel 1 (
    echo.
    echo ============================================================
    echo                    BUILD FAILED
    echo ============================================================
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo                  BUILD SUCCESSFUL
echo ============================================================
echo.
echo EXE:
echo %ROOT_DIR%\dist\RBVP.exe
echo.
echo Icon:
echo %ICON%
echo.
pause
exit /b 0