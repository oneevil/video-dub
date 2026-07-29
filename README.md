# Video Dub

Веб-приложение для автоматического перевода и дубляжа видео.

## Возможности

- Скачивание видео по URL (yt-dlp) или загрузка локального файла
- Разделение голоса и фона (demucs) — опционально
- Транскрипция: Faster Whisper, WhisperX (с диаризацией) — локально, без облака
- Перевод: Claude, OpenAI, Google Translate, Ollama, Custom OpenAI-совместимый API
- Синтез речи: OmniVoice (600+ языков, клон голоса), Edge TTS, ElevenLabs (облако), Fish Audio (облако), macOS Say
- **Lip Sync**: LatentSync — синхронизация губ по аудио (CUDA, на CPU медленно)
- **Мульти-спикер**: определение говорящих (WhisperX + pyannote), назначение голоса каждому спикеру, ручная смена спикера
- Клонирование голоса из референс WAV (локально + облачно через ElevenLabs IVC / Fish Audio)
- Настраиваемый seed интонации и temperature для стабильного стиля озвучки
- Поштучная и пакетная генерация TTS с real-time обновлением UI
- Real-time отображение субтитров при транскрипции и переводе
- Предпросмотр видео с TTS-озвучкой и замедлением
- Отображение субтитров на видео (перевод / оригинал / оба / выкл) — кнопки SVG
- Аудио редактор с waveform, отдельными дорожками для каждого спикера, перетаскиванием TTS-сегментов
- Сборка видео: полный timeline, замедление, микширование, диапазон, вшивание субтитров
- Микширование: без оригинала / полное аудио фоном / только фон без голоса (demucs)
- **Закадровый перевод**: фон + приглушённый оригинальный голос + перевод (настраиваемые громкости)
- Плавные переходы громкости на границах TTS-сегментов
- Переключение оригинального/переведённого видео в плеере
- Синхронизация аудио дорожек (фон, голос) в плеере в зависимости от настроек
- Информация о видео (разрешение, кодек, битрейт, FPS)
- Скачивание исходного и переведённого видео
- Управление проектами: открытие, закрытие, переименование, удаление
- Загрузка и управление моделями через веб-интерфейс
- **Плагин-система**: транскрипция, перевод, TTS, lip sync — подключаемые модули
- **Изолированные окружения**: каждый ML-движок в своём venv (без конфликтов зависимостей)
- SVG иконки по всему интерфейсу
- Поддержка CUDA (Windows/Linux), MPS (macOS Apple Silicon), CPU
- Тёмная и светлая тема

## Установка

### Готовые сборки (рекомендуется)

