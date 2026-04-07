#!/usr/bin/env python3
"""
Video Translator — pipeline functions.
Скачивание → транскрипция → перевод → TTS → сборка
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
os.makedirs(MODELS_DIR, exist_ok=True)
os.environ.setdefault("HF_HOME", MODELS_DIR)

LANGUAGES = {
    "Русский":     "Russian",
    "English":     "English",
    "Español":     "Spanish",
    "Français":    "French",
    "Deutsch":     "German",
    "中文":        "Chinese",
    "日本語":      "Japanese",
    "한국어":      "Korean",
    "Português":   "Portuguese",
    "Italiano":    "Italian",
    "Polski":      "Polish",
    "Türkçe":      "Turkish",
    "العربية":     "Arabic",
    "हिन्दी":     "Hindi",
}

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"]


# ──────────────────────────────────────────────────────────────────────────────
# УТИЛИТЫ — SRT
# ──────────────────────────────────────────────────────────────────────────────

def parse_srt(srt_text: str) -> list[dict]:
    """Парсит SRT-файл в список словарей {index, start, end, text}."""
    blocks = re.split(r'\n\s*\n', srt_text.strip())
    subtitles = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
            times = lines[1].strip()
            m = re.match(
                r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})',
                times
            )
            if not m:
                continue
            start = (int(m.group(1))*3600 + int(m.group(2))*60 +
                     int(m.group(3)) + int(m.group(4))/1000)
            end   = (int(m.group(5))*3600 + int(m.group(6))*60 +
                     int(m.group(7)) + int(m.group(8))/1000)
            text = "\n".join(lines[2:]).strip()
            subtitles.append({"index": idx, "start": start, "end": end, "text": text})
        except (ValueError, IndexError):
            continue
    return subtitles


def secs_to_srt_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    ms = int(round((s - int(s)) * 1000))
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def write_srt(subtitles: list[dict], path: str):
    with open(path, "w", encoding="utf-8") as f:
        for sub in subtitles:
            f.write(f"{sub['index']}\n")
            f.write(f"{secs_to_srt_time(sub['start'])} --> {secs_to_srt_time(sub['end'])}\n")
            f.write(f"{sub['text']}\n\n")


# ──────────────────────────────────────────────────────────────────────────────
# ШАГИ ОБРАБОТКИ
# ──────────────────────────────────────────────────────────────────────────────

class ProcessingError(Exception):
    pass


def check_dependencies(log):
    """Проверяет наличие всех зависимостей."""
    missing = []
    for tool in ["yt-dlp", "ffmpeg"]:
        if not shutil.which(tool):
            missing.append(tool)
    try:
        import whisper
    except ImportError:
        missing.append("openai-whisper (pip install openai-whisper)")
    try:
        import anthropic
    except ImportError:
        missing.append("anthropic (pip install anthropic)")
    if missing:
        raise ProcessingError(
            "Не найдены зависимости:\n" + "\n".join(f"  • {m}" for m in missing)
        )
    log("✅ Все зависимости найдены")


def download_video(url: str, out_dir: str, log) -> str:
    """Скачивает видео через yt-dlp, возвращает путь к файлу."""
    log(f"⬇️  Скачиваю видео: {url}")
    out_template = os.path.join(out_dir, "source.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", out_template,
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProcessingError(f"yt-dlp ошибка:\n{result.stderr}")
    # Найти скачанный файл
    for f in Path(out_dir).glob("source.*"):
        if f.suffix in (".mp4", ".mkv", ".webm", ".avi"):
            log(f"✅ Видео скачано: {f.name}")
            return str(f)
    raise ProcessingError("Не удалось найти скачанный файл")


def extract_audio(video_path: str, out_dir: str, log) -> str:
    """Извлекает аудио из видео в WAV 16kHz mono."""
    log("🎵 Извлекаю аудио...")
    audio_path = os.path.join(out_dir, "audio.wav")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-ar", "16000", "-ac", "1", "-vn",
        audio_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProcessingError(f"ffmpeg (audio) ошибка:\n{result.stderr}")
    log("✅ Аудио извлечено")
    return audio_path


def transcribe_audio(audio_path: str, out_dir: str, model_name: str, log) -> list[dict]:
    """Транскрибирует аудио через Whisper, возвращает субтитры."""
    log(f"🎙️  Транскрибирую (модель: {model_name})...")
    import whisper
    whisper_cache = os.path.join(MODELS_DIR, "whisper")
    os.makedirs(whisper_cache, exist_ok=True)
    model = whisper.load_model(model_name, download_root=whisper_cache)
    result = model.transcribe(audio_path, task="transcribe", verbose=False)
    subtitles = []
    for i, seg in enumerate(result["segments"], 1):
        subtitles.append({
            "index": i,
            "start": seg["start"],
            "end":   seg["end"],
            "text":  seg["text"].strip(),
        })
    srt_path = os.path.join(out_dir, "original.srt")
    write_srt(subtitles, srt_path)
    log(f"✅ Транскрипция готова: {len(subtitles)} фраз")
    return subtitles


def translate_subtitles(subtitles: list[dict], target_lang: str,
                         api_key: str, out_dir: str, log) -> list[dict]:
    """Переводит субтитры через Claude."""
    import anthropic
    log(f"🌐 Перевожу на {target_lang} через Claude...")
    client = anthropic.Anthropic(api_key=api_key)

    # Разбиваем на чанки по 50 строк для надёжности
    chunk_size = 50
    translated = []

    for i in range(0, len(subtitles), chunk_size):
        chunk = subtitles[i:i + chunk_size]
        numbered = "\n".join(
            f"{sub['index']}|{sub['text']}" for sub in chunk
        )
        prompt = (
            f"Translate the following subtitles to {target_lang}. "
            f"Keep the same line numbering format (number|text). "
            f"Preserve natural speech rhythm. Return ONLY the translated lines, no explanations.\n\n"
            f"{numbered}"
        )
        resp = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        # Парсим ответ
        tr_map = {}
        for line in raw.splitlines():
            if "|" in line:
                parts = line.split("|", 1)
                try:
                    tr_map[int(parts[0].strip())] = parts[1].strip()
                except ValueError:
                    pass
        for sub in chunk:
            tr_text = tr_map.get(sub["index"], sub["text"])
            translated.append({**sub, "text": tr_text})
        log(f"   Переведено {min(i+chunk_size, len(subtitles))}/{len(subtitles)} фраз")

    srt_path = os.path.join(out_dir, "translated.srt")
    write_srt(translated, srt_path)
    log("✅ Перевод готов")
    return translated


def synthesize_speech(subtitles: list[dict], out_dir: str, log) -> list[dict]:
    """
    Синтезирует речь для каждого субтитра через Qwen3-TTS (qwen_tts).
    Возвращает субтитры с добавленным полем 'audio_path'.
    """
    log("🔊 Синтезирую речь (Qwen3-TTS)...")

    # Пробуем импортировать qwen_tts / transformers pipeline
    try:
        from transformers import pipeline as hf_pipeline
        tts = hf_pipeline("text-to-speech", model="Qwen/Qwen3-TTS")
        use_transformers = True
    except Exception:
        use_transformers = False

    if not use_transformers:
        # Fallback: pyttsx3 для демонстрации
        try:
            import pyttsx3
            engine = pyttsx3.init()
            use_pyttsx3 = True
        except Exception:
            raise ProcessingError(
                "Не удалось загрузить TTS. Установите:\n"
                "  pip install transformers torch\n"
                "или\n"
                "  pip install pyttsx3"
            )

    audio_dir = os.path.join(out_dir, "tts_audio")
    os.makedirs(audio_dir, exist_ok=True)

    result_subs = []
    for sub in subtitles:
        audio_path = os.path.join(audio_dir, f"seg_{sub['index']:04d}.wav")
        if use_transformers:
            output = tts(sub["text"])
            import soundfile as sf
            sf.write(audio_path, output["audio"], output["sampling_rate"])
        else:
            engine.save_to_file(sub["text"], audio_path)
            engine.runAndWait()
        result_subs.append({**sub, "audio_path": audio_path})
        if sub["index"] % 10 == 0:
            log(f"   TTS: {sub['index']}/{len(subtitles)}")

    log("✅ Синтез речи завершён")
    return result_subs


def get_audio_duration(audio_path: str) -> float:
    """Возвращает длительность аудиофайла в секундах через ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        audio_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    for stream in data.get("streams", []):
        if "duration" in stream:
            return float(stream["duration"])
    return 0.0


