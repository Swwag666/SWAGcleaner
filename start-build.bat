@echo off
chcp 866 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

rem ===========================================================================
rem  SWAGcleaner :: start-build.bat
rem
rem  Один запуск - вся сборка целиком. Тулчейн находится сам, а если его нет -
rem  ставится сам. Вмешиваться не нужно: батник доводит дело до dist\*.exe.
rem
rem  Этапы:
rem    0. разбор ключей, обновление PATH из реестра
rem    1. детект компилятора C/C++ (MSVC через vswhere) - выбор тулчейна Rust
rem    2. Python 3.10-3.14 x64: поиск, иначе winget, иначе установщик python.org
rem    3. venv + requirements.txt + requirements-dev.txt (с повтором при обрыве)
rem    4. Rust: поиск cargo, иначе rustup-init (winget как запасной путь)
rem    5. cargo build --release  ->  swagscan.exe
rem    6. контрольный --self-test на исходниках (ловит пропавшие ассеты до упаковки)
rem    7. PyInstaller по SWAGcleaner.spec  ->  dist\
rem    8. проверка артефакта: размер, SHA256
rem
rem  Ключи:
rem    start-build.bat                один файл dist\SWAGcleaner.exe
rem    start-build.bat --onedir       сборка папкой dist\SWAGcleaner\ (быстрый старт)
rem    start-build.bat --clean        снести venv, build, dist и собрать с нуля
rem    start-build.bat --skip-rust    не собирать ядро, взять готовый swagscan.exe
rem    start-build.bat --selftest     после сборки запустить exe --self-test (нужен UAC)
rem    start-build.bat --verify       доп. сборка без UAC и её self-test - без повышения
rem    start-build.bat --tests        прогнать pytest и cargo test перед сборкой
rem    start-build.bat --no-pause     не ждать клавишу в конце
rem    start-build.bat --help         эта справка
rem
rem  Переменная окружения SWAGCLEANER_ONEDIR=1 равносильна --onedir.
rem  Отдельная ловушка: swagcleaner.py при старте зовёт SetConsoleOutputCP(65001)
rem  и SetConsoleCP(65001), чтобы собственный русский вывод приложения не бился
rem  в cp437-консоли. Кодовая страница общая на консоль, а не на процесс, поэтому
rem  после ЛЮБОГО запуска swagcleaner.py - прямого или через импорт в pytest - cmd
rem  начинает читать этот CP866-файл как UTF-8, и кириллица рассыпается в "??".
rem  Значит 866 переустанавливается в каждой подпрограмме вывода и сразу после
rem  каждого такого запуска. QApplication сама по себе страницу не трогает.
rem
rem  ВАЖНО про кодировку: файл сохранён в CP866, а не в UTF-8. cmd.exe читает
rem  батник побайтово и на UTF-8 попадает в середину многобайтовых символов,
rem  из-за чего строки обрезаются и парсер ломается. При правках держи CP866.
rem ===========================================================================

set "ROOT=%CD%"
set "VENV=%ROOT%\venv"
set "VPY=%VENV%\Scripts\python.exe"
set "SPEC=%ROOT%\SWAGcleaner.spec"
set "RUST_DIR=%ROOT%\rust\swagscan"
set "ENTRY=%ROOT%\swagcleaner.py"
set "SELFTEST_TXT=%TEMP%\swagcleaner_selftest.txt"

rem Python по умолчанию пишет в перенаправленный stdout UTF-8, а батник работает
rem в CP866 - без этого лог получается вперемешку.
set "PYTHONIOENCODING=cp866:replace"
set "PYTHONUTF8=0"

rem Порядок перебора интерпретаторов: сначала то, что заявлено в README.
set "PY_ORDER=3.12 3.13 3.14 3.11 3.10"
set "PY_WINGET=Python.Python.3.12"
set "PY_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
set "RUSTUP_URL=https://win.rustup.rs/x86_64"

set "MODE=onefile"
if "%SWAGCLEANER_ONEDIR%"=="1" set "MODE=onedir"

