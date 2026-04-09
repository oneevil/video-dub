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
WHISPER_MODELS_DIR = os.path.join(MODELS_DIR, "whisper")
TTS_MODELS_DIR = os.path.join(MODELS_DIR, "tts")
os.makedirs(WHISPER_MODELS_DIR, exist_ok=True)
os.makedirs(TTS_MODELS_DIR, exist_ok=True)
os.environ.setdefault("HF_HOME", TTS_MODELS_DIR)

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
    "Українська":  "uk",
}

WHISPER_MODELS = ["turbo", "large-v3-turbo", "large-v3", "large-v2", "large", "medium", "small", "base", "tiny"]

SOURCE_LANGUAGES = {
    "English":     "en",
    "Русский":     "ru",
    "Español":     "es",
    "Français":    "fr",
    "Deutsch":     "de",
    "中文":        "zh",
    "日本語":      "ja",
    "한국어":      "ko",
    "Português":   "pt",
    "Italiano":    "it",
    "Polski":      "pl",
    "Türkçe":      "tr",
    "Українська":  "uk",
}

# Dynamic transcription engine discovery
from plugins.transcribe import discover_plugins as _discover_transcribe
TRANSCRIBE_ENGINES, _TRANSCRIBE_PLUGINS = _discover_transcribe()

# Dynamic translation engine discovery
from plugins.translate import discover_plugins as _discover_translate
TRANSLATE_ENGINES, _TRANSLATE_PLUGINS = _discover_translate()
TRANSLATE_PROVIDERS = list(TRANSLATE_ENGINES.keys())


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


def write_speaker_map(subtitles, path):
    """Save speaker assignments: {index_str: speaker_label}."""
    mapping = {str(sub["index"]): sub.get("speaker", "") for sub in subtitles if sub.get("speaker")}
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(mapping, ensure_ascii=False, indent=2))


def load_speaker_map(path, subtitles):
    """Load speaker assignments into subtitle list."""
    if not os.path.exists(path):
        return subtitles
    with open(path, encoding="utf-8") as f:
        mapping = json.loads(f.read())
    for sub in subtitles:
        key = str(sub["index"])
        if key in mapping:
            sub["speaker"] = mapping[key]
    return subtitles


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
        "ffmpeg", "-y",
        "-i", video_path,
        "-ar", "16000", "-ac", "1", "-vn",
        "-acodec", "pcm_s16le",
        "-map", "0:a:0",
        audio_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProcessingError(f"ffmpeg (audio) ошибка:\n{result.stderr}")
    log("✅ Аудио извлечено")
    return audio_path


DEMUCS_VENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv-demucs")
_DEMUCS_DEPS = ["demucs>=4.0.0", "torch>=2.8.0", "torchaudio>=2.8.0"]


def _setup_demucs_venv(log):
    """Create isolated venv for demucs if needed."""
    python = os.path.join(DEMUCS_VENV, "bin", "python")
    if os.path.exists(python):
        return
    import sys as _sys
    log("   📦 Создаю окружение demucs...")
    subprocess.run([_sys.executable, "-m", "venv", DEMUCS_VENV], check=True)
    log("   📦 Устанавливаю зависимости...")
    result = subprocess.run(
        [os.path.join(DEMUCS_VENV, "bin", "pip"), "install", "--quiet"] + _DEMUCS_DEPS,
        capture_output=True, text=True)
    if result.returncode != 0:
        raise ProcessingError(f"Ошибка установки demucs: {result.stderr[:500]}")
    log("   ✅ Окружение demucs готово")


