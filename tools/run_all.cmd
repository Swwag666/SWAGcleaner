@echo off
rem Polygon run in guest. Usage:
rem   \\VBoxSvr\swagtools\run_all.cmd          - analysis + dry-run (safe)
rem   \\VBoxSvr\swagtools\run_all.cmd --purge  - with real deletion
rem
rem Everything (scripts, portable python, swagscan.exe, staging) is read from
rem host shared folders, so the guest keeps NO stale copies: the snapshot only
rem has to provide a working Windows, and the 2.4 GB of seeded junk is never
rem copied into the guest disk.
rem   \\VBoxSvr\swagtools  - scripts + portable python + swagscan.exe + core/
rem   \\VBoxSvr\seed       - staging (good/ junk/ seed_manifest.json)
rem
rem NOTE: keep this file pure ASCII - cmd.exe parses UTF-8 batch files wrong.
setlocal
set TOOLS=\\VBoxSvr\swagtools
set STAGING=\\VBoxSvr\seed
set PY=%TOOLS%\python\python.exe
set SWAGSCAN_BIN=%TOOLS%\swagscan.exe
set PYTHONIOENCODING=utf-8

echo === [0/5] shared folders ===
if not exist "%TOOLS%\run_all.cmd" (
    echo shared folder not mounted: %TOOLS%
    exit /b 1
)
if not exist "%STAGING%\seed_manifest.json" (
    echo shared folder not mounted: %STAGING%
    exit /b 1
)
echo tools: %TOOLS%
echo staging: %STAGING%

echo === [1/5] seed files to real paths ===
%PY% %TOOLS%\seed_apply.py %STAGING% || exit /b 1

echo === [2/5] snapshot BEFORE ===
%PY% %TOOLS%\verify_manifest.py before %STAGING%\applied.json C:\seed\before.json || exit /b 1

echo === [3/5] scan, category check and purge ===
%PY% %TOOLS%\run_vm_test.py %STAGING% %* || set TESTFAIL=1

echo === [4/5] snapshot AFTER ===
%PY% %TOOLS%\verify_manifest.py after %STAGING%\applied.json C:\seed\after.json || exit /b 1

echo === [5/5] verify good files (crc32 byte-exact) ===
%PY% %TOOLS%\verify_manifest.py check C:\seed\before.json C:\seed\after.json || set TESTFAIL=1

if defined TESTFAIL (echo RESULT: FAILURES PRESENT & exit /b 1)
echo RESULT: POLYGON CLEAN