set "DO_CLEAN=0"
set "DO_SKIP_RUST=0"
set "DO_SELFTEST=0"
set "DO_TESTS=0"
set "DO_VERIFY=0"
set "DO_PAUSE=1"
set "ARGS=%*"
if defined ARGS for %%A in (%ARGS%) do (
    if /i "%%~A"=="--help"       goto :print_help
    if /i "%%~A"=="-h"           goto :print_help
    if /i "%%~A"=="--clean"      set "DO_CLEAN=1"
    if /i "%%~A"=="--onedir"     set "MODE=onedir"
    if /i "%%~A"=="--onefile"    set "MODE=onefile"
    if /i "%%~A"=="--skip-rust"  set "DO_SKIP_RUST=1"
    if /i "%%~A"=="--selftest"   set "DO_SELFTEST=1"
    if /i "%%~A"=="--tests"      set "DO_TESTS=1"
    if /i "%%~A"=="--verify"     set "DO_VERIFY=1"
    if /i "%%~A"=="--no-pause"   set "DO_PAUSE=0"
)

call :stamp T0
echo.
echo  ============================================================
echo   SWAGcleaner - сборка
echo   режим: %MODE%
echo   корень: %ROOT%
echo  ============================================================

if "%DO_CLEAN%"=="1" (
    call :banner "чистка старых артефактов"
    if exist "%VENV%"       rmdir /s /q "%VENV%"
    if exist "%ROOT%\build" rmdir /s /q "%ROOT%\build"
    if exist "%ROOT%\dist"          rmdir /s /q "%ROOT%\dist"
    if exist "%ROOT%\dist_verify"   rmdir /s /q "%ROOT%\dist_verify"
    if exist "%ROOT%\build_verify"  rmdir /s /q "%ROOT%\build_verify"
)

rem --- Этап 0: PATH ------------------------------------------------------------
call :banner "этап 0/8 - обновляю PATH из реестра"
call :refresh_path
echo    готово

