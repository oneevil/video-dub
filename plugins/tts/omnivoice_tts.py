"""OmniVoice plugin -- 600+ languages, voice cloning."""
import os
import sys
import json

_VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"


ENGINES = {"omnivoice": "OmniVoice (600+ языков, клон голоса)"}

DOWNLOAD_ENGINES = [{"value": "tts-omnivoice", "label": "OmniVoice"}]


def download_model(engine, model, log_msg):
    """Download OmniVoice venv + model. Yields SSE messages."""
    import json as _json
    import subprocess as _sp
    from pipeline import venv_ready, mark_venv_ready
    python_path = os.path.join(OMNIVOICE_VENV, _VENV_BIN, "python")
    pip = os.path.join(OMNIVOICE_VENV, _VENV_BIN, "pip")
    # 1. Create venv if needed
    if not venv_ready(OMNIVOICE_VENV):
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Создаю окружение OmniVoice...'})}\n\n"
        _sp.run([sys.executable, "-m", "venv", OMNIVOICE_VENV], check=True)
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Устанавливаю зависимости...'})}\n\n"
        from pipeline import _torch_index_args
        result = _sp.run([pip, "install"] + _torch_index_args() + ["omnivoice>=0.1.3", "torch>=2.8.0", "torchaudio>=2.8.0", "torchcodec", "soundfile>=0.12.0"],
                         capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ Ошибка установки: {result.stderr[:500]}'})}\n\n"
            return
        mark_venv_ready(OMNIVOICE_VENV)
        yield f"data: {_json.dumps({'type': 'log', 'message': '✅ Окружение создано'})}\n\n"
    # 2. Check model
    from pipeline import TTS_MODELS_DIR
    tts_hub = os.path.join(TTS_MODELS_DIR, "hub")
    if os.path.isdir(tts_hub) and any(d.startswith("models--") and "OmniVoice" in d for d in os.listdir(tts_hub)):
        yield f"data: {_json.dumps({'type': 'done', 'message': '✅ OmniVoice модель уже загружена'})}\n\n"
        return
    # 3. Download model
    yield f"data: {_json.dumps({'type': 'log', 'message': '⬇️ Загружаю модель OmniVoice...'})}\n\n"
    dl_script = "from omnivoice import OmniVoice; OmniVoice.from_pretrained('k2-fsa/OmniVoice', device_map='cpu'); print('OK')"
    result = _sp.run([python_path, "-c", dl_script], capture_output=True, text=True, encoding="utf-8", timeout=600,
                     cwd=os.path.dirname(OMNIVOICE_VENV), env={**os.environ, "HF_HOME": TTS_MODELS_DIR})
    if result.returncode != 0 or "OK" not in result.stdout:
        err = result.stderr[:500] if result.stderr else result.stdout[:500]
        yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ {err}'})}\n\n"
    else:
        yield f"data: {_json.dumps({'type': 'done', 'message': '✅ OmniVoice модель загружена'})}\n\n"


OMNIVOICE_VENV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".venv-omnivoice")

_omnivoice_proc = None  # Persistent worker process


def _setup_omnivoice_venv(log):
    """Create isolated venv for OmniVoice with transformers>=5.3."""
    from pipeline import venv_ready, mark_venv_ready
    if venv_ready(OMNIVOICE_VENV):
        return  # Already set up
    log("   📦 Создаю изолированное окружение для OmniVoice...")
    import subprocess as _sp
    _sp.run([sys.executable, "-m", "venv", OMNIVOICE_VENV], check=True)
    pip = os.path.join(OMNIVOICE_VENV, _VENV_BIN, "pip")
    log("   📦 Устанавливаю OmniVoice + зависимости...")
    from pipeline import _torch_index_args
    _sp.run([pip, "install", "--quiet"] + _torch_index_args() + ["omnivoice>=0.1.3", "torch>=2.8.0", "torchaudio>=2.8.0", "torchcodec", "soundfile>=0.12.0"],
            check=True, capture_output=True)
    mark_venv_ready(OMNIVOICE_VENV)
    log("   ✅ OmniVoice окружение готово")


def _err_text(proc) -> str:
    return "\n".join(getattr(proc, "_err_tail", []))


def _start_readers(proc):
    """Читаем stdout в очередь, stderr — в фоновый буфер.

    Оба потока обязательны: труба всего 16 КБ, и как только tqdm/варнинги её
    заполнят, воркер навсегда блокируется на записи (выглядит как зависание).
    """
    import queue as _q
    import threading as _th
    from pipeline import drain_stderr

    proc._err_tail = drain_stderr(proc)
    out_q: "_q.Queue" = _q.Queue()

    def _read_stdout():
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    out_q.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
        out_q.put(None)  # EOF — процесс завершился

    _th.Thread(target=_read_stdout, daemon=True).start()
    proc._out_q = out_q


