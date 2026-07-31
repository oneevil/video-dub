"""WhisperX plugin -- transcription with speaker diarization (isolated venv)."""
import os
import sys
import json
import subprocess as _sp

from pipeline import safe_cwd

_VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"


ENGINES = {"whisperx": "WhisperX + Диаризация (локально)"}

MODELS = ["turbo", "large-v3-turbo", "large-v3", "large-v2", "large", "medium", "small", "base", "tiny"]

DOWNLOAD_ENGINES = [
    {"value": "whisperx", "label": "WhisperX"},
    {"value": "whisperx-align", "label": "WhisperX Align (wav2vec2)"},
    {"value": "whisperx-diarize", "label": "WhisperX Diarization (pyannote)"},
]

WHISPERX_VENV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".venv-whisperx")

_WHISPERX_DEPS = [
    "whisperx>=3.8.0",
    "pyannote-audio>=4.0.0",
    "torch>=2.8.0",
    "torchaudio>=2.8.0",
]

# whisperx объявляет requires-python <3.14, поэтому на более новом интерпретаторе
# pip отвергает вообще все версии пакета. Окружение строим на подходящем.
_MAX_PY_MINOR = 13


def _get_models_dir():
    """Каталог моделей выравнивания (wav2vec2) и диаризации (pyannote)."""
    from pipeline import WHISPER_MODELS_DIR
    return os.path.join(WHISPER_MODELS_DIR, "whisperx")


def _get_asr_dir():
    """Каталог моделей распознавания — общий с Faster Whisper."""
    from pipeline import WHISPER_ASR_DIR
    return WHISPER_ASR_DIR


def _migrate_asr_models():
    """Убирает копии моделей распознавания, оставшиеся от прежней раскладки.

    WhisperX качал их к себе, хотя внутри использует faster-whisper и берёт те
    же репозитории Systran: на диске лежали две копии по несколько гигабайт.
    """
    old, new = _get_models_dir(), _get_asr_dir()
    if not os.path.isdir(old):
        return
    import shutil as _shutil
    for d in os.listdir(old):
        if not (d.startswith("models--") and "faster-whisper" in d):
            continue
        src, dst = os.path.join(old, d), os.path.join(new, d)
        if os.path.exists(dst):
            _shutil.rmtree(src, ignore_errors=True)   # ровно та же модель уже в общем каталоге
            continue
        os.makedirs(new, exist_ok=True)
        try:
            os.replace(src, dst)
        except OSError:      # разные файловые системы
            _shutil.move(src, dst)


def _get_python():
    return os.path.join(WHISPERX_VENV, _VENV_BIN, "python")


def _get_pip():
    return os.path.join(WHISPERX_VENV, _VENV_BIN, "pip")


def _create_venv(python: str):
    """Создаёт окружение с нуля.

    Недостроенное окружение сносим: если прошлая попытка сорвалась на другой
    версии Python, venv поверх неё оставит вперемешку старые и новые файлы.
    """
    import shutil as _shutil
    _shutil.rmtree(WHISPERX_VENV, ignore_errors=True)
    _sp.run([python, "-m", "venv", WHISPERX_VENV], check=True)