rem --- Этап 1: компилятор C/C++ ------------------------------------------------
call :banner "этап 1/8 - ищу компилятор C/C++"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
set "VSPATH="
if exist "%VSWHERE%" for /f "usebackq delims=" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2^>nul`) do set "VSPATH=%%I"

set "VCVARS="
set "VCVARS_ARG="
set "RUST_HOST=x86_64-pc-windows-msvc"
if defined VSPATH (
    if exist "%VSPATH%\VC\Auxiliary\Build\vcvars64.bat" (
        set "VCVARS=%VSPATH%\VC\Auxiliary\Build\vcvars64.bat"
        set "VCVARS_ARG="
    ) else if exist "%VSPATH%\VC\Auxiliary\Build\vcvarsall.bat" (
        set "VCVARS=%VSPATH%\VC\Auxiliary\Build\vcvarsall.bat"
        set "VCVARS_ARG=amd64"
    )
)
if defined VCVARS (
    echo    MSVC найден: %VSPATH%
    echo    тулчейн Rust: msvc
) else (
    set "RUST_HOST=x86_64-pc-windows-gnu"
    echo    MSVC не найден - беру самодостаточный GNU тулчейн
    echo    тулчейн Rust: gnu
)

rem --- Этап 2: Python ----------------------------------------------------------
call :banner "этап 2/8 - ищу Python 3.10-3.14 x64"
set "PYBASE="
for %%V in (%PY_ORDER%) do if not defined PYBASE call :probe_py %%V
if not defined PYBASE call :probe_plain_python
if not defined PYBASE (
    echo    Python не найден - ставлю автоматически
    call :install_python
    if errorlevel 1 (
        set "FAILMSG=Python поставить не удалось. Поставь вручную: winget install %PY_WINGET%"
        goto :fail
    )
    call :refresh_path
    for %%V in (%PY_ORDER%) do if not defined PYBASE call :probe_py %%V
    if not defined PYBASE call :probe_plain_python
)
if not defined PYBASE (
    set "FAILMSG=подходящий Python 3.10-3.14 x64 так и не появился"
    goto :fail
)
echo    интерпретатор: %PYBASE%
for /f "delims=" %%V in ('"%PYBASE%" --version 2^>^&1') do echo    версия: %%V

rem --- Этап 3: venv и зависимости ---------------------------------------------
call :banner "этап 3/8 - venv и зависимости"
if not exist "%VPY%" (
    echo    создаю venv
    "%PYBASE%" -m venv "%VENV%"
    if errorlevel 1 (
        set "FAILMSG=не удалось создать venv"
        goto :fail
    )
)
if not exist "%VPY%" (
    set "FAILMSG=venv создан, но %VPY% отсутствует"
    goto :fail
)

echo    обновляю pip, setuptools, wheel
"%VPY%" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel >nul 2>&1

set "DEPS_OK=0"
for /L %%N in (1,1,3) do if "!DEPS_OK!"=="0" (
    echo    попытка %%N: ставлю зависимости из requirements
    "%VPY%" -m pip install --disable-pip-version-check -r "%ROOT%\requirements.txt" -r "%ROOT%\requirements-dev.txt"
    if not errorlevel 1 set "DEPS_OK=1"
    if "!DEPS_OK!"=="0" (
        echo    не вышло - жду 5 секунд и повторяю
        timeout /t 5 /nobreak >nul
    )
)
if "%DEPS_OK%"=="0" (
    set "FAILMSG=зависимости не установились после трёх попыток - смотри вывод pip выше"
    goto :fail
)

echo    проверяю импорты
"%VPY%" -c "import PySide6, psutil, PIL, imagehash; from PySide6 import QtWidgets; print('    PySide6', PySide6.__version__)"
if errorlevel 1 (
    set "FAILMSG=PySide6 или соседние пакеты не импортируются в venv"
    goto :fail
)
"%VPY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    set "FAILMSG=PyInstaller не установлен"
    goto :fail
)
for /f "delims=" %%V in ('"%VPY%" -m PyInstaller --version 2^>nul') do echo    PyInstaller %%V

rem --- Этап 4: Rust ------------------------------------------------------------
call :banner "этап 4/8 - ищу Rust"
set "CARGO="
where cargo >nul 2>&1 && set "CARGO=cargo"
if not defined CARGO if exist "%USERPROFILE%\.cargo\bin\cargo.exe" set "CARGO=%USERPROFILE%\.cargo\bin\cargo.exe"
if not defined CARGO (
    echo    Rust не найден - ставлю rustup автоматически
    call :install_rust
    if errorlevel 1 (
        set "FAILMSG=rustup не удалось поставить автоматически"
        goto :fail
    )
    call :refresh_path
    where cargo >nul 2>&1 && set "CARGO=cargo"
    if not defined CARGO if exist "%USERPROFILE%\.cargo\bin\cargo.exe" set "CARGO=%USERPROFILE%\.cargo\bin\cargo.exe"
)
if not defined CARGO (
    set "FAILMSG=cargo так и не появился в PATH"
    goto :fail
)
for /f "delims=" %%V in ('"%CARGO%" --version 2^>nul') do echo    %%V
for /f "delims=" %%V in ('rustc --version 2^>nul') do echo    %%V

rem Гарантируем наличие нужного тулчейна, а не надеемся на default.
set "CARGO_TC="
if /i "%RUST_HOST%"=="x86_64-pc-windows-gnu" (
    rustup toolchain list 2>nul | findstr /i "x86_64-pc-windows-gnu" >nul
    if errorlevel 1 (
        echo    докачиваю GNU тулчейн
        rustup toolchain install stable-x86_64-pc-windows-gnu --profile minimal
        if errorlevel 1 (
            set "FAILMSG=не удалось поставить GNU тулчейн Rust"
            goto :fail
        )
    )
    set "CARGO_TC=+stable-x86_64-pc-windows-gnu"
) else (
    rustup toolchain list 2>nul | findstr /i "x86_64-pc-windows-msvc" >nul
    if errorlevel 1 (
        echo    докачиваю MSVC тулчейн
        rustup toolchain install stable-x86_64-pc-windows-msvc --profile minimal
        if errorlevel 1 (
            set "FAILMSG=не удалось поставить MSVC тулчейн Rust"
            goto :fail
        )
    )
)

rem --- Этап 5: сборка ядра -----------------------------------------------------
call :banner "этап 5/8 - Rust ядро swagscan"
if "%DO_SKIP_RUST%"=="1" (
    echo    пропуск сборки по --skip-rust
) else (
    if defined VCVARS (
        echo    поднимаю окружение MSVC
        call "%VCVARS%" %VCVARS_ARG% >nul 2>&1
        where link.exe >nul 2>&1
        if errorlevel 1 echo    внимание: link.exe не виден, линковка MSVC может упасть
    )
    pushd "%RUST_DIR%"
    if defined CARGO_TC (
        "%CARGO%" %CARGO_TC% build --release --target x86_64-pc-windows-gnu
    ) else (
        "%CARGO%" build --release
    )
    if errorlevel 1 (
        popd
        set "FAILMSG=cargo build --release упал - смотри вывод выше"
        goto :fail
    )
    popd
    echo    ядро собрано
)

set "SWAGSCAN_EXE="
if exist "%RUST_DIR%\target\release\swagscan.exe" set "SWAGSCAN_EXE=%RUST_DIR%\target\release\swagscan.exe"
if not defined SWAGSCAN_EXE if exist "%RUST_DIR%\target\x86_64-pc-windows-gnu\release\swagscan.exe" set "SWAGSCAN_EXE=%RUST_DIR%\target\x86_64-pc-windows-gnu\release\swagscan.exe"
if not defined SWAGSCAN_EXE if exist "%ROOT%\bin\swagscan.exe" set "SWAGSCAN_EXE=%ROOT%\bin\swagscan.exe"
if not defined SWAGSCAN_EXE for /f "delims=" %%F in ('dir /b /s "%RUST_DIR%\target\swagscan.exe" 2^>nul') do if not defined SWAGSCAN_EXE set "SWAGSCAN_EXE=%%F"

if defined SWAGSCAN_EXE (
    for %%F in ("!SWAGSCAN_EXE!") do echo    ядро: %%~F  [%%~zF байт]
) else (
    echo    ВНИМАНИЕ: swagscan.exe не найден
    echo    сборка уедет без Rust-ядра - приложение это переживёт,
    echo    мост core\swagscan.py откатится на Python-обход
)

rem --- Этап 6: контроль исходников --------------------------------------------
call :banner "этап 6/8 - контрольный self-test исходников"
set "QT_QPA_PLATFORM=offscreen"
"%VPY%" "%ENTRY%" --self-test >nul 2>&1
if errorlevel 1 (
    set "QT_QPA_PLATFORM="
    echo    self-test исходников провалился, показываю отчёт
    call :showreport "%SELFTEST_TXT%"
    set "FAILMSG=исходники не проходят self-test - упаковывать нечего"
    goto :fail
)
set "QT_QPA_PLATFORM="
rem self-test только что запустил swagcleaner.py, а он переключил консоль
rem в CP 65001. Возвращаем 866 до разбора следующей строки с кириллицей.
chcp 866 >nul
echo    self-test исходников: OK

rem --tests - это явный запрос на гейт, а не на справку: упавший тест
rem останавливает сборку. Без флага поведение прежнее - тесты не гоняются.
if "%DO_TESTS%"=="1" (
    call :banner "прогоняю тесты по --tests"
    rem --basetemp обязателен. На общем %TEMP%\pytest-of-<user> pytest держит
    rem симлинк pytest-current и удаляет его через pathlib.unlink, что на
    rem каталожном симлинке даёт WinError 5 - и pytest падает в sessionfinish
    rem уже ПОСЛЕ успешных тестов. Со своим basetemp эта механика не включается.
    "%VPY%" -m pytest -q --basetemp "%TEMP%\swagcleaner-pytest"
    if errorlevel 1 (
        set "FAILMSG=pytest вернул ненулевой код: упавшие тесты или сбой самого pytest"
        goto :fail
    )
    pushd "%RUST_DIR%"
    if defined CARGO_TC ( "%CARGO%" %CARGO_TC% test --release ) else ( "%CARGO%" test --release )
    set "TRC=!errorlevel!"
    popd
    if not "!TRC!"=="0" (
        set "FAILMSG=cargo test провалился - сборку останавливаю"
        goto :fail
    )
)
rem pytest импортирует swagcleaner, а тот на импорте ставит консоли CP 65001
rem через SetConsoleOutputCP/SetConsoleCP. Страница общая на консоль, а не на
rem процесс, поэтому после тестов она остаётся 65001. Вернуть 866 надо ДО разбора
rem следующей строки с кириллицей: cmd декодирует аргумент call в момент чтения
rem строки, и chcp 866 внутри :banner помогает уже слишком поздно - без этой
rem строки баннер этапа 7 уезжает в "??" .
if "%DO_TESTS%"=="1" chcp 866 >nul

rem --- Этап 7: PyInstaller -----------------------------------------------------
call :banner "этап 7/8 - PyInstaller"
if "%MODE%"=="onedir" ( set "SWAGCLEANER_ONEDIR=1" ) else ( set "SWAGCLEANER_ONEDIR=" )
"%VPY%" -m PyInstaller --noconfirm --distpath "%ROOT%\dist" --workpath "%ROOT%\build" "%SPEC%"
if errorlevel 1 (
    set "FAILMSG=PyInstaller упал - смотри вывод выше"
    goto :fail
)

rem --- Этап 8: проверка артефакта ---------------------------------------------
call :banner "этап 8/8 - проверка артефакта"
set "ARTIFACT="
if exist "%ROOT%\dist\SWAGcleaner.exe" set "ARTIFACT=%ROOT%\dist\SWAGcleaner.exe"
if exist "%ROOT%\dist\SWAGcleaner\SWAGcleaner.exe" set "ARTIFACT=%ROOT%\dist\SWAGcleaner\SWAGcleaner.exe"
if not defined ARTIFACT (
    set "FAILMSG=PyInstaller отработал, но exe в dist не появился"
    goto :fail
)

set "ART_SIZE=0"
for %%F in ("%ARTIFACT%") do set "ART_SIZE=%%~zF"
set /a ART_MB=%ART_SIZE% / 1048576
set "ART_SHA="
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -LiteralPath '%ARTIFACT%' -Algorithm SHA256).Hash" 2^>nul`) do set "ART_SHA=%%H"

