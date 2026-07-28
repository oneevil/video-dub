"""OpenAI Whisper plugin -- local transcription (isolated venv)."""
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


ENGINES = {"openai-whisper": "OpenAI Whisper (локально)"}

MODELS = ["turbo", "large-v3-turbo", "large-v3", "large-v2", "large", "medium", "small", "base", "tiny"]

DOWNLOAD_ENGINES = [{"value": "openai-whisper", "label": "OpenAI Whisper"}]

WHISPER_VENV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".venv-whisper")

_DEPS = ["openai-whisper>=20250625", "torch>=2.8.0", "torchaudio>=2.8.0"]


def _get_models_dir():
    from pipeline import WHISPER_MODELS_DIR
    return os.path.join(WHISPER_MODELS_DIR, "openai")


def _get_python():
    return os.path.join(WHISPER_VENV, _VENV_BIN, "python")


def _setup_venv(log):
    from pipeline import venv_ready, mark_venv_ready
    if venv_ready(WHISPER_VENV):
        return
    log("   📦 Создаю окружение OpenAI Whisper...")
    _sp.run([sys.executable, "-m", "venv", WHISPER_VENV], check=True)
    log("   📦 Устанавливаю зависимости...")
    from pipeline import _torch_index_args
    result = _sp.run([os.path.join(WHISPER_VENV, _VENV_BIN, "pip"), "install", "--quiet"] + _torch_index_args() + _DEPS,
                     capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"Ошибка установки OpenAI Whisper: {result.stderr[:500]}")
    mark_venv_ready(WHISPER_VENV)
    log("   ✅ Окружение готово")


def download_model(engine, model, log_msg):
    """Download OpenAI Whisper model. Yields SSE messages."""
    import json as _json
    python = _get_python()
    # Ensure venv
    from pipeline import venv_ready, mark_venv_ready
    if not venv_ready(WHISPER_VENV):
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Создаю окружение OpenAI Whisper...'})}\n\n"
        _sp.run([sys.executable, "-m", "venv", WHISPER_VENV], check=True)
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Устанавливаю зависимости...'})}\n\n"
        from pipeline import _torch_index_args
        r = _sp.run([os.path.join(WHISPER_VENV, _VENV_BIN, "pip"), "install"] + _torch_index_args() + _DEPS,
                    capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ {r.stderr[:500]}'})}\n\n"
            return
        mark_venv_ready(WHISPER_VENV)
        yield f"data: {_json.dumps({'type': 'log', 'message': '✅ Окружение создано'})}\n\n"

    cache_dir = _get_models_dir()
    os.makedirs(cache_dir, exist_ok=True)
    if os.path.exists(os.path.join(cache_dir, f"{model}.pt")):
        yield f"data: {_json.dumps({'type': 'done', 'message': f'✅ Модель {model} уже загружена'})}\n\n"
        return
    yield f"data: {_json.dumps({'type': 'log', 'message': f'⬇️ Загружаю Whisper модель: {model}...'})}\n\n"
    _cd = cache_dir.replace('\\', '/')
    script = f"import whisper; whisper.load_model('{model}', download_root='{_cd}'); print('OK')"
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
    for f in sorted(os.listdir(cache_dir)):
        fp = os.path.join(cache_dir, f)
        if f.endswith(".pt") and ".lock" not in f:
            size_mb = os.path.getsize(fp) / 1024 / 1024
            result.append({
                "name": f.replace(".pt", ""),
                "engine": "OpenAI Whisper",
                "file": f,
                "size": f"{size_mb:.0f} MB",
                "path": fp,
                "category": "whisper",
            })
    return result


def transcribe(audio_path: str, out_dir: str, model_name: str, log,
               source_language: str = "", on_segment=None, **kwargs) -> list[dict]:
    lang_msg = f", язык: {source_language}" if source_language else ", язык: auto"
    log(f"🎙️ Транскрибирую — OpenAI Whisper (модель: {model_name}{lang_msg})...")

    _setup_venv(log)

    worker = os.path.join(os.path.dirname(__file__), "openai_whisper_worker.py")
    cache_dir = _get_models_dir()
    os.makedirs(cache_dir, exist_ok=True)

    cmd = [_get_python(), worker,
           "--audio", audio_path,
           "--model", model_name,
           "--cache_dir", cache_dir]
    if source_language:
        cmd += ["--language", source_language]

    proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, encoding="utf-8", bufsize=1, cwd=_safe_cwd())
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
                raise RuntimeError(f"Whisper: {msg.get('message', '')}")
        except json.JSONDecodeError:
            pass

    proc.wait()
    if proc.returncode != 0 and subtitles is None:
        stderr = "\n".join(err_tail)
        raise RuntimeError(f"Whisper worker ошибка: {stderr[:500]}")

    if subtitles is None:
        raise RuntimeError("Whisper не вернул результат")

    if on_segment:
        for sub in subtitles:
            on_segment(sub)

    from pipeline import write_srt
    write_srt(subtitles, os.path.join(out_dir, "original.srt"))
    return subtitles