def _setup_venv(log):
    """Create isolated venv for WhisperX if needed."""
    from pipeline import venv_ready, mark_venv_ready
    if venv_ready(WHISPERX_VENV):
        return
    log("   📦 Создаю изолированное окружение WhisperX...")
    from pipeline import base_python
    _create_venv(base_python(_MAX_PY_MINOR, log))
    log("   📦 Устанавливаю WhisperX + зависимости...")
    from pipeline import _torch_index_args
    result = _sp.run([_get_pip(), "install", "--quiet"] + _torch_index_args() + _WHISPERX_DEPS,
                     capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        # Хвост, а не начало: pip кладёт причину в последние строки, а сверху
        # перечисляет отброшенные версии — на 500 символах видно только их
        raise RuntimeError(f"Ошибка установки WhisperX: {result.stderr.strip()[-500:]}")
    mark_venv_ready(WHISPERX_VENV)
    log("   ✅ WhisperX окружение готово")


def download_model(engine, model, log_msg):
    """Download WhisperX / align / diarization models. Yields SSE messages."""
    import json as _json
    cache_dir = _get_models_dir()
    os.makedirs(cache_dir, exist_ok=True)
    python = _get_python()

    # Ensure venv exists
    from pipeline import venv_ready, mark_venv_ready
    if not venv_ready(WHISPERX_VENV):
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Создаю окружение WhisperX...'})}\n\n"
        from pipeline import base_python
        if sys.version_info.minor > _MAX_PY_MINOR:
            yield f"data: {_json.dumps({'type': 'log', 'message': f'📦 Готовлю Python 3.{_MAX_PY_MINOR} — WhisperX не поддерживает 3.{sys.version_info.minor}...'})}\n\n"
        try:
            py = base_python(_MAX_PY_MINOR)
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            return
        _create_venv(py)
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Устанавливаю зависимости...'})}\n\n"
        from pipeline import _torch_index_args
        result = _sp.run([_get_pip(), "install"] + _torch_index_args() + _WHISPERX_DEPS,
                         capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            # Хвост, а не начало: причина у pip в последних строках
            yield f"data: {_json.dumps({'type': 'error', 'message': f'Ошибка установки: {result.stderr.strip()[-500:]}'})}\n\n"
            return
        mark_venv_ready(WHISPERX_VENV)
        yield f"data: {_json.dumps({'type': 'log', 'message': '✅ Окружение создано'})}\n\n"

    if engine == "whisperx":
        # Модель распознавания общая с Faster Whisper — и ищем, и качаем туда же
        _migrate_asr_models()
        asr_dir = _get_asr_dir()
        os.makedirs(asr_dir, exist_ok=True)
        from plugins.transcribe.faster_whisper_plugin import find_model_dir
        if find_model_dir(model):
            yield f"data: {_json.dumps({'type': 'done', 'message': f'✅ Модель {model} уже загружена'})}\n\n"
            return
        yield f"data: {_json.dumps({'type': 'log', 'message': f'⬇️ Загружаю WhisperX модель: {model}...'})}\n\n"
        _cd = asr_dir.replace('\\', '/')
        script = (
            f"import warnings; warnings.filterwarnings('ignore'); "
            f"import logging; logging.getLogger('pytorch_lightning').setLevel(logging.ERROR); "
            f"import whisperx; "
            f"whisperx.load_model('{model}', device='cpu', compute_type='int8', download_root='{_cd}'); "
            f"print('OK')"
        )
        result = _sp.run([python, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=600, cwd=safe_cwd())
        if result.returncode != 0 or "OK" not in result.stdout:
            err = result.stderr[:500] if result.stderr else result.stdout[:500]
            if "token" in err.lower() or "401" in err or "auth" in err.lower():
                err = f"Требуется HF_TOKEN. Укажите в настройках. {err}"
            yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ {err}'})}\n\n"
        else:
            yield f"data: {_json.dumps({'type': 'done', 'message': f'✅ WhisperX модель {model} загружена'})}\n\n"

    elif engine == "whisperx-align":
        existing = [d for d in os.listdir(cache_dir)
                    if d.startswith("models--") and "wav2vec2" in d.lower()]
        if existing:
            yield f"data: {_json.dumps({'type': 'done', 'message': '✅ Align модель уже загружена'})}\n\n"
            return
        yield f"data: {_json.dumps({'type': 'log', 'message': f'⬇️ Загружаю Align модель (wav2vec2) для языка: {model}...'})}\n\n"
        _cd = cache_dir.replace('\\', '/')
        script = f"import whisperx; whisperx.load_align_model(language_code='{model}', device='cpu', model_dir='{_cd}'); print('OK')"
        result = _sp.run([python, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=300, cwd=safe_cwd())
        if result.returncode != 0 or "OK" not in result.stdout:
            err = result.stderr[:500] if result.stderr else result.stdout[:500]
            yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ {err}'})}\n\n"
        else:
            yield f"data: {_json.dumps({'type': 'done', 'message': f'✅ Align модель для {model} загружена'})}\n\n"

    elif engine == "whisperx-diarize":
        existing = [d for d in os.listdir(cache_dir)
                    if d.startswith("models--") and "diarization" in d.lower()]
        if existing:
            yield f"data: {_json.dumps({'type': 'done', 'message': '✅ Diarization модель уже загружена'})}\n\n"
            return
        hf_tok = os.environ.get("HF_TOKEN", "")
        if not hf_tok:
            yield f"data: {_json.dumps({'type': 'error', 'message': '❌ HF_TOKEN не задан. Укажите в настройках транскрипции.'})}\n\n"
            return
        yield f"data: {_json.dumps({'type': 'log', 'message': '⬇️ Загружаю Diarization модель (pyannote)...'})}\n\n"
        _cd = cache_dir.replace('\\', '/')
        script = (
            f"from whisperx.diarize import DiarizationPipeline; "
            f"DiarizationPipeline(token='{hf_tok}', device='cpu', cache_dir='{_cd}'); "
            f"print('OK')"
        )
        result = _sp.run([python, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=300, cwd=safe_cwd())
        if result.returncode != 0 or "OK" not in result.stdout:
            err = result.stderr[:500] if result.stderr else result.stdout[:500]
            yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ {err}'})}\n\n"
        else:
            yield f"data: {_json.dumps({'type': 'done', 'message': '✅ Diarization модель загружена'})}\n\n"


def list_downloaded_models():
    """List downloaded WhisperX models."""
    result = []
    cache_dir = _get_models_dir()
    if not os.path.isdir(cache_dir):
        return result
    for d in sorted(os.listdir(cache_dir)):
        dp = os.path.join(cache_dir, d)
        # Модели распознавания перечисляет плагин Faster Whisper: каталог общий,
        # и дважды показывать одно и то же в настройках незачем
        if "faster-whisper" in d:
            continue
        if os.path.isdir(dp) and d.startswith("models--") and ".lock" not in d:
            model_name = d.replace("models--", "").replace("--", "/")
            from pipeline import dir_size
            total = dir_size(dp)
            size_mb = total / 1024 / 1024
            result.append({
                "name": model_name,
                "engine": "WhisperX",
                "file": d,
                "size": f"{size_mb:.0f} MB",
                "path": dp,
                "category": "whisper",
            })
    return result


def transcribe(audio_path: str, out_dir: str, model_name: str, log,
               source_language: str = "", num_speakers: int = 0,
               on_segment=None, **kwargs) -> list[dict]:
    lang_msg = f", язык: {source_language}" if source_language else ", язык: auto"
    log(f"🎙️ Транскрибирую — WhisperX + Диаризация (модель: {model_name}{lang_msg})...")

    _setup_venv(log)

    worker = os.path.join(os.path.dirname(__file__), "whisperx_worker.py")
    python = _get_python()
    cache_dir = _get_models_dir()
    os.makedirs(cache_dir, exist_ok=True)
    _migrate_asr_models()
    asr_dir = _get_asr_dir()
    os.makedirs(asr_dir, exist_ok=True)

    cmd = [
        python, worker,
        "--audio", audio_path,
        "--out_dir", out_dir,
        "--model", model_name,
        "--cache_dir", cache_dir,
        "--asr_cache_dir", asr_dir,
    ]
    if source_language:
        cmd += ["--language", source_language]
    if num_speakers > 0:
        cmd += ["--num_speakers", str(num_speakers)]
    hf_token = os.environ.get("HF_TOKEN", "")
    if hf_token:
        cmd += ["--hf_token", hf_token]

    proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, encoding="utf-8", bufsize=1, cwd=safe_cwd())
    from pipeline import drain_stderr, register_proc
    err_tail = drain_stderr(proc)
    register_proc(proc)   # для принудительной остановки

    subtitles = None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("type") == "log":
                log(msg["message"])
            elif msg.get("type") == "result":
                subtitles = msg["subtitles"]
            elif msg.get("type") == "error":
                raise RuntimeError(f"WhisperX: {msg.get('message', '')}")
        except json.JSONDecodeError:
            pass

    proc.wait()
    if proc.returncode != 0 and subtitles is None:
        stderr = "\n".join(err_tail)
        raise RuntimeError(f"WhisperX worker завершился с ошибкой: {stderr[:500]}")

    if subtitles is None:
        raise RuntimeError("WhisperX не вернул результат")

    # Save speaker map and SRT
    from pipeline import write_speaker_map, write_srt
    speaker_map_path = os.path.join(out_dir, "speaker_map.json")
    write_speaker_map(subtitles, speaker_map_path)
    srt_path = os.path.join(out_dir, "original.srt")
    write_srt(subtitles, srt_path)

    if on_segment:
        for sub in subtitles:
            on_segment(sub)
    return subtitles