Скачайте с [Releases](https://github.com/OneEvil/video-dub/releases):

| Система | Файл | Что делать |
|---------|------|------------|
| **Windows** | `Video-Dub-*-setup.exe` | Запустить установщик, дальше ярлык на рабочем столе |
| **macOS** | `Video-Dub-*.dmg` | Открыть, перетащить в Applications, запустить |
| **Linux** | `install.sh` | `curl -LsSf https://raw.githubusercontent.com/OneEvil/video-dub/main/install.sh \| bash`, затем команда `video-dub` |

Больше делать ничего не нужно: при первом запуске приложение само поставит Python
нужной версии, ffmpeg и зависимости, создаст `.env` и откроет браузер. Займёт
несколько минут — качается несколько гигабайт.

> **macOS:** сборка подписана Developer ID и нотаризована Apple, поэтому открывается
> без предупреждений. Данные приложения (модели, окружения, проекты) лежат в
> `~/Library/Application Support/Video-Dub` — сам бандл не изменяется, иначе бы
> ломалась подпись.

> **Windows:** ставится именно shared-сборка ffmpeg. Обычная статическая не подходит —
> ML-библиотеки (torchcodec) грузят `avcodec-*.dll` напрямую и без них падают.

> **NVIDIA:** для GPU-ускорения нужен установленный [CUDA Toolkit](https://developer.nvidia.com/cuda-downloads) 12.4+
> и драйвер (`nvidia-smi`). Отдельно cuDNN не нужен — PyTorch несёт его с собой.
> Без CUDA всё работает на процессоре, только медленнее.

### Запуск из исходников

Нужен [uv](https://docs.astral.sh/uv/) и ffmpeg в PATH.

```bash
git clone https://github.com/OneEvil/video-dub
cd video-dub
./scripts/bootstrap.sh          # macOS / Linux
.\Video-Dub.bat                 # Windows
```

`bootstrap` проверит окружение, доустановит недостающее и запустит сервер на
http://localhost:5050. Если хочется вручную:

```bash
cp .env.example .env
uv sync
uv run python app.py
```

ML-окружения создаются сами при первом использовании движка. Поставить все заранее:

```bash
uv run python setup_all.py
```

### Сборка установщиков

```bash
packaging/macos/build_dmg.sh 0.1.0                          # macOS → dist/*.dmg (подпись ad-hoc)
NOTARY_PROFILE=video-dub packaging/macos/build_dmg.sh 0.1.0  # + подпись Developer ID и нотаризация
.\packaging\windows\build_installer.ps1 -Version 0.1.0      # Windows → dist/*.exe (нужен Inno Setup 6)
```

Обычно этого не требуется: `git tag v0.1.0 && git push --tags` запускает
[GitHub Actions](.github/workflows/release.yml), который собирает все три
платформы и выкладывает их в Releases.

---

## Настройки (.env)

Все настройки можно менять через веб-интерфейс (сохраняются автоматически в `.env`).

### API ключи

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `ANTHROPIC_API_KEY` | API ключ Anthropic | — |
| `OPENAI_API_KEY` | API ключ OpenAI | — |
| `CUSTOM_API_KEY` | Ключ для custom API | — |
| `CUSTOM_API_URL` | URL custom API | — |
| `OLLAMA_URL` | URL Ollama | `http://localhost:11434` |
| `HF_TOKEN` | HuggingFace токен для диаризации (WhisperX) | — |
| `ELEVENLABS_API_KEY` | API ключ ElevenLabs (TTS) | — |
| `FISH_API_KEY` | API ключ Fish Audio (TTS) | — |

### Транскрипция

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `TRANSCRIBE_ENGINE` | Движок транскрипции | `faster-whisper` |
| `WHISPER_MODEL` | Модель Whisper | `large-v3` |
| `SEPARATE_VOCALS` | Разделять голос и фон (demucs) | `true` |

### Перевод

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `TRANSLATE_PROVIDER` | Провайдер перевода | `claude` |
| `TRANSLATE_MODEL` | Модель перевода | `claude-haiku-4-5` |

### TTS

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `TTS_ENGINE` | Движок TTS | `omnivoice` |
| `TTS_VOICE` | Голос TTS | — |
| `TTS_SEED` | Seed интонации (фикс. стиль), -1 = выкл | `44` |

### Сборка

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `BUILD_FORMAT` | Формат выходного видео | `mp4` |
| `BUILD_CODEC` | Видеокодек | `copy` |
| `BUILD_PRESET` | Качество (пресет) | `medium` |
| `BUILD_AUDIO_BITRATE` | Аудио битрейт | `128k` |
| `BUILD_MAX_SLOWDOWN` | Макс. замедление видео | `3.0` |
| `BUILD_ORIGINAL_AUDIO` | Оригинальное аудио: `none`, `full`, `no_vocals`, `voiceover` | `no_vocals` |
| `BUILD_ORIGINAL_VOLUME` | Громкость полного аудио (%) | `10` |
| `BUILD_NO_VOCALS_VOLUME` | Громкость фона без голоса (%) | `50` |
| `BUILD_VOCALS_VOLUME` | Громкость оригинального голоса — закадровый перевод (%) | `15` |
| `BUILD_BURN_SUBS` | Вшить субтитры в видео | `false` |
| `OUTPUT_DIR` | Папка для результатов | `./projects` |

### Lip Sync

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `LIPSYNC_ENABLED` | Включить синхронизацию губ | `false` |
| `LIPSYNC_ENGINE` | Движок lip sync | `latentsync` |

---

## Архитектура

| Шаг | Инструмент | Описание |
|-----|-----------|----------|
| 1. Скачивание | yt-dlp / загрузка | Видео из URL или локальный файл |
| 2. Разделение | demucs (опц.) | Голос + фон без голоса |
| 3. Транскрипция | Плагины | 5 движков, WhisperX с диаризацией спикеров |
| 4. Перевод | Плагины | 5 провайдеров, чанками по 50 фраз |
| 5. TTS | Плагины | 7 движков, мульти-спикер, клонирование |
| 6. Сборка | FFmpeg | Полный timeline + замедление + микширование |
| 7. Lip Sync | LatentSync (опц.) | Синхронизация губ по аудио |

### Плагин-система

Все движки транскрипции, перевода, TTS и lip sync реализованы как плагины — файлы `.py` в соответствующих папках. Чтобы добавить новый движок — создайте файл. Чтобы удалить — удалите файл.

```
plugins/
├── transcribe/                  # Движки транскрипции
│   ├── __init__.py              # Авто-обнаружение плагинов
│   ├── faster_whisper_plugin.py # Faster Whisper (.venv-faster-whisper)
│   ├── faster_whisper_worker.py # Worker-процесс
│   ├── whisperx_plugin.py       # WhisperX + диаризация (.venv-whisperx)
│   └── whisperx_worker.py       # Worker-процесс
├── translate/                   # Провайдеры перевода (без отд. venv)
│   ├── __init__.py              # Авто-обнаружение плагинов
│   ├── _helpers.py              # Общие функции (промпт, парсинг)
│   ├── claude.py                # Claude (Anthropic)
│   ├── openai_translate.py      # OpenAI
│   ├── google.py                # Google Translate
│   ├── ollama.py                # Ollama (локальный)
│   └── custom_api.py            # Custom OpenAI-совместимый
├── tts/                         # TTS движки
│   ├── __init__.py              # Авто-обнаружение плагинов
│   ├── edge.py                  # Edge TTS (без venv)
│   ├── elevenlabs.py            # ElevenLabs (облако, без venv)
│   ├── fish_audio.py            # Fish Audio (облако, без venv)
│   ├── macos_say.py             # macOS Say (системный)
│   ├── omnivoice_tts.py         # OmniVoice (.venv-omnivoice)
│   └── omnivoice_worker.py      # Persistent worker-процесс
└── lipsync/                     # Lip Sync движки
    ├── __init__.py              # Авто-обнаружение плагинов
    ├── latentsync.py            # LatentSync (.venv-latentsync)
    └── _latentsync_runner.py    # Inference-скрипт (CPU/CUDA)
```

**Контракт плагина транскрипции:**
```python
ENGINES = {"engine-id": "Display Name"}  # обязательно
def transcribe(audio_path, out_dir, model_name, log, source_language="", on_segment=None, **kwargs) -> list[dict]  # обязательно
MODELS = [...]  # опционально — список моделей
DOWNLOAD_ENGINES = [{"value": "...", "label": "..."}]  # опционально — загрузка моделей
def download_model(engine, model, log) -> generator  # опционально
def list_downloaded_models() -> list[dict]  # опционально
def check_available() -> bool  # опционально
```

**Контракт плагина перевода:**
```python
ENGINES = {"engine-id": "Display Name"}  # обязательно
def translate(subtitles, target_lang, out_dir, log, on_chunk=None, **kwargs) -> list[dict]  # обязательно
MODELS = [{"id": "model-id", "name": "Display Name"}]  # опционально — для UI селектора
API_KEY_ENV = "ENV_VAR_NAME"  # опционально
NEEDS_MODEL = True  # опционально
NEEDS_BASE_URL = True  # опционально
```

**Контракт плагина TTS:**
```python
ENGINES = {"engine-id": "Display Name"}  # обязательно
def synthesize(subtitles, out_dir, log, engine="", voice="", voice_wav="", voice_text="", seed=-1, temperature=0.7, on_segment=None, **kwargs) -> list[dict]  # обязательно
DOWNLOAD_ENGINES = [{"value": "...", "label": "..."}]  # опционально
def download_model(engine, model, log) -> generator  # опционально
def check_available() -> bool  # опционально
```

**Контракт плагина lip sync:**
```python
ENGINES = {"engine-id": "Display Name"}  # обязательно
def process(video_path, audio_path, out_path, log, **kwargs) -> str  # обязательно — возвращает путь к результату
def setup(log) -> None  # опционально — установка зависимостей
def check_available() -> bool  # опционально
```

### Изолированные окружения

Каждый ML-движок работает в своём виртуальном окружении через worker-процессы. Основной проект лёгкий — только Flask, API клиенты, edge-tts.

| Окружение | Пакеты | Когда создаётся |
|-----------|--------|----------------|
| `.venv-faster-whisper` | faster-whisper | при первом запуске Faster Whisper |
| `.venv-whisperx` | whisperx, pyannote-audio, torch | при первом запуске WhisperX |
| `.venv-omnivoice` | omnivoice, torch | при первом запуске OmniVoice |
| `.venv-demucs` | demucs, torch | при первом разделении голоса |
| `.venv-latentsync` | torch, diffusers, kornia, insightface | при первом запуске LatentSync |

Все окружения можно создать заранее: `uv run python setup_all.py`

### Закадровый перевод (voiceover)

Режим сборки "Закадровый перевод" микширует 3 аудио-дорожки:
- **Фон** (demucs, без голоса) — настраиваемая громкость
- **Оригинальный голос** (demucs, только голос) — настраиваемая громкость
- **Переведённый голос** (TTS) — 100%

В промежутках между субтитрами оригинальный спикер слышен на фоне. На TTS-сегментах все три дорожки микшируются.

### Lip Sync (LatentSync)

LatentSync — модель синхронизации губ по аудио от ByteDance. Автоматически скачивает репозиторий, создаёт окружение и загружает модель при первом использовании.

- **CUDA (рекомендуется)**: ~30 сек на 10 сек видео (RTX 4090)
- **CPU**: работает, но крайне медленно (десятки минут)
- **macOS**: поддерживается на CPU (MPS не поддерживается LatentSync)
- Доступен только в режимах аудио "Без оригинала" и "Только фон"

### Мульти-спикер

1. **WhisperX + pyannote** определяет количество и границы спикеров
2. В субтитрах каждая фраза помечена спикером (S0, S1...) — кликом можно переназначить
3. В панели "Голоса спикеров" каждому спикеру назначается свой TTS движок и голос
4. При генерации TTS субтитры группируются по спикерам — каждый озвучивается своим голосом
5. В аудио редакторе — отдельная дорожка для каждого спикера с цветовой кодировкой

### TTS движки

| Движок | Тип | Клон голоса | Встроенные голоса |
|--------|-----|-------------|-------------------|
| Edge TTS | Облако (бесплатно) | Нет | 300+ голосов на всех языках |
| ElevenLabs | Облако (API ключ) | Да (IVC, сохраняется) | Голоса из аккаунта |
| Fish Audio | Облако (API ключ) | Да (модель, сохраняется) | 1.6M+ публичных моделей |
| OmniVoice | Локально (отд. venv) | Да (WAV референс) | Voice design (instruct) |
| macOS Say | Системный (macOS) | Нет | Системные голоса |

### Замедление видео

Если озвученная фраза длиннее временного слота — видео-сегмент плавно замедляется (`setpts`/`atempo`), максимум настраивается (по умолчанию 3x). Промежутки между субтитрами сохраняются полностью.

---

## Структура файлов

```
video-dub/
├── app.py                  # Flask-сервер + маршруты
├── pipeline.py             # Пайплайн (диспетчер плагинов)
├── setup_all.py            # Установка всех ML-окружений
├── app/
│   ├── app.html            # Веб-интерфейс (Jinja2)
│   ├── app.css             # Стили
│   └── app.js              # Клиентская логика
├── scripts/
│   ├── bootstrap.sh        # Запуск: macOS / Linux
│   └── bootstrap.ps1       # Запуск: Windows
├── packaging/
│   ├── macos/              # Сборка .app и .dmg
│   └── windows/            # Инсталлятор Inno Setup
├── plugins/
│   ├── transcribe/         # Плагины транскрипции + workers
│   ├── translate/          # Плагины перевода
│   ├── tts/                # Плагины TTS + workers
│   └── lipsync/            # Плагины lip sync
├── .venv-faster-whisper/   # Faster Whisper (создаётся автоматически)
├── .venv-whisperx/         # WhisperX + pyannote
├── .venv-omnivoice/        # OmniVoice
├── .venv-demucs/           # Demucs
├── .venv-latentsync/       # LatentSync
├── .latentsync/            # LatentSync репозиторий (клонируется автоматически)
├── models/
│   ├── whisper/
│   │   ├── faster-whisper/ # Faster Whisper модели
│   │   └── whisperx/       # WhisperX + align + diarize модели
│   ├── tts/
│   │   └── hub/            # TTS модели (HuggingFace)
│   └── lipsync/
│       └── latentsync/     # LatentSync модель (скачивается автоматически)
├── voices/                 # Клонированные голоса (WAV + meta.json)
└── projects/               # Результаты
    └── job_*/
        ├── meta.json
        ├── source.mp4
        ├── audio.wav
        ├── vocals.wav                  # голос (demucs)
        ├── no_vocals.wav               # фон без голоса (demucs)
        ├── original.srt
        ├── translated.srt
        ├── tts_audio/                  # сгенерированные TTS сегменты
        ├── speaker_map.json            # метки спикеров (WhisperX)
        ├── speaker_voice_mapping.json  # маппинг спикер → голос
        └── output.mp4
```