echo.
echo  ============================================================
echo   ГОТОВО
echo   артефакт : %ARTIFACT%
echo   размер   : %ART_SIZE% байт  ~%ART_MB% МБ
if defined ART_SHA echo   SHA256   : %ART_SHA%
if defined SWAGSCAN_EXE echo   ядро     : %SWAGSCAN_EXE%
echo  ============================================================
echo.

if "%DO_SELFTEST%"=="1" (
    call :banner "запускаю self-test собранного exe - потребуется UAC"
    if exist "%SELFTEST_TXT%" del /q "%SELFTEST_TXT%"
    "%ARTIFACT%" --self-test
    timeout /t 3 /nobreak >nul
    call :showreport "%SELFTEST_TXT%"
) else (
    echo  Проверка собранного exe - потребует UAC:
    echo      "%ARTIFACT%" --self-test
    echo  Отчёт ляжет в %SELFTEST_TXT%
    echo  Или запусти сборку с ключом --selftest, чтобы проверить сразу.
    echo.
)

if "%DO_VERIFY%"=="1" (
    call :verify_frozen
    if errorlevel 1 (
        set "FAILMSG=верификация замороженной сборки провалилась - отчёт выше"
        goto :fail
    )
)

call :stamp T1
call :duration
echo.
if "%DO_PAUSE%"=="1" pause
endlocal
exit /b 0

