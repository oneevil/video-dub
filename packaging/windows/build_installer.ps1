# Собирает установщик Video-Dub для Windows.
#
# Требуется Inno Setup 6 (winget install JRSoftware.InnoSetup).
# Использование:  .\packaging\windows\build_installer.ps1 -Version 0.1.0

param(
    [string]$Version = ''
)

$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Dist = Join-Path $Root 'dist'
$Payload = Join-Path $Dist 'payload'

if (-not $Version) {
    # Версия из pyproject.toml, чтобы не держать её в двух местах
    $line = Select-String -Path (Join-Path $Root 'pyproject.toml') -Pattern '^version\s*=' | Select-Object -First 1
    if (-not $line) { throw 'не нашёл version в pyproject.toml' }
    $Version = ($line.Line -split '"')[1]
}
Write-Host "Версия: $Version"

# ── payload ──────────────────────────────────────────────────────────────────
# Список файлов даёт git с учётом .gitignore — venv'ы, модели и проекты
# отсекаются, а незакоммиченные правки при локальной сборке попадают.
Remove-Item -Recurse -Force $Dist -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Payload | Out-Null

Push-Location $Root
$prevEncoding = [Console]::OutputEncoding
try {
    # core.quotepath=off — иначе git отдаёт пути с не-ASCII в кавычках и
    # восьмеричных escape'ах ("voices/\320\232..."), и Copy-Item получает
    # несуществующее имя файла. UTF-8 нужен, чтобы PowerShell не прочитал
    # вывод git в кодировке консоли Windows.
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $files = git -c core.quotepath=off ls-files --cached --others --exclude-standard
    if ($LASTEXITCODE -ne 0) { throw 'git ls-files завершился с ошибкой' }
    foreach ($rel in $files) {
        $dst = Join-Path $Payload $rel
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
        Copy-Item -LiteralPath $rel -Destination $dst -Force
    }
} finally {
    [Console]::OutputEncoding = $prevEncoding
    Pop-Location
}
if (-not (Test-Path (Join-Path $Payload 'scripts\bootstrap.ps1'))) {
    throw 'в сборку не попал scripts\bootstrap.ps1'
}
Write-Host "payload собран: $Payload"

# ── компиляция ───────────────────────────────────────────────────────────────
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    foreach ($p in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )) {
        if (Test-Path $p) { $iscc = $p; break }
    }
}
if (-not $iscc) {
    throw 'Inno Setup не найден. Установите: winget install JRSoftware.InnoSetup'
}

& $iscc "/DMyAppVersion=$Version" (Join-Path $PSScriptRoot 'video-dub.iss')
if ($LASTEXITCODE -ne 0) { throw 'iscc завершился с ошибкой' }

Remove-Item -Recurse -Force $Payload
Get-ChildItem $Dist -Filter '*.exe' | ForEach-Object { Write-Host "+ $($_.FullName)" -ForegroundColor Green }
