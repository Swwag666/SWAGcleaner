@echo off
rem Polygon run in guest. Usage:
rem   C:\tools\run_all.cmd          - analysis + dry-run (safe)
rem   C:\tools\run_all.cmd --purge  - with real deletion
rem NOTE: keep this file pure ASCII - cmd.exe parses UTF-8 batch files wrong.
setlocal
set PY=C:\tools\python\python.exe
set SWAGSCAN_BIN=C:\tools\swagscan.exe
set PYTHONIOENCODING=utf-8

echo === [1/5] seed files to real paths ===
%PY% C:\tools\seed_apply.py C:\seed || exit /b 1

echo === [2/5] snapshot BEFORE ===
%PY% C:\tools\verify_manifest.py before C:\seed\applied.json C:\seed\before.json || exit /b 1

echo === [3/5] scan, category check and purge ===
%PY% C:\tools\run_vm_test.py C:\seed %* || set TESTFAIL=1

echo === [4/5] snapshot AFTER ===
%PY% C:\tools\verify_manifest.py after C:\seed\applied.json C:\seed\after.json || exit /b 1

echo === [5/5] verify good files (crc32 byte-exact) ===
%PY% C:\tools\verify_manifest.py check C:\seed\before.json C:\seed\after.json || set TESTFAIL=1

if defined TESTFAIL (echo RESULT: FAILURES PRESENT & exit /b 1)
echo RESULT: POLYGON CLEAN
