# Единая точка запуска для Windows.
#
# Доводит машину до рабочего состояния без ручных шагов: ставит uv (он же
# принесёт нужный Python), синхронизирует зависимости, кладёт ffmpeg и поднимает
# сервер. Всё доустановленное лежит в runtime\ внутри папки приложения.
#
# Про ffmpeg: берём именно shared-сборку. Обычный статический ffmpeg работает
# для конвертации, но torchcodec грузит avcodec-*.dll / avformat-*.dll напрямую
# и без них падает при импорте — это самая частая причина «у меня не ставится».

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # иначе Invoke-WebRequest тормозит на прогресс-баре

function Say  ($m) { Write-Host "> $m"  -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "+ $m"  -ForegroundColor Green }
function Warn ($m) { Write-Host "! $m"  -ForegroundColor Yellow }
function Die  ($m) { Write-Host "x $m"  -ForegroundColor Red; Read-Host "`nEnter для выхода"; exit 1 }

# Приложение создаёт venv'ы, качает модели и пишет .env рядом с кодом. В
# Program Files это доступно только администратору, поэтому если папка не
# пишется — переносим код в профиль и работаем оттуда. Так же устроена сборка
# для macOS: бандл там неизменяемый шаблон.
function Get-WorkDir ($bundle) {
    $probe = Join-Path $bundle ('.write-' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType File -Path $probe -ErrorAction Stop | Out-Null
        Remove-Item $probe -Force -ErrorAction SilentlyContinue
        return $bundle
    } catch { }

    $target = Join-Path $env:LOCALAPPDATA 'Video-Dub'
    Say "Папка установки только для чтения — работаю в $target"
    New-Item -ItemType Directory -Force -Path $target | Out-Null

    # /E, а не /MIR: зеркало удалило бы из профиля окружения, модели и проекты.
    # Исключения на случай, если в папке установки они всё же появились —
    # копировать оттуда гигабайты незачем.
    # Не $args: это встроенная переменная PowerShell со списком аргументов функции
    $rcArgs = @($bundle, $target, '/E', '/NJH', '/NJS', '/NP', '/NDL', '/NFL',
                '/XD', 'voices', 'runtime', 'models', 'projects', '.venv', '.venv-faster-whisper',
                '.venv-whisperx', '.venv-omnivoice', '.venv-demucs', '.venv-latentsync', '.latentsync',
                '/XF', '.env')
    $rc = Start-Process robocopy -ArgumentList $rcArgs -NoNewWindow -Wait -PassThru
    # У robocopy успех — это код меньше 8; 1 означает «файлы скопированы»
    if ($rc.ExitCode -ge 8) { Die "не удалось скопировать приложение в $target (robocopy $($rc.ExitCode))" }

    # Голоса кладём только при первой установке: дальше это папка пользователя
    $voices = Join-Path $target 'voices'
    if (-not (Test-Path $voices)) {
        Copy-Item (Join-Path $bundle 'voices') $voices -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $target
}

$AppDir     = Get-WorkDir (Split-Path -Parent $PSScriptRoot)
$RuntimeDir = Join-Path $AppDir 'runtime'
$RuntimeBin = Join-Path $RuntimeDir 'bin'
$Port       = if ($env:PORT) { $env:PORT } else { '5050' }

New-Item -ItemType Directory -Force -Path $RuntimeBin | Out-Null
$env:PATH = "$RuntimeBin;$env:PATH"

function Ensure-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Ok "uv найден: $(uv --version)"
        return
    }
    Say 'Устанавливаю uv...'
    $env:UV_INSTALL_DIR = $RuntimeBin
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Die "не удалось установить uv: $_"
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Die 'uv не установился, поставьте вручную: https://docs.astral.sh/uv/'
    }
    Ok 'uv установлен'
}

function Ensure-Ffmpeg {
    # Мало найти ffmpeg.exe — нужен ещё и avcodec DLL рядом, иначе torchcodec
    # упадёт уже в рантайме, а не при проверке.
    $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($ff) {
        $dir = Split-Path -Parent $ff.Source
        $dll = Get-ChildItem -Path $dir -Filter 'avcodec-*.dll' -ErrorAction SilentlyContinue
        if ($dll) {
            Ok 'ffmpeg найден (shared-сборка с DLL)'
            return
        }
        Warn 'Найден ffmpeg без DLL — ML-библиотекам такой не подходит, ставлю shared-сборку рядом'
    }

    Say 'Скачиваю ffmpeg (shared)...'
    $zip = Join-Path $env:TEMP 'ffmpeg-shared.zip'
    $url = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip'
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip
    } catch {
        Die "не удалось скачать ffmpeg: $_`n   Поставьте вручную: scoop install ffmpeg-shared"
    }

    $tmp = Join-Path $env:TEMP 'ffmpeg-extract'
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Expand-Archive -Path $zip -DestinationPath $tmp -Force

    # Внутри архива один каталог верхнего уровня с bin\ — забираем его содержимое
    $bin = Get-ChildItem -Path $tmp -Directory | ForEach-Object { Join-Path $_.FullName 'bin' } |
           Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $bin) { Die 'структура архива ffmpeg изменилась, поставьте его вручную' }

    Copy-Item -Path (Join-Path $bin '*') -Destination $RuntimeBin -Force
    Remove-Item -Recurse -Force $tmp, $zip -ErrorAction SilentlyContinue
    Ok 'ffmpeg установлен в runtime\bin'
}

function Ensure-Deps {
    Say 'Синхронизирую зависимости...'
    Push-Location $AppDir
    try {
        uv sync --quiet
        if ($LASTEXITCODE -ne 0) { Die 'uv sync завершился с ошибкой' }
    } finally {
        Pop-Location
    }
    Ok 'Зависимости готовы'
}

function Ensure-Env {
    $envFile = Join-Path $AppDir '.env'
    $example = Join-Path $AppDir '.env.example'
    if (-not (Test-Path $envFile) -and (Test-Path $example)) {
        Copy-Item $example $envFile
        Ok 'Создан .env (ключи API можно задать в настройках приложения)'
    }
}

Write-Host ''
Say 'Video-Dub — подготовка к запуску'
Ensure-Uv
Ensure-Ffmpeg
Ensure-Deps
Ensure-Env
Write-Host ''
Ok "Открываю http://127.0.0.1:$Port"
Write-Host ''

Set-Location $AppDir
$env:VIDEO_DUB_OPEN_BROWSER = '1'
$env:PORT = $Port
uv run python app.py