def build_final_video(video_path: str, subtitles_with_audio: list[dict],
                       out_path: str, out_dir: str, log):
    """
    Склеивает финальное видео:
    - Для каждого сегмента: если TTS длиннее слота → замедляет видео-сегмент (atempo/setpts)
    - Строит filter_complex и запускает ffmpeg
    """
    log("🎬 Собираю финальное видео...")

    tmp_dir = os.path.join(out_dir, "segments")
    os.makedirs(tmp_dir, exist_ok=True)

    segment_files = []  # список (video_seg, audio_seg)

    total = len(subtitles_with_audio)
    for i, sub in enumerate(subtitles_with_audio):
        slot_dur  = sub["end"] - sub["start"]
        audio_dur = get_audio_duration(sub["audio_path"])
        if audio_dur < 0.05:
            audio_dur = slot_dur  # на случай ошибки TTS

        speed_factor = audio_dur / slot_dur if audio_dur > slot_dur else 1.0
        # Ограничиваем замедление (не более 3x)
        speed_factor = min(speed_factor, 3.0)

        v_seg = os.path.join(tmp_dir, f"v_{i:04d}.mp4")
        a_seg = os.path.join(tmp_dir, f"a_{i:04d}.wav")

        # --- Вырезаем видео-сегмент и при необходимости замедляем ---
        vf = f"setpts={speed_factor:.4f}*PTS"
        # atempo поддерживает только 0.5–2.0 за один шаг
        if speed_factor <= 1.0:
            af = "anull"
        elif speed_factor <= 2.0:
            af = f"atempo={1/speed_factor:.4f}"
        else:
            # Два прохода atempo
            f1 = max(0.5, 1/speed_factor * 2)
            f2 = (1/speed_factor) / f1 * 2 if f1 > 0 else 0.5
            af = f"atempo={f1:.4f},atempo={f2:.4f}"

        cmd_v = [
            "ffmpeg", "-y",
            "-ss", str(sub["start"]),
            "-t",  str(slot_dur),
            "-i",  video_path,
            "-vf", vf,
            "-af", af,
            "-c:v", "libx264", "-preset", "fast",
            "-an",           # без оригинального аудио
            v_seg
        ]
        subprocess.run(cmd_v, capture_output=True)

        # --- Подготавливаем TTS-аудио (обрезаем / дополняем тишиной) ---
        target_dur = slot_dur * speed_factor  # реальная длительность видео-сегмента
        cmd_a = [
            "ffmpeg", "-y",
            "-i", sub["audio_path"],
            "-t", str(target_dur),
            "-af", f"apad=whole_dur={target_dur:.3f}",
            "-ar", "44100", "-ac", "2",
            a_seg
        ]
        subprocess.run(cmd_a, capture_output=True)

        segment_files.append((v_seg, a_seg))

        if (i + 1) % 10 == 0 or i == total - 1:
            log(f"   Сегментов: {i+1}/{total}")

    # --- Создаём concat-файлы ---
    log("🔗 Конкатенирую сегменты...")
    v_list = os.path.join(tmp_dir, "vlist.txt")
    a_list = os.path.join(tmp_dir, "alist.txt")
    with open(v_list, "w") as fv, open(a_list, "w") as fa:
        for v_seg, a_seg in segment_files:
            fv.write(f"file '{v_seg}'\n")
            fa.write(f"file '{a_seg}'\n")

    v_concat = os.path.join(tmp_dir, "video_concat.mp4")
    a_concat = os.path.join(tmp_dir, "audio_concat.wav")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", v_list, "-c", "copy", v_concat
    ], capture_output=True)

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", a_list, "-c", "copy", a_concat
    ], capture_output=True)

    # --- Финальное объединение ---
    log("🎞️  Финальная сборка...")
    cmd_final = [
        "ffmpeg", "-y",
        "-i", v_concat,
        "-i", a_concat,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        out_path
    ]
    result = subprocess.run(cmd_final, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProcessingError(f"ffmpeg (финал) ошибка:\n{result.stderr}")
    log(f"✅ Готово! Файл: {out_path}")
