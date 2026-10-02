<#
.SYNOPSIS
    Проверяет, что файлы сборки не побиты кодировкой, и что тракт запуска жив.

.DESCRIPTION
    Дешёвая проверка (секунды), которая стоит перед долгой сборкой и спасает от
    самой злой поломки в этом проекте.

    start-build.bat содержит русские строки и ОБЯЗАН лежать в рабочем дереве в
    CP866+CRLF. cmd.exe читает батник побайтово, но позицию в файле отслеживает в
    символах, поэтому на UTF-8 он попадает внутрь многобайтовой последовательности
    и начинает исполнять мусор - в лог сыплются ошибки вида
    "'ропавшие' is not recognized as an internal or external command".
    Достаточно один раз открыть файл в редакторе и нажать «сохранить».
    .gitattributes это запрещает, а этот скрипт ловит нарушение до того, как
    сгорит десять минут основной сборки.

    Второй файл - .github/scripts/run-build.ps1. У него обратная беда: GitHub
    Actions на `shell: powershell` запускает Windows PowerShell 5.1, а тот читает
    .ps1 БЕЗ BOM как ANSI. Кириллица в скрипте превращается в кашу, и парсер
    падает на середине файла. Поэтому BOM у run-build.ps1 обязателен, и его
    пропажу надо ловить так же строго.

.OUTPUTS
    Код возврата 0 - всё в порядке, 1 - найдены проблемы (печатаются как
    ::error::, чтобы GitHub показал их в шапке джобы).
#>
[CmdletBinding()]
param(
    [string]$Bat = 'start-build.bat',
    [string]$Runner = '.github/scripts/run-build.ps1'
)

$ErrorActionPreference = 'Continue'

# GitHub Actions читает stdout шага как UTF-8, а Windows PowerShell 5.1
# кодирует перенаправленный вывод в OEM-кодировку консоли - русский журнал
# превращается в кракозябры. Ставим UTF-8 явно. На чтение журнала батника это
# не влияет: тот пишет cmd напрямую в файл, и там по-прежнему CP866.
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$root = if ($env:GITHUB_WORKSPACE) { $env:GITHUB_WORKSPACE } else { (Get-Location).Path }
Set-Location $root
$tmp = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }

$fail = New-Object System.Collections.Generic.List[string]
$cp866 = [System.Text.Encoding]::GetEncoding(866)

# ---------------------------------------------------------------------------
# 1. start-build.bat: CP866, CRLF, без BOM, и cmd его действительно парсит
# ---------------------------------------------------------------------------
$batPath = Join-Path $root $Bat
if (-not (Test-Path $batPath)) {
    Write-Host ('::error::не найден ' + $Bat)
    exit 1
}
$b = [System.IO.File]::ReadAllBytes($batPath)
Write-Host ($Bat + ': ' + $b.Length + ' байт')

# 1.1 BOM. cmd прочитает EF BB BF как часть первой команды, и @echo off не
#     сработает - дальше в лог поедет эхо всех команд.
if ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF) {
    $fail.Add('BOM в начале файла: cmd прочитает его как часть первой команды')
}

# 1.2 Кириллица в UTF-8 вместо CP866. В CP866 русские буквы живут в 0x80-0xAF
#     и 0xE0-0xF1, а байты 0xD0/0xD1 - это псевдографика, которой в файле нет.
#     Так что пара D0|D1 + 80..BF означает ровно одно: файл пересохранён в UTF-8.
$utf8cyr = 0
for ($i = 0; $i -lt $b.Length - 1; $i++) {
    if (($b[$i] -eq 0xD0 -or $b[$i] -eq 0xD1) -and $b[$i + 1] -ge 0x80 -and $b[$i + 1] -le 0xBF) { $utf8cyr++ }
}
if ($utf8cyr -gt 0) {
    $fail.Add('найдено ' + $utf8cyr + ' последовательностей UTF-8-кириллицы (D0/D1 + 80..BF): файл в UTF-8, а нужен CP866')
}

# 1.3 Псевдографика CP866 (0xB0-0xDF). Баннеры нарисованы символами = и -, так
#     что этот блок в файле пуст. Если байты появились - вставка из чужого
#     редактора, и кодировка там почти наверняка уже не та.
$box = 0
foreach ($x in $b) { if ($x -ge 0xB0 -and $x -le 0xDF) { $box++ } }
if ($box -gt 0) {
    $fail.Add('найдено ' + $box + ' байтов псевдографики 0xB0-0xDF, в этом батнике их быть не должно')
}

# 1.4 Переводы строк строго CRLF. Голый LF ломает метки и goto.
$cr = 0; $lf = 0
foreach ($x in $b) { if ($x -eq 0x0D) { $cr++ } elseif ($x -eq 0x0A) { $lf++ } }
if ($cr -ne $lf) {
    $fail.Add('переводы строк не CRLF: CR=' + $cr + ' LF=' + $lf)
}

$text = $cp866.GetString($b)
$lines = $text -split "`r`n"