def _read_msg(proc, log, what: str, heartbeat: int = 20):
    """Ждёт следующее сообщение воркера, напоминая в лог, что работа идёт."""
    import queue as _q
    import time as _t
    started = _t.monotonic()
    while True:
        try:
            msg = proc._out_q.get(timeout=heartbeat)
        except _q.Empty:
            log(f"   ⏳ {what}... ({int(_t.monotonic() - started)}с)")
            continue
        if msg is None:
            raise RuntimeError(
                f"OmniVoice worker завершился (код {proc.poll()}): {_err_text(proc)[-500:]}")
        if msg.get("type") == "log":
            log(msg["message"])
            started = _t.monotonic()
            continue
        return msg


def _get_omnivoice_worker(log):
    """Get or start persistent OmniVoice worker process."""
    global _omnivoice_proc
    import subprocess as _sp

    if _omnivoice_proc and _omnivoice_proc.poll() is None:
        return _omnivoice_proc  # Still running

    _setup_omnivoice_venv(log)
    # осиротевшие воркеры завершаются сами: они видят EOF на stdin (см.
    # _stdin_lines в omnivoice_worker.py), это работает на всех ОС
    worker = os.path.join(os.path.dirname(__file__), "omnivoice_worker.py")
    python = os.path.join(OMNIVOICE_VENV, _VENV_BIN, "python")
    from pipeline import TTS_MODELS_DIR
    cache_dir = TTS_MODELS_DIR
    os.makedirs(cache_dir, exist_ok=True)

    _omnivoice_proc = _sp.Popen(
        [python, worker, "--cache_dir", cache_dir],
        stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE,
        text=True, encoding="utf-8", bufsize=1,
        env={**os.environ, "HF_HOME": cache_dir},
    )
    _start_readers(_omnivoice_proc)

    # Ждём "ready" (первая загрузка модели на MPS занимает несколько минут)
    while True:
        msg = _read_msg(_omnivoice_proc, log, "OmniVoice загружается")
        if msg.get("type") == "ready":
            break
        if msg.get("type") == "error":
            _omnivoice_proc = None
            raise RuntimeError(f"OmniVoice: {msg.get('message', '')}")

    return _omnivoice_proc


def _omnivoice_send(proc, cmd, log, on_segment=None, subtitles=None, audio_dir=""):
    """Send command to worker and read response(s)."""
    # Check if worker is still alive
    if proc.poll() is not None:
        raise RuntimeError(f"OmniVoice worker завершился (код {proc.returncode}): {_err_text(proc)[-500:]}")
    try:
        proc.stdin.write(json.dumps(cmd, ensure_ascii=False) + "\n")
        proc.stdin.flush()
    except BrokenPipeError:
        raise RuntimeError(f"OmniVoice worker упал: {_err_text(proc)[-500:]}")

    what = "Прогрев модели" if cmd.get("cmd") == "warmup" else "Синтез речи"
    msg = _read_msg(proc, log, what)
    if msg.get("type") == "error":
        log(f"   ❌ {msg.get('message', '')}")
    return msg


def synthesize(subtitles: list[dict], out_dir: str, log,
               engine: str = "omnivoice",
               voice: str = "", voice_wav: str = "", voice_text: str = "",
               seed: int = -1, on_segment=None, **kwargs) -> list[dict]:
    """Synthesize speech via persistent OmniVoice worker."""
    log("🔊 Синтезирую речь (OmniVoice, 600+ языков)...")

    proc = _get_omnivoice_worker(log)

    # Warmup with selected voice at load time
    warmup_cmd = {"cmd": "warmup"}
    if voice_wav and os.path.exists(voice_wav):
        warmup_cmd["ref_audio"] = voice_wav
        warmup_cmd["ref_text"] = voice_text or ""
    elif voice:
        warmup_cmd["instruct"] = voice
    _omnivoice_send(proc, warmup_cmd, log)

    audio_dir = os.path.join(out_dir, "tts_audio")
    os.makedirs(audio_dir, exist_ok=True)

    total = len(subtitles)
    result_subs = []
    skipped = 0

    for i, sub in enumerate(subtitles):
        out_path = os.path.join(audio_dir, f"seg_{sub['index']:04d}.wav")

        cmd = {
            "cmd": "generate",
            "text": sub["text"],
            "index": sub["index"],
            "out_path": out_path,
            "seed": seed,
        }
        if voice_wav and os.path.exists(voice_wav):
            cmd["ref_audio"] = voice_wav
            cmd["ref_text"] = voice_text or ""
        elif voice:
            cmd["instruct"] = voice

        resp = _omnivoice_send(proc, cmd, log)
        if resp and resp.get("type") == "segment":
            result_subs.append({**sub, "audio_path": out_path})
            if on_segment:
                on_segment(sub["index"])
        elif resp and resp.get("type") == "error":
            skipped += 1

        if (i + 1) % 5 == 0 or i == total - 1:
            log(f"   🔊 TTS: {i+1}/{total}")

    if skipped:
        log(f"   ⚠️ Пропущено с ошибками: {skipped}")
    log("✅ OmniVoice синтез завершён")
    return result_subs
