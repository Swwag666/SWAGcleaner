@echo off
rem Сборка SWAGcleaner в один .exe
rem Требуется: Python + venv с установленными requirements.txt и requirements-dev.txt
rem Работает только в Windows с privileges для UPX (опционально).
rem
rem Использование: call run_build.bat [build_dir]
rem
rem Если вы хотите собрать без UPX, установите переменную окружения
rem PYINSTALLER_UPX=0
rem
rem Пример:
rem   call run_build.bat
rem   call run_build.bat dist

setlocal EnableDelayedExpansion
set BUILD_DIR=%1
if "%BUILD_DIR%"=="" set BUILD_DIR=.

set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%
if not exist "%PROJECT_ROOT%\venv\Scripts\python.exe" (
    echo venv не найден. Создайте: python -m venv venv
    echo Установите зависимости: venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
    pause
    exit /b 1
)

set PYTHON=%PROJECT_ROOT%\venv\Scripts\python.exe

set UPX_FLAG=-
echo === Building SWAGcleaner ===
echo Using: %PYTHON%
echo Project root: %PROJECT_ROOT%
echo Build dir: %BUILD_DIR%
echo.

"%PYTHON%" -m PyInstaller --name SWAGcleaner --onefile --console --collect-all PySide6 --collect-all shiboken6 --collect-all core --collect-all ui --collect-all services %UPX_FLAG% --distpath "%BUILD_DIR%\dist" --workpath "%BUILD_DIR%\build" --specpath "%BUILD_DIR%" swagcleaner.py

if %ERRORLEVEL% neq 0 (
    echo Build failed with error code %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo === Build complete ===
echo Exe: %BUILD_DIR%\dist\SWAGcleaner.exe
echo.

if exist "%BUILD_DIR%\dist\SWAGcleaner.exe" (
    echo OK: exe найден.
) else (
    echo WARNING: exe не найден.
)

endlocal