def separate_vocals(audio_path: str, out_dir: str, log) -> tuple[str, str]:
    """Разделяет аудио на голос и фон через demucs (isolated venv). Возвращает (vocals_path, no_vocals_path)."""
    vocals_path = os.path.join(out_dir, "vocals.wav")
    no_vocals_path = os.path.join(out_dir, "no_vocals.wav")

    # Skip if already separated
    if os.path.exists(vocals_path) and os.path.exists(no_vocals_path):
        log("⏭️ Разделение уже выполнено")
        return vocals_path, no_vocals_path

    _setup_demucs_venv(log)

    log("🎙️ Разделяю голос и фон (demucs)...")
    demucs_out = os.path.join(out_dir, "demucs_out")
    python = os.path.join(DEMUCS_VENV, "bin", "python")
    cmd = [
        python, "-m", "demucs",
        "--two-stems=vocals",
        "-o", demucs_out,
        "--filename", "{stem}.{ext}",
        audio_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProcessingError(f"demucs ошибка:\n{result.stderr}")

    # Find output files — demucs creates subdir named after model
    for model_dir in Path(demucs_out).iterdir():
        if model_dir.is_dir():
            v = model_dir / "vocals.wav"
            nv = model_dir / "no_vocals.wav"
            if v.exists():
                shutil.move(str(v), vocals_path)
            if nv.exists():
                shutil.move(str(nv), no_vocals_path)
            break

    # Cleanup demucs temp
    shutil.rmtree(demucs_out, ignore_errors=True)

    if not os.path.exists(vocals_path):
        raise ProcessingError("demucs: vocals.wav не найден")

    log("✅ Голос и фон разделены")
    return vocals_path, no_vocals_path


def transcribe_audio(audio_path: str, out_dir: str, model_name: str, log,
                      source_language: str = "", engine: str = "openai-whisper",
                      api_key: str = "", num_speakers: int = 0,
                      on_segment=None) -> list[dict]:
    """Транскрибирует аудио через выбранный движок (plugin system)."""
    plugin = _TRANSCRIBE_PLUGINS.get(engine)
    if not plugin:
        raise ProcessingError(f"Транскрипция движок '{engine}' не найден")
    return plugin.transcribe(audio_path, out_dir, model_name, log,
                             source_language=source_language, api_key=api_key,
                             num_speakers=num_speakers, on_segment=on_segment)


def translate_subtitles(subtitles: list[dict], target_lang: str,
                         api_key: str, out_dir: str, log,
                         provider: str = "claude",
                         model: str = "",
                         base_url: str = "",
                         on_chunk=None) -> list[dict]:
    """Переводит субтитры через выбранный провайдер (plugin system)."""
    plugin = _TRANSLATE_PLUGINS.get(provider)
    if not plugin:
        raise ProcessingError(f"Провайдер перевода '{provider}' не найден")
    return plugin.translate(subtitles, target_lang, out_dir, log,
                            api_key=api_key, model=model, base_url=base_url,
                            on_chunk=on_chunk)


from plugins.tts import discover_plugins

# Dynamic TTS engine discovery
TTS_ENGINES, _TTS_PLUGINS = discover_plugins()

VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")
os.makedirs(VOICES_DIR, exist_ok=True)


def get_voices() -> list[dict]:
    """List saved voice profiles from voices/ directory."""
    voices = []
    for d in sorted(Path(VOICES_DIR).iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        with open(meta_path, encoding="utf-8") as f:
            meta = json.loads(f.read())
        samples = []
        for s in meta.get("samples", []):
            wav_path = d / s["file"]
            if wav_path.exists():
                samples.append({
                    "file": s["file"],
                    "text": s.get("text", ""),
                    "path": str(wav_path),
                })
        if samples:
            voices.append({
                "name": d.name,
                "path": str(d),
                "samples": samples,
            })
    return voices


def _get_voice_reference(voice_name: str) -> tuple[str, str]:
    """Return (wav_path, text) for a voice — picks first sample."""
    voice_dir = Path(VOICES_DIR) / voice_name
    meta_path = voice_dir / "meta.json"
    if not meta_path.exists():
        return "", ""
    with open(meta_path, encoding="utf-8") as f:
        meta = json.loads(f.read())
    for s in meta.get("samples", []):
        wav = voice_dir / s["file"]
        if wav.exists():
            return str(wav), s.get("text", "")
    return "", ""


def synthesize_speech(subtitles: list[dict], out_dir: str, log,
                       engine: str = "qwen3-1.7b-base",
                       voice: str = "",
                       voice_wav: str = "",
                       voice_text: str = "",
                       seed: int = 44,
                       temperature: float = 0.7,
                       speaker_voice_map: dict | None = None,
                       on_segment=None) -> list[dict]:
    """Синтезирует речь через выбранный движок (plugin system)."""
    if speaker_voice_map:
        return _tts_multi_speaker(subtitles, out_dir, log, speaker_voice_map, seed, temperature, on_segment=on_segment)
    plugin = _TTS_PLUGINS.get(engine)
    if not plugin:
        raise ProcessingError(f"TTS движок '{engine}' не найден")
    return plugin.synthesize(subtitles, out_dir, log, engine=engine, voice=voice,
                             voice_wav=voice_wav, voice_text=voice_text, seed=seed,
                             temperature=temperature, on_segment=on_segment)


def _tts_multi_speaker(subtitles: list[dict], out_dir: str, log,
                        speaker_voice_map: dict, seed: int = 44,
                        temperature: float = 0.7, on_segment=None) -> list[dict]:
    """Синтезирует речь для нескольких спикеров с разными голосами/движками."""
    log("🔊 Синтезирую речь (мульти-спикер)...")

    # Group subtitles by speaker
    from collections import defaultdict
    speaker_groups = defaultdict(list)
    for sub in subtitles:
        speaker = sub.get("speaker", "")
        if speaker and speaker in speaker_voice_map:
            speaker_groups[speaker].append(sub)
        else:
            # Fallback: use first speaker config or skip
            speaker_groups[speaker or "_default"].append(sub)

    log(f"   👥 Спикеров: {len(speaker_groups)}")
    all_results = []

    for speaker, subs in speaker_groups.items():
        voice_cfg = speaker_voice_map.get(speaker, {})
        if not voice_cfg:
            voice_cfg = next(iter(speaker_voice_map.values()), {})
        engine = voice_cfg.get("engine", "edge-tts")
        voice = voice_cfg.get("voice", "")

        # Reset clone cache for qwen3 plugin if available
        plugin = _TTS_PLUGINS.get(engine)
        if plugin and hasattr(plugin, 'reset_clone_cache'):
            plugin.reset_clone_cache()

        log(f"   🎤 {speaker}: {engine}, голос: {voice} ({len(subs)} фраз)")

        if not plugin:
            raise ProcessingError(f"TTS движок '{engine}' не найден")

        voice_wav = ""
        voice_text = ""
        if voice:
            voice_wav, voice_text = _get_voice_reference(voice)

        result = plugin.synthesize(subs, out_dir, log, engine=engine, voice=voice,
                                   voice_wav=voice_wav, voice_text=voice_text,
                                   seed=seed, temperature=temperature, on_segment=on_segment)
        all_results.extend(result)

    # Sort by index to restore original order
    all_results.sort(key=lambda s: s["index"])
    log("✅ Мульти-спикер синтез завершён")
    return all_results


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


BUILD_FORMATS = ["mp4", "mkv", "webm"]
BUILD_CODECS = {
    "libx264": "H.264",
    "libx265": "H.265 (HEVC)",
    "copy": "Без перекодирования",
}
BUILD_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
BUILD_AUDIO_BITRATES = ["128k", "192k", "256k", "320k"]


def build_final_video(video_path: str, subtitles_with_audio: list[dict],
                       out_path: str, out_dir: str, log,
                       codec: str = "libx264",
                       preset: str = "fast",
                       audio_bitrate: str = "128k",
                       max_slowdown: float = 3.0,
                       original_audio_mode: str = "none",
                       original_audio_volume: float = 0.1,
                       no_vocals_volume: float = 0.5,
                       vocals_volume: float = 0.15,
                       burn_subtitles: bool = False,
                       srt_path: str = "",
                       start_sec: float = 0,
                       end_sec: float = 0):
    """
    Склеивает финальное видео с настройками.
    """
    log("🎬 Собираю финальное видео...")
    log(f"   ⚙️ Кодек: {codec}, пресет: {preset}, аудио: {audio_bitrate}, макс. замедление: {max_slowdown}x")

    tmp_dir = os.path.join(out_dir, "segments")
    os.makedirs(tmp_dir, exist_ok=True)

    # Get total video duration
    probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path]
    probe_r = subprocess.run(probe_cmd, capture_output=True, text=True)
    video_duration = float(json.loads(probe_r.stdout).get("format", {}).get("duration", 0))

    v_codec = codec if codec != "copy" else "libx264"

    # Apply time range
    range_start = start_sec if start_sec > 0 else 0.0
    range_end = end_sec if end_sec > 0 else video_duration
    range_end = min(range_end, video_duration)
    if range_start > 0 or range_end < video_duration:
        log(f"   ✂️ Диапазон: {range_start}с — {range_end}с")

    # Sort subtitles by start time
    subs_sorted = sorted(subtitles_with_audio, key=lambda s: s["start"])

    # Build list of all segments: gaps + TTS slots
    timeline = []  # (type, start, dur, sub_or_None, speed_factor)
    cursor = range_start

    for sub in subs_sorted:
        # Skip subtitles outside range
        if sub["end"] <= range_start or sub["start"] >= range_end:
            continue

        # Gap before this subtitle
        gap = sub["start"] - cursor
        if gap > 0.01:
            timeline.append(("gap", cursor, gap, None, 1.0))

        slot_dur = sub["end"] - sub["start"]
        audio_dur = get_audio_duration(sub["audio_path"])
        if audio_dur < 0.05:
            audio_dur = slot_dur

        speed_factor = audio_dur / slot_dur if audio_dur > slot_dur else 1.0
        speed_factor = min(speed_factor, max_slowdown)

        timeline.append(("tts", sub["start"], slot_dur, sub, speed_factor))
        cursor = sub["end"]

    # Trailing gap after last subtitle
    if cursor < range_end - 0.01:
        timeline.append(("gap", cursor, range_end - cursor, None, 1.0))

    segment_files = []
    total = len(timeline)

    for i, (seg_type, seg_start, seg_dur, sub, speed_factor) in enumerate(timeline):
        v_seg = os.path.join(tmp_dir, f"v_{i:04d}.mp4")
        a_seg = os.path.join(tmp_dir, f"a_{i:04d}.wav")

        if seg_type == "gap":
            # Normal speed gap — keep original video
            cmd_v = [
                "ffmpeg", "-y",
                "-ss", str(seg_start), "-t", str(seg_dur),
                "-i", video_path,
                "-c:v", v_codec, "-preset", preset,
                "-an", v_seg
            ]
            subprocess.run(cmd_v, capture_output=True)
            # Gap audio depends on original_audio_mode
            if original_audio_mode in ("no_vocals", "voiceover"):
                bg_file = os.path.join(out_dir, "no_vocals.wav")
                vocals_file = os.path.join(out_dir, "vocals.wav")
                if os.path.exists(bg_file):
                    if original_audio_mode == "voiceover" and os.path.exists(vocals_file):
                        # Voiceover gap: bg + vocals (original speaker audible in gaps)
                        bg_tmp_g = os.path.join(tmp_dir, f"gbg_{i:04d}.wav")
                        voc_tmp_g = os.path.join(tmp_dir, f"gvoc_{i:04d}.wav")
                        subprocess.run([
                            "ffmpeg", "-y", "-ss", str(seg_start), "-t", str(seg_dur),
                            "-i", bg_file, "-ar", "44100", "-ac", "2", bg_tmp_g
                        ], capture_output=True)
                        subprocess.run([
                            "ffmpeg", "-y", "-ss", str(seg_start), "-t", str(seg_dur),
                            "-i", vocals_file, "-ar", "44100", "-ac", "2", voc_tmp_g
                        ], capture_output=True)
                        subprocess.run([
                            "ffmpeg", "-y", "-i", bg_tmp_g, "-i", voc_tmp_g,
                            "-filter_complex",
                            f"[0:a]volume={no_vocals_volume:.2f}[bg];[1:a]volume={vocals_volume:.2f}[voc];[bg][voc]amix=inputs=2:duration=first[out]",
                            "-map", "[out]", "-ar", "44100", "-ac", "2", a_seg
                        ], capture_output=True)
                    else:
                        cmd_a = [
                            "ffmpeg", "-y",
                            "-ss", str(seg_start), "-t", str(seg_dur),
                            "-i", bg_file,
                            "-ar", "44100", "-ac", "2", a_seg
                        ]
                        subprocess.run(cmd_a, capture_output=True)
                else:
                    # Fallback to silence
                    cmd_a = [
                        "ffmpeg", "-y",
                        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
                        "-t", str(seg_dur), a_seg
                    ]
                    subprocess.run(cmd_a, capture_output=True)
            elif original_audio_mode == "full":
                cmd_a = [
                    "ffmpeg", "-y",
                    "-ss", str(seg_start), "-t", str(seg_dur),
                    "-i", video_path,
                    "-vn", "-ar", "44100", "-ac", "2", a_seg
                ]
                subprocess.run(cmd_a, capture_output=True)
            else:
                # none — silence
                cmd_a = [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", str(seg_dur), a_seg
                ]
                subprocess.run(cmd_a, capture_output=True)
        else:
            # TTS segment — possibly slowed down
            vf = f"setpts={speed_factor:.4f}*PTS"
            cmd_v = [
                "ffmpeg", "-y",
                "-ss", str(seg_start), "-t", str(seg_dur),
                "-i", video_path,
                "-vf", vf,
                "-c:v", v_codec, "-preset", preset,
                "-an", v_seg
            ]
            subprocess.run(cmd_v, capture_output=True)

            # TTS audio padded/trimmed to match slowed video duration
            target_dur = seg_dur * speed_factor
            tts_tmp = os.path.join(tmp_dir, f"tts_{i:04d}.wav")
            cmd_a = [
                "ffmpeg", "-y",
                "-i", sub["audio_path"],
                "-t", str(target_dur),
                "-af", f"apad=whole_dur={target_dur:.3f}",
                "-ar", "44100", "-ac", "2",
                tts_tmp
            ]
            subprocess.run(cmd_a, capture_output=True)

            # Mix TTS with background audio if requested
            if original_audio_mode in ("full", "no_vocals", "voiceover"):
                bg_file = os.path.join(out_dir, "no_vocals.wav") if original_audio_mode in ("no_vocals", "voiceover") else None
                if bg_file and os.path.exists(bg_file):
                    bg_src = bg_file
                elif original_audio_mode == "full":
                    bg_src = video_path
                else:
                    bg_src = None

                if bg_src:
                    vol = no_vocals_volume if original_audio_mode in ("no_vocals", "voiceover") else original_audio_volume
                    bg_tmp = os.path.join(tmp_dir, f"bg_{i:04d}.wav")
                    # Extract background for this time range (original timing, not slowed)
                    if bg_src == video_path:
                        subprocess.run([
                            "ffmpeg", "-y", "-ss", str(seg_start), "-t", str(seg_dur),
                            "-i", bg_src, "-vn", "-ar", "44100", "-ac", "2", bg_tmp
                        ], capture_output=True)
                    else:
                        subprocess.run([
                            "ffmpeg", "-y", "-ss", str(seg_start), "-t", str(seg_dur),
                            "-i", bg_src, "-ar", "44100", "-ac", "2", bg_tmp
                        ], capture_output=True)
                    # Stretch background to match slowed duration if needed
                    if speed_factor > 1.0:
                        bg_stretched = os.path.join(tmp_dir, f"bgs_{i:04d}.wav")
                        atempo = 1.0 / speed_factor
                        if atempo >= 0.5:
                            af_bg = f"atempo={atempo:.4f}"
                        else:
                            f1 = max(0.5, atempo * 2)
                            f2 = atempo / f1
                            af_bg = f"atempo={f1:.4f},atempo={f2:.4f}"
                        subprocess.run([
                            "ffmpeg", "-y", "-i", bg_tmp,
                            "-af", af_bg, "-ar", "44100", "-ac", "2", bg_stretched
                        ], capture_output=True)
                        os.replace(bg_stretched, bg_tmp)

                    # Voiceover mode: mix 3 tracks (bg + original vocals + TTS)
                    if original_audio_mode == "voiceover":
                        vocals_file = os.path.join(out_dir, "vocals.wav")
                        if os.path.exists(vocals_file):
                            voc_tmp = os.path.join(tmp_dir, f"voc_{i:04d}.wav")
                            subprocess.run([
                                "ffmpeg", "-y", "-ss", str(seg_start), "-t", str(seg_dur),
                                "-i", vocals_file, "-ar", "44100", "-ac", "2", voc_tmp
                            ], capture_output=True)
                            if speed_factor > 1.0:
                                voc_stretched = os.path.join(tmp_dir, f"vocs_{i:04d}.wav")
                                subprocess.run([
                                    "ffmpeg", "-y", "-i", voc_tmp,
                                    "-af", af_bg, "-ar", "44100", "-ac", "2", voc_stretched
                                ], capture_output=True)
                                os.replace(voc_stretched, voc_tmp)
                            subprocess.run([
                                "ffmpeg", "-y", "-i", tts_tmp, "-i", bg_tmp, "-i", voc_tmp,
                                "-filter_complex",
                                f"[0:a]volume=1.0[tts];[1:a]volume={vol:.2f}[bg];[2:a]volume={vocals_volume:.2f}[voc];"
                                f"[tts][bg][voc]amix=inputs=3:duration=first[out]",
                                "-map", "[out]", "-ar", "44100", "-ac", "2", a_seg
                            ], capture_output=True)
                        else:
                            # No vocals file — fallback to 2-track
                            subprocess.run([
                                "ffmpeg", "-y", "-i", tts_tmp, "-i", bg_tmp,
                                "-filter_complex",
                                f"[0:a]volume=1.0[tts];[1:a]volume={vol:.2f}[bg];[tts][bg]amix=inputs=2:duration=first[out]",
                                "-map", "[out]", "-ar", "44100", "-ac", "2", a_seg
                            ], capture_output=True)
                    else:
                        # 2-track mix: TTS + background
                        subprocess.run([
                            "ffmpeg", "-y", "-i", tts_tmp, "-i", bg_tmp,
                            "-filter_complex",
                            f"[0:a]volume=1.0[tts];[1:a]volume={vol:.2f}[bg];[tts][bg]amix=inputs=2:duration=first[out]",
                            "-map", "[out]", "-ar", "44100", "-ac", "2", a_seg
                        ], capture_output=True)
                else:
                    os.rename(tts_tmp, a_seg)
            else:
                os.rename(tts_tmp, a_seg)

        segment_files.append((v_seg, a_seg))

        pct = int((i + 1) / total * 100)
        log(f"   📦 Сегментов: {i+1}/{total} ({pct}%)")

    # --- Apply volume crossfades at gap↔TTS boundaries ---
    if original_audio_mode in ("full", "voiceover"):
        fade_dur = 0.8  # seconds — match player lookahead
        for i in range(len(timeline)):
            seg_type = timeline[i][0]
            if seg_type != "gap":
                continue
            _, a_seg = segment_files[i]
            seg_dur = timeline[i][2]
            if seg_dur < 0.1:
                continue
            # Check if next segment is TTS → fade out end of gap
            fade_out = i + 1 < len(timeline) and timeline[i + 1][0] == "tts"
            # Check if previous segment is TTS → fade in start of gap
            fade_in = i > 0 and timeline[i - 1][0] == "tts"
            if not fade_out and not fade_in:
                continue
            af_parts = []
            fd = min(fade_dur, seg_dur / 2)
            if fade_out:
                # Fade out last fd seconds of gap audio
                af_parts.append(f"afade=t=out:st={max(0, seg_dur - fd):.3f}:d={fd:.3f}")
            if fade_in:
                # Fade in first fd seconds of gap audio
                af_parts.append(f"afade=t=in:st=0:d={fd:.3f}")
            if af_parts:
                faded = a_seg + ".faded.wav"
                subprocess.run([
                    "ffmpeg", "-y", "-i", a_seg,
                    "-af", ",".join(af_parts),
                    "-ar", "44100", "-ac", "2", faded
                ], capture_output=True)
                os.replace(faded, a_seg)

    # --- Concat ---
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

    # --- Final merge ---
    log("🎞️  Финальная сборка...")

    cmd_final = ["ffmpeg", "-y", "-i", v_concat, "-i", a_concat]
    cmd_final += ["-map", "0:v", "-map", "1:a"]

    # Burn subtitles
    if burn_subtitles and srt_path and os.path.exists(srt_path):
        escaped_srt = srt_path.replace("'", r"\'").replace(":", r"\:")
        cmd_final += ["-vf", f"subtitles='{escaped_srt}'"]
        log("   📺 Субтитры вшиты в видео")

    cmd_final += [
        "-c:v", codec if not burn_subtitles and codec == "copy" else ("libx265" if codec == "libx265" else "libx264"),
        "-preset", preset,
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-shortest",
        out_path
    ]
    result = subprocess.run(cmd_final, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProcessingError(f"ffmpeg (финал) ошибка:\n{result.stderr}")

    # Cleanup temp segments
    import shutil
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir)
        log("🧹 Временные файлы удалены")

    if original_audio_mode == "full":
        log(f"   🔉 Оригинальное аудио подмешано (громкость: {int(original_audio_volume*100)}%)")
    elif original_audio_mode == "voiceover":
        log(f"   🔉 Закадровый перевод: фон {int(no_vocals_volume*100)}%, голос {int(vocals_volume*100)}%")
    elif original_audio_mode == "no_vocals":
        log(f"   🔉 Фон без голоса подмешан (громкость: {int(no_vocals_volume*100)}%)")
    log(f"✅ Готово! Файл: {out_path}")