rem ============================== ПОДПРОГРАММЫ =================================

:banner
chcp 866 >nul
echo.
echo --- %~1 ---------------------------------------------------------
exit /b 0

:showreport
chcp 866 >nul
rem Отчёт self-test лежит в UTF-8; печатаем его через Python, чтобы консоль
rem в CP866 не превратила текст в кракозябры.
if not exist "%~1" (
    echo    отчёт %~1 не найден
    exit /b 1
)
"%VPY%" -c "import sys,pathlib;sys.stdout.write(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))" "%~1"
echo.
exit /b 0

:probe_py
rem %1 - версия вида 3.12. Годится только 64-битный интерпретатор 3.10-3.14.
py -%~1 -c "import sys,struct;sys.exit(0 if (struct.calcsize('P')*8==64 and (3,10)<=sys.version_info[:2]<(3,15)) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
for /f "usebackq delims=" %%P in (`py -%~1 -c "import sys;print(sys.executable)" 2^>nul`) do set "PYBASE=%%P"
exit /b 0

:probe_plain_python
where python >nul 2>&1
if errorlevel 1 exit /b 1
python -c "import sys,struct;sys.exit(0 if (struct.calcsize('P')*8==64 and (3,10)<=sys.version_info[:2]<(3,15)) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
for /f "usebackq delims=" %%P in (`python -c "import sys;print(sys.executable)" 2^>nul`) do set "PYBASE=%%P"
exit /b 0

