# Video Dub

Веб-приложение для автоматического перевода и дубляжа видео.

## Возможности

- Скачивание видео по URL (yt-dlp) или загрузка локального файла
- Разделение голоса и фона (demucs) — опционально
- Транскрипция: OpenAI Whisper, Faster Whisper, WhisperX (с диаризацией), Whisper API
- Перевод: Claude, OpenAI, Google Translate, Ollama, Custom OpenAI-совместимый API
- Синтез речи: Qwen3-TTS Base (клон голоса), Qwen3-TTS CustomVoice (встроенные), Edge TTS (Microsoft), OmniVoice (600+ языков), macOS Say
- **Мульти-спикер**: определение говорящих (WhisperX + pyannote), назначение голоса каждому спикеру, ручная смена спикера
- Клонирование голоса из референс WAV с тестом
- Настраиваемый seed интонации и temperature для стабильного стиля озвучки
- Поштучная и пакетная генерация TTS с real-time обновлением UI
- Real-time отображение субтитров при транскрипции и переводе
- Предпросмотр видео с TTS-озвучкой и замедлением
- Отображение субтитров на видео (перевод / оригинал / оба / выкл) — кнопки SVG
- Аудио редактор с waveform, отдельными дорожками для каждого спикера, перетаскиванием TTS-сегментов
- Сборка видео: полный timeline, замедление, микширование, диапазон, вшивание субтитров
- Микширование: без оригинала / полное аудио фоном / только фон без голоса (demucs)
- Плавные переходы громкости на границах TTS-сегментов
- Информация о видео (разрешение, кодек, битрейт, FPS)
- Скачивание исходного и переведённого видео
- Управление проектами: открытие, закрытие, переименование, удаление
- Загрузка и управление моделями через веб-интерфейс
- **Закадровый перевод**: фон + приглушённый оригинальный голос + перевод (настраиваемые громкости)
- **Плагин-система**: транскрипция, перевод и TTS — подключаемые модули
- **Изолированные окружения**: каждый ML-движок в своём venv (без конфликтов зависимостей)
- SVG иконки по всему интерфейсу
- Поддержка CUDA (Windows/Linux), MPS (macOS Apple Silicon), CPU
- Тёмная и светлая тема

## Установка

### 1. Системные зависимости

```bash
# macOS
brew install ffmpeg yt-dlp

# Ubuntu/Debian
sudo apt install ffmpeg
pip install yt-dlp

# Windows
# Установите ffmpeg и yt-dlp, добавьте в PATH
```

### 2. Настройка

```bash
cp .env.example .env
# Отредактируйте .env или настройте через веб-интерфейс
```

### 3. Установка

```bash
# Основной проект (лёгкий — Flask, API клиенты)
uv sync

# Все ML-окружения сразу (Whisper, WhisperX, Qwen3, OmniVoice, demucs)
uv run python setup_all.py
```

> ML-окружения также создаются автоматически при первом использовании каждого движка.

### 4. Запуск

```bash
uv run python app.py
```

Откроется на http://localhost:5000

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

### Транскрипция

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `TRANSCRIBE_ENGINE` | Движок транскрипции | `openai-whisper` |
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
| `TTS_ENGINE` | Движок TTS | `qwen3-1.7b-base` |
| `TTS_VOICE` | Голос TTS | — |
| `TTS_SEED` | Seed интонации (фикс. стиль), -1 = выкл | `44` |
| `TTS_TEMPERATURE` | Стабильность голоса (0.1-0.9) | `0.7` |

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

---

## Архитектура

| Шаг | Инструмент | Описание |
|-----|-----------|----------|
| 1. Скачивание | yt-dlp / загрузка | Видео из URL или локальный файл |
| 2. Разделение | demucs (опц.) | Голос + фон без голоса |
| 3. Транскрипция | Плагины | 4 движка, WhisperX с диаризацией спикеров |
| 4. Перевод | Плагины | 5 провайдеров, чанками по 50 фраз |
| 5. TTS | Плагины | 5 движков, мульти-спикер, клонирование |
| 6. Сборка | FFmpeg | Полный timeline + замедление + микширование |

### Плагин-система

