<#
.SYNOPSIS
    Упаковывает собранный exe и публикует его в GitHub Release.

.DESCRIPTION
    README обещает в релизе архив SWAGcleaner-vX.Y.Z.zip с одним exe без
    установки и файл SHA256SUMS.txt рядом. Этот скрипт держится ровно того
    формата, а не придумывает свой.

    Вынесено из release.yml по той же причине, что и run-build.ps1: GitHub
    Actions пишет инлайн-скрипты шага в UTF-8 без BOM, а `shell: powershell` на
    раннере - это Windows PowerShell 5.1, который читает такой файл как ANSI.
    Кириллица в инлайне ломает парсер, поэтому весь текст живёт здесь, в файле
    с BOM, а в YAML остаётся ASCII-однострочник.

    Публикация идёт через gh CLI, который уже стоит на раннере и авторизуется
    из GITHUB_TOKEN. Сторонние actions не используются намеренно: у них
    непрозрачный цикл релизов и чужие права, а тут нужны две команды.

.PARAMETER Tag
    Тег релиза, например v1.0.1. Берётся из github.ref_name.

.PARAMETER Exe
    Путь к артефакту относительно корня репозитория.

.OUTPUTS
    Код возврата 0 при успехе. В рабочем дереве остаются zip и SHA256SUMS.txt.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Tag,

    [string]$Exe = 'dist\SWAGcleaner.exe'
)

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

$exePath = Join-Path $root $Exe
if (-not (Test-Path $exePath)) {
    Write-Host ('::error::артефакт не найден: ' + $Exe)
    exit 1
}
$exeName = [System.IO.Path]::GetFileName($exePath)

$zipName  = 'SWAGcleaner-' + $Tag + '.zip'
$sumsName = 'SHA256SUMS.txt'
$zipPath  = Join-Path $root $zipName
$sumsPath = Join-Path $root $sumsName
foreach ($f in @($zipPath, $sumsPath)) { if (Test-Path $f) { Remove-Item $f -Force } }

Compress-Archive -Path $exePath -DestinationPath $zipPath -CompressionLevel Optimal
if (-not (Test-Path $zipPath)) {
    Write-Host '::error::Compress-Archive не создал архив'
    exit 1
}

# Формат GNU sha256sum: "<хеш><два пробела><имя>", чтобы работал sha256sum -c.
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLower()
$exeHash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash.ToLower()
$sumsText = $zipHash + '  ' + $zipName + "`n" + $exeHash + '  ' + $exeName + "`n"
[System.IO.File]::WriteAllText($sumsPath, $sumsText, (New-Object System.Text.UTF8Encoding($false)))

$zipSize = (Get-Item $zipPath).Length
$exeSize = (Get-Item $exePath).Length
Write-Host ('архив   : ' + $zipName + ', ' + $zipSize + ' байт')
Write-Host ('exe     : ' + $exeName + ', ' + $exeSize + ' байт')
Write-Host ('SHA256  : ' + $zipHash)

# ---------------------------------------------------------------------------
# Версия в исходнике против тега. Намеренно НЕ блокирую релиз: тег - решение
# мейнтейнера, и запрещать ему расходиться было бы наглостью. Но разъезд надо
# показывать заметно, иначе в релиз уедет exe, который на --version отвечает
# не тем числом, и это всплывёт только в баг-репортах.
# ---------------------------------------------------------------------------
$appVer = ''
$srcPath = Join-Path $root 'swagcleaner.py'
if (Test-Path $srcPath) {
    $src = [System.IO.File]::ReadAllText($srcPath, [System.Text.Encoding]::UTF8)
    if ($src -match 'def _version\(\)[^\n]*\r?\n\s*return\s+["'']([^"'']+)["'']') { $appVer = $Matches[1] }
}
$tagVer = $Tag.TrimStart('v', 'V')
$verLabel = if ($appVer) { $appVer } else { 'не найдена' }
Write-Host ('тег     : ' + $tagVer + '   _version() в swagcleaner.py: ' + $verLabel)
$verWarn = ''
if ($appVer -and ($appVer -ne $tagVer)) {
    $verWarn = '`_version()` в `swagcleaner.py` возвращает `' + $appVer + '`, а тег релиза `' + $Tag + '`. exe на `--version` ответит не тем числом.'
    Write-Host ('::warning::' + $verWarn)
}

# ---------------------------------------------------------------------------
$sha = ''
if ($env:GITHUB_SHA) { $sha = $env:GITHUB_SHA }
if (-not $sha) { $sha = ((git rev-parse HEAD) | Out-String).Trim() }
$shortSha = $sha
if ($sha.Length -gt 7) { $shortSha = $sha.Substring(0, 7) }

$notes = New-Object System.Collections.Generic.List[string]
$notes.Add('Собрано в GitHub Actions из коммита `' + $shortSha + '` тем же `start-build.bat`, которым проект собирается локально: pytest и cargo test как жёсткий гейт, Rust-ядро, PyInstaller и живой self-test уже замороженного exe.')
$notes.Add('')
$notes.Add('| | |')
$notes.Add('|---|---|')
$notes.Add('| Архив | `' + $zipName + '` |')
$notes.Add('| Размер | ' + $zipSize + ' байт |')
$notes.Add('| SHA256 | `' + $zipHash + '` |')
$notes.Add('| Внутри | `' + $exeName + '`, один файл, без установки |')
$notes.Add('')
if ($verWarn) {
    $notes.Add('> [!WARNING]')
    $notes.Add('> ' + $verWarn)
    $notes.Add('')
}
$notes.Add('Проверка целостности: `certutil -hashfile ' + $zipName + ' SHA256` или `sha256sum -c ' + $sumsName + '`.')
$notes.Add('')
$notes.Add('Запуск требует повышения прав: в манифесте сборки стоит `requireAdministrator`.')
$notesText = $notes -join "`n"

# Заметки кладу во временный каталог, а не в рабочее дерево: --notes-file
# читает файл, а лишнего мусора в чекауте оставлять не надо.
$notesPath = Join-Path $tmp ('release-notes-' + $Tag + '.md')
[System.IO.File]::WriteAllText($notesPath, $notesText, (New-Object System.Text.UTF8Encoding($false)))

# ---------------------------------------------------------------------------
$env:GH_TOKEN = $env:GITHUB_TOKEN
if (-not $env:GH_TOKEN) {
    Write-Host '::error::GITHUB_TOKEN не передан в окружение шага'
    exit 1
}

gh release view $Tag *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host ('релиз ' + $Tag + ' уже есть - докладываю файлы поверх')
} else {
    Write-Host ('создаю релиз ' + $Tag)
    gh release create $Tag --title ('SWAGcleaner ' + $Tag) --notes-file $notesPath --target $sha
    if ($LASTEXITCODE -ne 0) {
        Write-Host '::error::gh release create провалился'
        exit 1
    }
}

Write-Host 'загружаю архив и SHA256SUMS.txt'
gh release upload $Tag $zipPath $sumsPath --clobber
if ($LASTEXITCODE -ne 0) {
    Write-Host '::error::gh release upload провалился'
    exit 1
}

$repoUrl = ''
if ($env:GITHUB_SERVER_URL -and $env:GITHUB_REPOSITORY) {
    $repoUrl = $env:GITHUB_SERVER_URL + '/' + $env:GITHUB_REPOSITORY
}
Write-Host ''
Write-Host ('готово: ' + $repoUrl + '/releases/tag/' + $Tag)
exit 0