:install_python
rem Путь 1: winget. Путь 2: установщик с python.org, тихая установка на юзера.
where winget >nul 2>&1
if not errorlevel 1 (
    echo    ставлю через winget: %PY_WINGET%
    winget install --id %PY_WINGET% -e --scope user --silent --accept-source-agreements --accept-package-agreements
    if not errorlevel 1 exit /b 0
    echo    winget не справился, пробую установщик напрямую
)
where curl >nul 2>&1
if errorlevel 1 (
    echo    нет ни winget, ни curl - ставить Python нечем
    exit /b 1
)
echo    качаю установщик Python с python.org
curl -L --fail --retry 3 -o "%TEMP%\swag_python_setup.exe" "%PY_URL%"
if errorlevel 1 (
    echo    скачать установщик не удалось
    exit /b 1
)
echo    тихая установка на текущего пользователя
"%TEMP%\swag_python_setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 Include_pip=1
set "PR=!errorlevel!"
del /q "%TEMP%\swag_python_setup.exe" >nul 2>&1
if not "!PR!"=="0" (
    echo    установщик Python вернул код !PR!
    exit /b 1
)
exit /b 0

:install_rust
rem Путь 1: rustup-init напрямую. Путь 2: winget.
where curl >nul 2>&1
if not errorlevel 1 (
    echo    качаю rustup-init
    curl -L --fail --retry 3 -o "%TEMP%\rustup-init.exe" "%RUSTUP_URL%"
    if not errorlevel 1 (
        echo    ставлю rustup, host=%RUST_HOST%
        "%TEMP%\rustup-init.exe" -y --profile minimal --default-host %RUST_HOST% --default-toolchain stable
        set "RR=!errorlevel!"
        del /q "%TEMP%\rustup-init.exe" >nul 2>&1
        if "!RR!"=="0" exit /b 0
        echo    rustup-init вернул !RR!, пробую winget
    ) else (
        echo    скачать rustup-init не удалось, пробую winget
    )
)
where winget >nul 2>&1
if errorlevel 1 (
    echo    нет ни curl, ни winget - ставить Rust нечем
    exit /b 1
)
winget install --id Rustlang.Rustup -e --silent --accept-source-agreements --accept-package-agreements
if errorlevel 1 exit /b 1
exit /b 0

:refresh_path
rem Перечитать PATH из реестра: после установок текущая сессия его не видит.
set "NEWPATH="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')" 2^>nul`) do set "NEWPATH=%%P"
if defined NEWPATH set "PATH=!NEWPATH!"
if exist "%USERPROFILE%\.cargo\bin" set "PATH=!PATH!;%USERPROFILE%\.cargo\bin"
exit /b 0

:stamp
set "%~1="
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()" 2^>nul`) do set "%~1=%%T"
exit /b 0