Все движки транскрипции, перевода и TTS реализованы как плагины — файлы `.py` в соответствующих папках. Чтобы добавить новый движок — создайте файл. Чтобы удалить — удалите файл.

```
plugins/
├── transcribe/                  # Движки транскрипции
│   ├── __init__.py              # Авто-обнаружение плагинов
│   ├── openai_whisper.py        # OpenAI Whisper (.venv-whisper)
│   ├── openai_whisper_worker.py # Worker-процесс
│   ├── faster_whisper.py        # Faster Whisper (.venv-faster-whisper)
│   ├── faster_whisper_worker.py # Worker-процесс
│   ├── whisper_api.py           # OpenAI Whisper API (облако, без venv)
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
└── tts/                         # TTS движки
    ├── __init__.py              # Авто-обнаружение плагинов
    ├── qwen3.py                 # Qwen3-TTS (.venv-qwen3)
    ├── qwen3_worker.py          # Persistent worker-процесс
    ├── edge.py                  # Edge TTS (без venv)
    ├── macos_say.py             # macOS Say (системный)
    ├── omnivoice_tts.py         # OmniVoice (.venv-omnivoice)
    └── omnivoice_worker.py      # Persistent worker-процесс
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

### Изолированные окружения

Каждый ML-движок работает в своём виртуальном окружении через worker-процессы. Основной проект лёгкий — только Flask, API клиенты, edge-tts.

| Окружение | Пакеты | Когда создаётся |
|-----------|--------|----------------|
| `.venv-whisper` | openai-whisper, torch | при первом запуске OpenAI Whisper |
| `.venv-faster-whisper` | faster-whisper | при первом запуске Faster Whisper |
| `.venv-whisperx` | whisperx, pyannote-audio, torch | при первом запуске WhisperX |
| `.venv-qwen3` | qwen-tts, transformers, torch | при первом запуске Qwen3-TTS |
| `.venv-omnivoice` | omnivoice, torch | при первом запуске OmniVoice |
| `.venv-demucs` | demucs, torch | при первом разделении голоса |

Все окружения можно создать заранее: `uv run python setup_all.py`

### Закадровый перевод (voiceover)

Режим сборки "Закадровый перевод" микширует 3 аудио-дорожки:
- **Фон** (demucs, без голоса) — настраиваемая громкость
- **Оригинальный голос** (demucs, только голос) — настраиваемая громкость
- **Переведённый голос** (TTS) — 100%

В промежутках между субтитрами оригинальный спикер слышен на фоне. На TTS-сегментах все три дорожки микшируются.

### Мульти-спикер

1. **WhisperX + pyannote** определяет количество и границы спикеров
2. В субтитрах каждая фраза помечена спикером (S0, S1...) — кликом можно переназначить
3. В панели "Голоса спикеров" каждому спикеру назначается свой TTS движок и голос
4. При генерации TTS субтитры группируются по спикерам — каждый озвучивается своим голосом
5. В аудио редакторе — отдельная дорожка для каждого спикера с цветовой кодировкой

### TTS движки

| Движок | Тип | Клон голоса | Встроенные голоса |
|--------|-----|-------------|-------------------|
| Qwen3-TTS Base | Локально | Да (WAV референс) | Нет |
| Qwen3-TTS CustomVoice | Локально | Нет | Vivian, Ryan, Aiden и др. |
| Edge TTS | Облако (бесплатно) | Нет | 300+ голосов на всех языках |
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
├── plugins/
│   ├── transcribe/         # Плагины транскрипции + workers
│   ├── translate/          # Плагины перевода
│   └── tts/                # Плагины TTS + workers
├── .venv-whisper/          # OpenAI Whisper (создаётся автоматически)
├── .venv-faster-whisper/   # Faster Whisper
├── .venv-whisperx/         # WhisperX + pyannote
├── .venv-qwen3/            # Qwen3-TTS
├── .venv-omnivoice/        # OmniVoice
├── .venv-demucs/           # Demucs
├── models/
│   ├── whisper/
│   │   ├── openai/         # OpenAI Whisper модели (.pt)
│   │   ├── faster-whisper/ # Faster Whisper модели
│   │   └── whisperx/       # WhisperX + align + diarize модели
│   └── tts/
│       └── hub/            # TTS модели (HuggingFace)
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
