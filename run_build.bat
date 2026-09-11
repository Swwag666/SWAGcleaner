@echo off
rem Сборка SWAGcleaner в Windows-приложение.
rem
rem По умолчанию собирается один файл: dist\SWAGcleaner.exe
rem Запуск оконный — без чёрного окна консоли.
rem
rem Пока доводишь интерфейс, удобнее собирать папкой: она стартует заметно
rem быстрее, а пересборка идёт по кэшу.
rem     set SWAGCLEANER_ONEDIR=1
rem     run_build.bat
rem
rem Если что-то не запускается — проверь сборку без показа окна:
rem     dist\SWAGcleaner.exe --self-test

setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo venv не найден. Создай окружение:
    echo     python -m venv venv
    echo     venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
    pause
    exit /b 1
)

set PYTHON=%CD%\venv\Scripts\python.exe

echo === Сборка SWAGcleaner ===
echo Python: %PYTHON%
if "%SWAGCLEANER_ONEDIR%"=="1" (
    echo Режим:  папка ^(быстрый запуск^)
) else (
    echo Режим:  один файл
)
echo.

"%PYTHON%" -m PyInstaller --noconfirm --distpath dist --workpath build SWAGcleaner.spec
if errorlevel 1 (
    echo.
    echo Сборка упала с кодом %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)

echo.
if exist "dist\SWAGcleaner.exe" echo Готово: %CD%\dist\SWAGcleaner.exe
if exist "dist\SWAGcleaner\SWAGcleaner.exe" echo Готово: %CD%\dist\SWAGcleaner\SWAGcleaner.exe
echo.
echo Проверка: запусти приложение или прогони dist\SWAGcleaner.exe --self-test

endlocal
