"""Qwen3-TTS plugin -- voice cloning (Base) and built-in speakers (CustomVoice). Isolated venv."""
import os
import json
import sys
import subprocess as _sp

_VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"


def _safe_cwd():
    """Не запускаем дочерние процессы из папки проекта: её модули
    затеняют библиотеки. «/» на Windows — корень текущего диска."""
    import tempfile
    return tempfile.gettempdir()


ENGINES = {
    "qwen3-1.7b-base":   "Qwen3-TTS 1.7B Base (клон голоса)",
    "qwen3-0.6b-base":   "Qwen3-TTS 0.6B Base (клон голоса)",
    "qwen3-1.7b-custom": "Qwen3-TTS 1.7B CustomVoice (встроенные)",
    "qwen3-0.6b-custom": "Qwen3-TTS 0.6B CustomVoice (встроенные)",
}

MODELS = {
    "qwen3-1.7b-base":   "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    "qwen3-0.6b-base":   "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "qwen3-1.7b-custom": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "qwen3-0.6b-custom": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
}

DOWNLOAD_ENGINES = [
    {"value": "tts-qwen3-1.7b-base",   "label": "Qwen3 1.7B Base"},
    {"value": "tts-qwen3-0.6b-base",   "label": "Qwen3 0.6B Base"},
    {"value": "tts-qwen3-1.7b-custom", "label": "Qwen3 1.7B CustomVoice"},
    {"value": "tts-qwen3-0.6b-custom", "label": "Qwen3 0.6B CustomVoice"},
]

QWEN3_CUSTOM_VOICES = [
    "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
    "Ryan", "Aiden", "Ono_Anna", "Sohee",
]
QWEN3_BASE_VOICES = QWEN3_CUSTOM_VOICES

QWEN3_VENV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".venv-qwen3")

_DEPS = ["qwen-tts>=0.1.1", "transformers>=4.57.3", "torch>=2.8.0", "torchaudio>=2.8.0", "soundfile>=0.12.0", "numpy"]

_qwen3_proc = None  # Persistent worker


def _get_python():
    return os.path.join(QWEN3_VENV, _VENV_BIN, "python")


def _setup_venv(log):
    from pipeline import venv_ready, mark_venv_ready
    if venv_ready(QWEN3_VENV):
        return
    log("   📦 Создаю окружение Qwen3-TTS...")
    _sp.run([sys.executable, "-m", "venv", QWEN3_VENV], check=True)
    log("   📦 Устанавливаю зависимости...")
    from pipeline import _torch_index_args
    result = _sp.run([os.path.join(QWEN3_VENV, _VENV_BIN, "pip"), "install", "--quiet"] + _torch_index_args() + _DEPS,
                     capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"Ошибка установки Qwen3-TTS: {result.stderr[:500]}")
    mark_venv_ready(QWEN3_VENV)
    log("   ✅ Окружение готово")


