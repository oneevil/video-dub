# Video Translator

GUI-приложение для автоматического перевода видео с дубляжом.

## Пайплайн

```
URL/файл → yt-dlp → Whisper → Claude (перевод) → Qwen3-TTS → FFmpeg (сборка)
```

**Ключевая фича:** если озвученная фраза длиннее своего временного слота —
видео-сегмент плавно замедляется (`setpts`), чтобы аудио точно совпадало с картинкой.

---

## Установка

### 1. Системные зависимости

```bash
# macOS
brew install ffmpeg yt-dlp

# Ubuntu/Debian
sudo apt install ffmpeg
pip install yt-dlp
```

### 2. Настройка

```bash
cp .env.example .env
# Отредактируйте .env — укажите ANTHROPIC_API_KEY и другие параметры
```

### 3. Запуск

```bash
uv run python app.py
```

Откроется на http://localhost:5000

---

## Переменные окружения (.env)

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `ANTHROPIC_API_KEY` | API ключ Anthropic | — (обязательно) |
| `WHISPER_MODEL` | Модель Whisper | `medium` |
| `OUTPUT_DIR` | Папка для результатов | `~/translated_videos` |

---

## Архитектура

| Шаг | Инструмент | Описание |
|-----|-----------|----------|
| 1. Скачивание | `yt-dlp` | Лучшее качество MP4 |
| 2. Аудио | `ffmpeg` | WAV 16kHz mono для Whisper |
| 3. Транскрипция | `openai-whisper` | С тайм-кодами сегментов |
| 4. Перевод | `Claude claude-opus-4-5` | Чанками по 50 фраз |
| 5. TTS | `Qwen3-TTS` | Синтез речи для каждой фразы |
| 6. Сборка | `ffmpeg` | Сегментирование + замедление + concat |

### Логика замедления видео

```python
speed_factor = tts_duration / slot_duration  # если > 1 — замедляем

# Видео: setpts = speed_factor * PTS
# Аудио: atempo = 1 / speed_factor
```

Ограничение: максимум 3x замедление (иначе видео выглядит неестественно).

---

## Файлы в рабочей папке

```
~/translated_videos/
└── job_20250407_143022/
    ├── source.mp4          ← скачанное видео
    ├── audio.wav           ← извлечённое аудио
    ├── original.srt        ← оригинальные субтитры
    ├── translated.srt      ← переведённые субтитры
    ├── tts_audio/
    │   ├── seg_0001.wav    ← TTS для каждой фразы
    │   └── ...
    └── segments/           ← временные видео-сегменты
translated_russian_20250407_143022.mp4  ← РЕЗУЛЬТАТ
```
