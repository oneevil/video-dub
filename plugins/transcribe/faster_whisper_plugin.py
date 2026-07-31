"""Faster Whisper plugin -- optimized CTranslate2 transcription (isolated venv)."""
import glob
import os
import shutil
import sys
import json
import subprocess as _sp

from pipeline import safe_cwd

_VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"


ENGINES = {"faster-whisper": "Faster Whisper (локально)"}

MODELS = ["turbo", "large-v3-turbo", "large-v3", "large-v2", "large", "medium", "small", "base", "tiny"]

DOWNLOAD_ENGINES = [{"value": "faster-whisper", "label": "Faster Whisper"}]

FASTER_WHISPER_VENV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".venv-faster-whisper")

_DEPS = ["faster-whisper>=1.2.0"]

# CTranslate2 не тянет CUDA за собой и на видеокарте падает с
# «libcublas.so.12 is not found». В отличие от whisperx, который получает эти
# библиотеки бесплатно вместе с torch, здесь их приходится ставить руками.
_CUDA_DEPS = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12>=9,<10"]


def _get_models_dir():
    from pipeline import WHISPER_ASR_DIR
    return WHISPER_ASR_DIR


# «turbo» faster-whisper тянет из репозитория с другим именем, остальные модели
# называются одинаково
_REPO_SUFFIX = {"turbo": "large-v3-turbo-ct2"}


def find_model_dir(model: str):
    """Каталог HF-кеша с этой моделью, если она уже скачана.

    Кеш лежит не по имени модели, а по имени репозитория
    (models--Systran--faster-whisper-large-v3), поэтому простой проверки
    существования каталога недостаточно.
    """
    cache_dir = _get_models_dir()
    if not os.path.isdir(cache_dir):
        return None
    want = _REPO_SUFFIX.get(model, model)
    for d in os.listdir(cache_dir):
        if not d.startswith("models--") or "faster-whisper-" not in d:
            continue
        # Точное совпадение хвоста: иначе large-v3 нашёлся бы в large-v3-turbo
        if d.split("faster-whisper-", 1)[1] == want:
            return os.path.join(cache_dir, d)
    return None


def pretty_model_name(dirname: str) -> str:
    """models--Systran--faster-whisper-large-v3 → Systran/faster-whisper-large-v3"""
    return dirname.replace("models--", "").replace("--", "/")


def _get_python():
    return os.path.join(FASTER_WHISPER_VENV, _VENV_BIN, "python")


def _has_nvidia_gpu() -> bool:
    """Есть ли видеокарта. Библиотеки CUDA весят около гигабайта, так что на
    машине без неё их качать незачем."""
    return sys.platform != "darwin" and shutil.which("nvidia-smi") is not None


def _nvidia_lib_dirs() -> list[str]:
    """Каталоги с libcublas/libcudnn внутри venv — пакеты nvidia-*-cu12."""
    if sys.platform == "win32":
        site_packages = [os.path.join(FASTER_WHISPER_VENV, "Lib", "site-packages")]
        sub = "bin"
    else:
        site_packages = glob.glob(os.path.join(FASTER_WHISPER_VENV, "lib", "python3.*", "site-packages"))
        sub = "lib"
    dirs = []
    for sp in site_packages:
        dirs += sorted(glob.glob(os.path.join(sp, "nvidia", "*", sub)))
    return dirs


def _worker_env():
    """Окружение воркера с путём к CUDA-библиотекам.

    Задать его нужно именно дочернему процессу: динамический загрузчик читает
    LD_LIBRARY_PATH один раз при старте, менять его изнутри уже поздно.
    """
    dirs = _nvidia_lib_dirs()
    if not dirs:
        return None
    env = os.environ.copy()
    key = "PATH" if sys.platform == "win32" else "LD_LIBRARY_PATH"
    env[key] = os.pathsep.join(dirs + ([env[key]] if env.get(key) else []))
    return env


def _ensure_cuda_libs(log):
    """Доставляет CUDA-библиотеки в уже созданное окружение.

    Проверяем отдельно от venv_ready: у тех, кто ставил Faster Whisper раньше,
    окружение помечено готовым, но библиотек в нём нет.
    """
    if not _has_nvidia_gpu() or _nvidia_lib_dirs():
        return
    log("   📦 Доустанавливаю библиотеки CUDA (около 1 ГБ, один раз)...")
    r = _sp.run([os.path.join(FASTER_WHISPER_VENV, _VENV_BIN, "pip"), "install", "--quiet"] + _CUDA_DEPS,
                capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        log(f"   ⚠️ Не удалось поставить CUDA-библиотеки, останусь на процессоре: {r.stderr[:200]}")
    else:
        log("   ✅ Библиотеки CUDA готовы")


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
    if find_model_dir(model):
        yield f"data: {_json.dumps({'type': 'done', 'message': f'✅ Модель {model} уже загружена'})}\n\n"
        return
    yield f"data: {_json.dumps({'type': 'log', 'message': f'⬇️ Загружаю Faster Whisper модель: {model}...'})}\n\n"
    _cd = cache_dir.replace('\\', '/')
    script = f"from faster_whisper import WhisperModel; WhisperModel('{model}', download_root='{_cd}', device='cpu'); print('OK')"
    result = _sp.run([python, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=600, cwd=safe_cwd())
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
        if os.path.isdir(dp) and d.startswith("models--") and ".lock" not in d:
            from pipeline import dir_size
            total = dir_size(dp)
            size_mb = total / 1024 / 1024
            result.append({
                "name": pretty_model_name(d),
                # Каталог общий: эти же файлы использует и WhisperX
                "engine": "Faster Whisper / WhisperX",
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
    _ensure_cuda_libs(log)

    worker = os.path.join(os.path.dirname(__file__), "faster_whisper_worker.py")
    cache_dir = _get_models_dir()
    os.makedirs(cache_dir, exist_ok=True)

    cmd = [_get_python(), worker,
           "--audio", audio_path,
           "--model", model_name,
           "--cache_dir", cache_dir]
    if source_language:
        cmd += ["--language", source_language]
    # Имя видеокарты спрашиваем здесь: в изолированном окружении воркера нет ни
    # pipeline, ни torch, а машина всё равно та же самая
    from pipeline import gpu_name
    name = gpu_name()
    if name:
        cmd += ["--gpu_name", name]

    proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, encoding="utf-8", bufsize=1,
                     cwd=safe_cwd(), env=_worker_env())
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