def download_model(engine, model, log_msg):
    import json as _json
    python = _get_python()
    from pipeline import venv_ready, mark_venv_ready
    if not venv_ready(QWEN3_VENV):
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Создаю окружение Qwen3-TTS...'})}\n\n"
        _sp.run([sys.executable, "-m", "venv", QWEN3_VENV], check=True)
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Устанавливаю зависимости...'})}\n\n"
        from pipeline import _torch_index_args
        r = _sp.run([os.path.join(QWEN3_VENV, _VENV_BIN, "pip"), "install"] + _torch_index_args() + _DEPS,
                    capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ {r.stderr[:500]}'})}\n\n"
            return
        mark_venv_ready(QWEN3_VENV)
        yield f"data: {_json.dumps({'type': 'log', 'message': '✅ Окружение создано'})}\n\n"

    hf_model = MODELS.get(engine.replace("tts-", ""), "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
    from pipeline import TTS_MODELS_DIR
    tts_hub = os.path.join(TTS_MODELS_DIR, "hub")
    model_dir_name = "models--" + hf_model.replace("/", "--")
    if os.path.isdir(os.path.join(tts_hub, model_dir_name)):
        yield f"data: {_json.dumps({'type': 'done', 'message': f'✅ {hf_model} уже загружена'})}\n\n"
        return
    yield f"data: {_json.dumps({'type': 'log', 'message': f'⬇️ Загружаю {hf_model}...'})}\n\n"
    script = (
        f"import io, sys; sys.stdout = io.StringIO(); "
        f"from qwen_tts import Qwen3TTSModel; "
        f"sys.stdout = sys.__stdout__; "
        f"Qwen3TTSModel.from_pretrained('{hf_model}'); print('OK')"
    )
    result = _sp.run([python, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=600,
                     cwd=_safe_cwd(), env={**os.environ, "HF_HOME": TTS_MODELS_DIR})
    if result.returncode != 0 or "OK" not in result.stdout:
        err = result.stderr[:500] if result.stderr else result.stdout[:500]
        yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ {err}'})}\n\n"
    else:
        yield f"data: {_json.dumps({'type': 'done', 'message': f'✅ {hf_model} загружена'})}\n\n"


def _get_worker(log):
    """Get or start persistent Qwen3 worker process."""
    global _qwen3_proc

    if _qwen3_proc and _qwen3_proc.poll() is None:
        return _qwen3_proc

    _setup_venv(log)

    worker = os.path.join(os.path.dirname(__file__), "qwen3_worker.py")
    python = _get_python()
    from pipeline import TTS_MODELS_DIR

    _qwen3_proc = _sp.Popen(
        [python, worker, "--cache_dir", TTS_MODELS_DIR],
        stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE,
        text=True, encoding="utf-8", bufsize=1, cwd=_safe_cwd()
    )
    # stderr обязательно вычитывать, иначе воркер зависнет на заполненной трубе
    from pipeline import drain_stderr, register_proc
    _qwen3_proc._err_tail = drain_stderr(_qwen3_proc)
    register_proc(_qwen3_proc)   # чтобы кнопка «Стоп» могла его убить

    # Wait for ready
    for line in _qwen3_proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("type") == "log":
                log(msg["message"])
            elif msg.get("type") == "ready":
                break
            elif msg.get("type") == "error":
                raise RuntimeError(f"Qwen3: {msg.get('message', '')}")
        except json.JSONDecodeError:
            pass

    if _qwen3_proc.poll() is not None:
        stderr = "\n".join(getattr(_qwen3_proc, "_err_tail", []))
        _qwen3_proc = None
        raise RuntimeError(f"Qwen3 worker не запустился: {stderr[:500]}")

    return _qwen3_proc


def _send(proc, cmd, log):
    """Send command to worker and read responses until terminal message."""
    if proc.poll() is not None:
        stderr = "\n".join(getattr(proc, "_err_tail", []))
        raise RuntimeError(f"Qwen3 worker завершился: {stderr[:500]}")
    try:
        proc.stdin.write(json.dumps(cmd, ensure_ascii=False) + "\n")
        proc.stdin.flush()
    except BrokenPipeError:
        stderr = "\n".join(getattr(proc, "_err_tail", []))
        raise RuntimeError(f"Qwen3 worker упал: {stderr[:500]}")

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("type") == "log":
                log(msg["message"])
            elif msg.get("type") in ("segment", "loaded", "clone_ready", "done", "error"):
                return msg
        except json.JSONDecodeError:
            pass
    return None


def reset_clone_cache():
    """Reset clone cache (used by multi-speaker)."""
    if _qwen3_proc and _qwen3_proc.poll() is None:
        try:
            _qwen3_proc.stdin.write(json.dumps({"cmd": "reset_clone"}) + "\n")
            _qwen3_proc.stdin.flush()
            # Read response
            for line in _qwen3_proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if msg.get("type") in ("done", "error"):
                        break
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass


def synthesize(subtitles: list[dict], out_dir: str, log,
               engine: str = "qwen3-1.7b-base",
               voice: str = "", voice_wav: str = "", voice_text: str = "",
               seed: int = 44, temperature: float = 0.7,
               on_segment=None, **kwargs) -> list[dict]:
    model = MODELS.get(engine, "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
    model_short = model.split('/')[-1]
    is_custom = "CustomVoice" in model
    voice_info = f", голос: {voice}" if voice else ""
    log(f"🔊 Синтезирую речь ({model_short}{voice_info})...")

    proc = _get_worker(log)
    use_seed = seed >= 0

    # Load model
    resp = _send(proc, {
        "cmd": "load", "model": model,
        "seed": seed, "temperature": temperature,
        "speaker": voice or "Vivian",
    }, log)
    if resp and resp.get("type") == "error":
        raise RuntimeError(resp.get("message", ""))

    # Prepare voice clone for Base model
    has_ref = voice_wav and os.path.exists(voice_wav)
    if is_custom and has_ref:
        log("⚠️ CustomVoice не поддерживает клонирование голоса. Используйте Base модель.")

    if not is_custom and has_ref:
        resp = _send(proc, {
            "cmd": "clone", "voice_wav": voice_wav, "voice_text": voice_text,
            "seed": seed, "temperature": temperature,
        }, log)
        if resp and resp.get("type") == "error":
            raise RuntimeError(resp.get("message", ""))
    elif not is_custom and not has_ref:
        raise RuntimeError(
            "Base модель требует клонированный голос. "
            "Выберите голос в настройках TTS или используйте CustomVoice модель."
        )

    audio_dir = os.path.join(out_dir, "tts_audio")
    os.makedirs(audio_dir, exist_ok=True)

    total = len(subtitles)
    skipped = 0
    result_subs = []

    if use_seed:
        log(f"   🎲 Seed интонации: {seed}")
    else:
        log("   🎲 Seed интонации: выкл (случайная)")

    for sub in subtitles:
        audio_path = os.path.join(audio_dir, f"seg_{sub['index']:04d}.wav")

        resp = _send(proc, {
            "cmd": "generate",
            "text": sub["text"],
            "index": sub["index"],
            "out_path": audio_path,
            "seed": seed,
            "temperature": temperature,
            "speaker": voice or "Vivian",
        }, log)

        if resp and resp.get("type") == "segment":
            result_subs.append({**sub, "audio_path": audio_path})
            if on_segment:
                on_segment(sub["index"])
        elif resp and resp.get("type") == "error":
            skipped += 1

        if sub["index"] % 5 == 0 or sub["index"] == total:
            log(f"   🔊 TTS: {sub['index']}/{total}")

    if skipped:
        log(f"   ⚠️ Пропущено с ошибками: {skipped}")
    log("✅ Синтез речи завершён")
    return result_subs
