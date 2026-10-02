<#
.SYNOPSIS
    Запускает start-build.bat и публикует его журнал в GitHub Actions.

.DESCRIPTION
    Вынесено из ci.yml и release.yml, чтобы оба workflow дёргали сборку ровно
    одним способом и не разъезжались со временем.

    «Просто запустить батник» здесь не работает по трём причинам:

      1. start-build.bat обязан лежать в CP866 (cmd.exe читает батник побайтово,
         но позицию считает в символах, и на UTF-8 съезжает внутрь многобайтовой
         последовательности). Значит и журнал он пишет байтами CP866, а лог
         GitHub Actions читается как UTF-8. Вывод перехватывается в файл и
         перекодируется, иначе вместо русского текста будут кракозябры.

      2. Интерактивный `pause` в конце подвесит джобу до таймаута, поэтому
         всегда добавляется --no-pause, а stdin закрывается через `< nul`.

      3. Код возврата батника надо донести до шага, иначе упавшая сборка
         выглядит в CI зелёной.

.PARAMETER Mode
    Раскладка PyInstaller: onefile или onedir.

.PARAMETER ExtraFlags
    Дополнительные флаги батника. По умолчанию --tests --verify, то есть
    тесты как жёсткий гейт и живой запуск уже замороженного exe.

.EXAMPLE
    & .github\scripts\run-build.ps1 -Mode onefile
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('onefile', 'onedir')]
    [string]$Mode,

    [string[]]$ExtraFlags = @('--tests', '--verify')
)

# намеренно не Stop: падающий pip или cargo пишут в stderr, и это нормальный
# рабочий поток батника, а не повод ронять шаг до разбора журнала
$ErrorActionPreference = 'Continue'

# GitHub Actions читает stdout шага как UTF-8, а Windows PowerShell 5.1
# кодирует перенаправленный вывод в OEM-кодировку консоли - русский журнал
# превращается в кракозябры. Ставим UTF-8 явно. На чтение журнала батника это
# не влияет: тот пишет cmd напрямую в файл, и там по-прежнему CP866.
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

$root = if ($env:GITHUB_WORKSPACE) { $env:GITHUB_WORKSPACE } else { (Get-Location).Path }
$tmp  = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
Set-Location $root

$log  = Join-Path $tmp 'swag-build.log'
$utf8 = Join-Path $tmp 'swag-build-utf8.log'
foreach ($f in @($log, $utf8)) { if (Test-Path $f) { Remove-Item $f -Force } }

$flags = (@("--$Mode") + $ExtraFlags + @('--no-pause')) -join ' '
Write-Host ('запускаю: start-build.bat ' + $flags)
Write-Host ('корень  : ' + $root)
Write-Host ''

cmd /c "start-build.bat $flags > `"$log`" 2>&1 < nul"
$rc = $LASTEXITCODE

$enc = [System.Text.Encoding]::GetEncoding(866)
# FileShare ReadWrite: батник может ещё держать журнал открытым
$fs = [System.IO.File]::Open($log, 'Open', 'Read', 'ReadWrite')
$ms = New-Object System.IO.MemoryStream
$fs.CopyTo($ms)
$fs.Close()
$text = $enc.GetString($ms.ToArray())
[System.IO.File]::WriteAllText($utf8, $text, (New-Object System.Text.UTF8Encoding($false)))
$lines = $text -split "`r?`n"

if ($env:GITHUB_ACTIONS) { Write-Host '::group::журнал start-build.bat (перекодирован из CP866 в UTF-8)' }
foreach ($l in $lines) {
    # Actions трактует строку, начинающуюся с :: или ##[, как свою команду -
    # а в выводе pytest вполне могут встретиться ::. Отбиваем пробелом.
    if ($l.StartsWith('::') -or $l.StartsWith('##[')) { Write-Host (' ' + $l) } else { Write-Host $l }
}
if ($env:GITHUB_ACTIONS) { Write-Host '::endgroup::' }

# Короткая выжимка в сводку джобы: её видно сразу, без прокрутки журнала.
$pick = $lines | Where-Object {
    $_ -match '^\s*(режим:|артефакт|размер|SHA256|итог:|время сборки|cargo:)' -or
    $_ -match 'вырезано тяжёлых бинарников|passed; \d+ failed|test result:|СБОЙ|ОШИБКА'
}
if ($env:GITHUB_STEP_SUMMARY) {
    $md = New-Object System.Collections.Generic.List[string]
    $md.Add('### Сборка ' + $Mode)
    $md.Add('')
    if ($rc -eq 0) { $md.Add('Код возврата `0` - батник дошёл до конца.') }
    else { $md.Add('Код возврата `' + $rc + '` - **сборка не удалась**.') }
    $md.Add('')
    $md.Add('```')
    if ($pick) { $pick | ForEach-Object { $md.Add($_.Trim()) } }
    else { $md.Add('(ключевые строки не найдены - смотри журнал выше)') }
    $md.Add('```')
    $md.Add('')
    [System.IO.File]::AppendAllText($env:GITHUB_STEP_SUMMARY, ($md -join "`n"), (New-Object System.Text.UTF8Encoding($false)))
}

Write-Host ''
Write-Host ('журнал в UTF-8: ' + $utf8)
Write-Host ('код возврата  : ' + $rc)
if ($rc -ne 0 -and $env:GITHUB_ACTIONS) {
    Write-Host ('::error::start-build.bat вернул ' + $rc + ', подробности в журнале выше')
}
exit $rc