# 1.5 chcp 866 обязан стоять второй строкой, до первого русского echo.
if ($lines.Count -lt 2 -or $lines[1] -notmatch '^chcp 866') {
    $fail.Add('вторая строка должна быть "chcp 866 >nul", найдено: "' + $lines[1] + '"')
}
Write-Host ('  строк: ' + $lines.Count + ', chcp 866 второй строкой: ' + ($lines[1] -match '^chcp 866'))

# 1.6 Главная проверка - cmd способен этот файл исполнить. Все байтовые эвристики
#     выше лишь подсказки; если --help печатает внятную справку и не содержит
#     "is not recognized", батник живой.
$helpLog = Join-Path $tmp 'encoding-check-help.log'
if (Test-Path $helpLog) { Remove-Item $helpLog -Force }
cmd /c "$Bat --help > `"$helpLog`" 2>&1 < nul"
$rc = $LASTEXITCODE
$htext = $cp866.GetString([System.IO.File]::ReadAllBytes($helpLog))
if ($rc -ne 0) { $fail.Add('--help вернул код ' + $rc) }
if ($htext -match 'is not recognized') { $fail.Add('cmd не распарсил файл: в выводе --help есть "is not recognized"') }
if ($htext.IndexOf([char]0xFFFD) -ge 0) { $fail.Add('в выводе --help есть U+FFFD - текст побился') }
if ($htext -notmatch 'SWAGcleaner') { $fail.Add('вывод --help не похож на справку SWAGcleaner') }
$hasRepl = ($htext.IndexOf([char]0xFFFD) -ge 0)
Write-Host ('  --help: код ' + $rc + ', U+FFFD ' + $(if ($hasRepl) { 'ЕСТЬ' } else { 'нет' }))

# ---------------------------------------------------------------------------
# 2. run-build.ps1: BOM обязателен, и скрипт должен запускаться
# ---------------------------------------------------------------------------
$psPath = Join-Path $root $Runner
if (-not (Test-Path $psPath)) {
    $fail.Add('не найден ' + $Runner)
} else {
    $pb = [System.IO.File]::ReadAllBytes($psPath)
    Write-Host ($Runner + ': ' + $pb.Length + ' байт')
    $hasBom = ($pb.Length -ge 3 -and $pb[0] -eq 0xEF -and $pb[1] -eq 0xBB -and $pb[2] -eq 0xBF)
    if (-not $hasBom) {
        $fail.Add($Runner + ' сохранён БЕЗ UTF-8 BOM: Windows PowerShell 5.1 прочитает кириллицу как ANSI и упадёт с ошибкой парсера. Пересохранить в UTF-8 with BOM.')
    }
    Write-Host ('  BOM: ' + $(if ($hasBom) { 'есть' } else { 'НЕТ' }))

    # Прогоняем весь тракт на мгновенном --help: перехват вывода батника,
    # перекодировка CP866 -> UTF-8, печать журнала, код возврата. Сводку джобы
    # на время глушим, чтобы проверка не засоряла настоящий отчёт.
    if ($hasBom) {
        $savedSummary = $env:GITHUB_STEP_SUMMARY
        $env:GITHUB_STEP_SUMMARY = $null
        $probeLog = Join-Path $tmp 'encoding-check-runner.log'
        if (Test-Path $probeLog) { Remove-Item $probeLog -Force }
        cmd /c "powershell -NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -Mode onefile -ExtraFlags --help > `"$probeLog`" 2>&1"
        $prc = $LASTEXITCODE
        $env:GITHUB_STEP_SUMMARY = $savedSummary
        $ptext = [System.IO.File]::ReadAllText($probeLog, [System.Text.Encoding]::UTF8)
        if ($prc -ne 0) { $fail.Add($Runner + ' --help вернул код ' + $prc) }
        if ($ptext -match 'ParserError|TerminatorExpected|Unexpected token') {
            $fail.Add($Runner + ' не парсится Windows PowerShell - почти наверняка пропал BOM')
        }
        if ($ptext -notmatch 'полная сборка SWAGcleaner') {
            $fail.Add($Runner + ' не вывел справку батника - перекодировка CP866 не работает')
        }
        if ($ptext.IndexOf([char]0xFFFD) -ge 0) { $fail.Add($Runner + ' напечатал U+FFFD - журнал декодируется не той кодировкой') }
        Write-Host ('  прогон тракта: код ' + $prc)
    }
}

# ---------------------------------------------------------------------------
if ($fail.Count -gt 0) {
    Write-Host ''
    Write-Host '::error::файлы сборки побиты кодировкой, чинить их, а не сборку'
    foreach ($f in $fail) { Write-Host ('::error::  ' + $f) }
    Write-Host ''
    Write-Host 'Как чинить:'
    Write-Host '  start-build.bat   сохранить в CP866 с CRLF. Правильный способ - держать в'
    Write-Host '                    .gitattributes строку'
    Write-Host '                      start-build.bat text eol=crlf working-tree-encoding=CP866'
    Write-Host '                    и дать git пересобрать рабочую копию: удалить файл и'
    Write-Host '                    выполнить  git checkout -- start-build.bat'
    Write-Host '  run-build.ps1     сохранить в UTF-8 с BOM'
    exit 1
}
Write-Host ''
Write-Host 'всё в порядке: start-build.bat в CP866+CRLF и парсится, run-build.ps1 с BOM и работает'
exit 0
