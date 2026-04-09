"""Faster Whisper plugin -- optimized CTranslate2 transcription (isolated venv)."""
import os
import sys
import json
import subprocess as _sp


ENGINES = {"faster-whisper": "Faster Whisper (локально)"}

MODELS = ["turbo", "large-v3-turbo", "large-v3", "large-v2", "large", "medium", "small", "base", "tiny"]

DOWNLOAD_ENGINES = [{"value": "faster-whisper", "label": "Faster Whisper"}]

FASTER_WHISPER_VENV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".venv-faster-whisper")

_DEPS = ["faster-whisper>=1.2.0"]


def _get_models_dir():
    from pipeline import WHISPER_MODELS_DIR
    return os.path.join(WHISPER_MODELS_DIR, "faster-whisper")


def _get_python():
    return os.path.join(FASTER_WHISPER_VENV, "bin", "python")


def _setup_venv(log):
    python = _get_python()
    if os.path.exists(python):
        return
    log("   📦 Создаю окружение Faster Whisper...")
    _sp.run([sys.executable, "-m", "venv", FASTER_WHISPER_VENV], check=True)
    log("   📦 Устанавливаю зависимости...")
    result = _sp.run([os.path.join(FASTER_WHISPER_VENV, "bin", "pip"), "install", "--quiet"] + _DEPS,
                     capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Ошибка установки Faster Whisper: {result.stderr[:500]}")
    log("   ✅ Окружение готово")


def download_model(engine, model, log_msg):
    import json as _json
    python = _get_python()
    if not os.path.exists(python):
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Создаю окружение Faster Whisper...'})}\n\n"
        _sp.run([sys.executable, "-m", "venv", FASTER_WHISPER_VENV], check=True)
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Устанавливаю зависимости...'})}\n\n"
        r = _sp.run([os.path.join(FASTER_WHISPER_VENV, "bin", "pip"), "install"] + _DEPS,
                    capture_output=True, text=True)
        if r.returncode != 0:
            yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ {r.stderr[:500]}'})}\n\n"
            return
        yield f"data: {_json.dumps({'type': 'log', 'message': '✅ Окружение создано'})}\n\n"

    cache_dir = _get_models_dir()
    os.makedirs(cache_dir, exist_ok=True)
    if os.path.isdir(os.path.join(cache_dir, model)):
        yield f"data: {_json.dumps({'type': 'done', 'message': f'✅ Модель {model} уже загружена'})}\n\n"
        return
    yield f"data: {_json.dumps({'type': 'log', 'message': f'⬇️ Загружаю Faster Whisper модель: {model}...'})}\n\n"
    script = f"from faster_whisper import WhisperModel; WhisperModel('{model}', download_root='{cache_dir}', device='cpu'); print('OK')"
    result = _sp.run([python, "-c", script], capture_output=True, text=True, timeout=600, cwd="/")
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

    proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, bufsize=1, cwd="/")

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
        stderr = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"Faster Whisper worker ошибка: {stderr[:500]}")

    if subtitles is None:
        raise RuntimeError("Faster Whisper не вернул результат")

    from pipeline import write_srt
    write_srt(subtitles, os.path.join(out_dir, "original.srt"))
    return subtitles
