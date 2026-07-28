"""Faster Whisper plugin -- optimized CTranslate2 transcription (isolated venv)."""
import os
import sys
import json
import subprocess as _sp

_VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"


def _safe_cwd():
    """Не запускаем дочерние процессы из папки проекта: её модули
    затеняют библиотеки. «/» на Windows — корень текущего диска."""
    import tempfile
    return tempfile.gettempdir()


ENGINES = {"faster-whisper": "Faster Whisper (локально)"}

MODELS = ["turbo", "large-v3-turbo", "large-v3", "large-v2", "large", "medium", "small", "base", "tiny"]

DOWNLOAD_ENGINES = [{"value": "faster-whisper", "label": "Faster Whisper"}]

FASTER_WHISPER_VENV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".venv-faster-whisper")

_DEPS = ["faster-whisper>=1.2.0"]


def _get_models_dir():
    from pipeline import WHISPER_MODELS_DIR
    return os.path.join(WHISPER_MODELS_DIR, "faster-whisper")


def _get_python():
    return os.path.join(FASTER_WHISPER_VENV, _VENV_BIN, "python")


def _setup_venv(log):
    from pipeline import venv_ready, mark_venv_ready
    if venv_ready(FASTER_WHISPER_VENV):
        return
    log("   📦 Создаю окружение Faster Whisper...")
    _sp.run([sys.executable, "-m", "venv", FASTER_WHISPER_VENV], check=True)
    log("   📦 Устанавливаю зависимости...")
    result = _sp.run([os.path.join(FASTER_WHISPER_VENV, _VENV_BIN, "pip"), "install", "--quiet"] + _DEPS,
                     capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"Ошибка установки Faster Whisper: {result.stderr[:500]}")
    mark_venv_ready(FASTER_WHISPER_VENV)
    log("   ✅ Окружение готово")


def download_model(engine, model, log_msg):
    import json as _json
    from pipeline import venv_ready, mark_venv_ready
    python = _get_python()
    if not venv_ready(FASTER_WHISPER_VENV):
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Создаю окружение Faster Whisper...'})}\n\n"
        _sp.run([sys.executable, "-m", "venv", FASTER_WHISPER_VENV], check=True)
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Устанавливаю зависимости...'})}\n\n"
        r = _sp.run([os.path.join(FASTER_WHISPER_VENV, _VENV_BIN, "pip"), "install"] + _DEPS,
                    capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ {r.stderr[:500]}'})}\n\n"
            return
        mark_venv_ready(FASTER_WHISPER_VENV)
        yield f"data: {_json.dumps({'type': 'log', 'message': '✅ Окружение создано'})}\n\n"

    cache_dir = _get_models_dir()
    os.makedirs(cache_dir, exist_ok=True)
    if os.path.isdir(os.path.join(cache_dir, model)):
        yield f"data: {_json.dumps({'type': 'done', 'message': f'✅ Модель {model} уже загружена'})}\n\n"
        return
    yield f"data: {_json.dumps({'type': 'log', 'message': f'⬇️ Загружаю Faster Whisper модель: {model}...'})}\n\n"
    _cd = cache_dir.replace('\\', '/')
    script = f"from faster_whisper import WhisperModel; WhisperModel('{model}', download_root='{_cd}', device='cpu'); print('OK')"
    result = _sp.run([python, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=600, cwd=_safe_cwd())
    if result.returncode != 0 or "OK" not in result.stdout:
        err = result.stderr[:500] if result.stderr else result.stdout[:500]
        yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ {err}'})}\n\n"
    else:
        yield f"data: {_json.dumps({'type': 'done', 'message': f'✅ Модель {model} загружена'})}\n\n"


def list_downloaded_models():
    result = []
    cache_dir = _get_models_dir()
    if not os.path.isdir(cache_dir):
        return result
    for d in sorted(os.listdir(cache_dir)):
        dp = os.path.join(cache_dir, d)
        if os.path.isdir(dp) and ".lock" not in d:
            total = sum(
                os.path.getsize(os.path.join(root, f))
                for root, _, files in os.walk(dp) for f in files
            )
            size_mb = total / 1024 / 1024
            result.append({
                "name": d,
                "engine": "Faster Whisper",
                "file": d,
                "size": f"{size_mb:.0f} MB",
                "path": dp,
                "category": "whisper",
            })
    return result


def transcribe(audio_path: str, out_dir: str, model_name: str, log,
               source_language: str = "", on_segment=None, **kwargs) -> list[dict]:
    lang_msg = f", язык: {source_language}" if source_language else ", язык: auto"
    log(f"🎙️ Транскрибирую — Faster Whisper (модель: {model_name}{lang_msg})...")

    _setup_venv(log)

    worker = os.path.join(os.path.dirname(__file__), "faster_whisper_worker.py")
    cache_dir = _get_models_dir()
    os.makedirs(cache_dir, exist_ok=True)

    cmd = [_get_python(), worker,
           "--audio", audio_path,
           "--model", model_name,
           "--cache_dir", cache_dir]
    if source_language:
        cmd += ["--language", source_language]

    proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, encoding="utf-8", bufsize=1, cwd=_safe_cwd())
    from pipeline import drain_stderr
    err_tail = drain_stderr(proc)

    subtitles = None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("type") == "log":
                log(msg["message"])
            elif msg.get("type") == "segment" and on_segment:
                on_segment(msg["sub"])
            elif msg.get("type") == "result":
                subtitles = msg["subtitles"]
            elif msg.get("type") == "error":
                raise RuntimeError(f"Faster Whisper: {msg.get('message', '')}")
        except json.JSONDecodeError:
            pass

    proc.wait()
    if proc.returncode != 0 and subtitles is None:
        stderr = "\n".join(err_tail)
        raise RuntimeError(f"Faster Whisper worker ошибка: {stderr[:500]}")

    if subtitles is None:
        raise RuntimeError("Faster Whisper не вернул результат")

    from pipeline import write_srt
    write_srt(subtitles, os.path.join(out_dir, "original.srt"))
    return subtitles