:duration
chcp 866 >nul
if not defined T0 exit /b 0
if not defined T1 exit /b 0
set /a D_SEC=%T1% - %T0%
set /a D_MIN=%D_SEC% / 60
set /a D_REM=%D_SEC% %% 60
echo  время сборки: %D_MIN% мин %D_REM% сек
exit /b 0

:fail
chcp 866 >nul
echo.
echo  ============================================================
echo   СБОЙ: %FAILMSG%
echo  ============================================================
call :stamp T1
call :duration
echo.
if "%DO_PAUSE%"=="1" pause
endlocal
exit /b 1

:verify_frozen
call :banner "верификация замороженной сборки, без UAC"
call :make_verify_spec
if errorlevel 1 exit /b 1
echo    собираю вариант без uac_admin и с консолью
"%VPY%" -m PyInstaller --noconfirm --distpath "%ROOT%\dist_verify" --workpath "%ROOT%\build_verify" "%ROOT%\SWAGcleaner-verify.spec"
if errorlevel 1 (
    echo    верификационная сборка упала
    exit /b 1
)
if not exist "%ROOT%\dist_verify\SWAGcleaner.exe" (
    echo    верификационный exe не появился
    exit /b 1
)
if exist "%SELFTEST_TXT%" del /q "%SELFTEST_TXT%"
set "QT_QPA_PLATFORM=offscreen"
"%ROOT%\dist_verify\SWAGcleaner.exe" --self-test >nul 2>&1
set "VRC=!errorlevel!"
set "QT_QPA_PLATFORM="
chcp 866 >nul
if not exist "%SELFTEST_TXT%" (
    echo    отчёт не появился - exe не доработал до записи
    del /q "%ROOT%\SWAGcleaner-verify.spec" >nul 2>&1
    exit /b 1
)
echo    отчёт замороженной сборки:
call :showreport "%SELFTEST_TXT%"
del /q "%ROOT%\SWAGcleaner-verify.spec" >nul 2>&1
if not "!VRC!"=="0" (
    echo    замороженная сборка вернула код !VRC!
    exit /b 1
)
exit /b 0

:make_verify_spec
rem Копия spec без uac_admin и с консолью. Такой exe стартует без повышения и
rem печатает трейсбеки в stderr - иначе проверить замороженную сборку из
rem автоматизации нельзя: боевой exe требует администратора.
"%VPY%" -c "import pathlib;s=pathlib.Path('SWAGcleaner.spec').read_text(encoding='utf-8');s=s.replace('uac_admin=True','uac_admin=False').replace('console=False','console=True');pathlib.Path('SWAGcleaner-verify.spec').write_text(s,encoding='utf-8')"
if errorlevel 1 (
    echo    не удалось сделать верификационный spec
    exit /b 1
)
echo    SWAGcleaner-verify.spec создан
exit /b 0

:print_help
chcp 866 >nul
echo.
echo  start-build.bat - полная сборка SWAGcleaner одним запуском
echo.
echo  Ключи:
echo    --onedir      сборка папкой dist\SWAGcleaner\ вместо одного exe
echo    --onefile     один файл dist\SWAGcleaner.exe - поведение по умолчанию
echo    --clean       удалить venv, build, dist и собрать заново
echo    --skip-rust   не пересобирать Rust-ядро, взять готовый swagscan.exe
echo    --selftest    запустить собранный exe --self-test, потребует UAC
echo    --verify      собрать вариант без UAC и прогнать его self-test
echo                  единственный способ проверить артефакт без повышения
echo    --tests       прогнать pytest и cargo test перед упаковкой
echo    --no-pause    не ждать клавишу в конце
echo    --help        эта справка
echo.
echo  Переменная окружения SWAGCLEANER_ONEDIR=1 равносильна --onedir.
echo.
echo  Батник сам ставит Python, Rust и зависимости, если их нет:
echo    Python  winget, затем установщик с python.org
echo    Rust    rustup-init с win.rustup.rs, затем winget
echo    MSVC    если Visual Studio Build Tools нет, собирается GNU тулчейн
echo.
endlocal
exit /b 0
