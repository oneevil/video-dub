"""OmniVoice plugin -- 600+ languages, voice cloning."""
import os
import sys
import json


ENGINES = {"omnivoice": "OmniVoice (600+ языков, клон голоса)"}

DOWNLOAD_ENGINES = [{"value": "tts-omnivoice", "label": "OmniVoice"}]


def download_model(engine, model, log_msg):
    """Download OmniVoice venv + model. Yields SSE messages."""
    import json as _json
    import subprocess as _sp
    python_path = os.path.join(OMNIVOICE_VENV, "bin", "python")
    pip = os.path.join(OMNIVOICE_VENV, "bin", "pip")
    # 1. Create venv if needed
    if not os.path.exists(python_path):
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Создаю окружение OmniVoice...'})}\n\n"
        _sp.run([sys.executable, "-m", "venv", OMNIVOICE_VENV], check=True)
        yield f"data: {_json.dumps({'type': 'log', 'message': '📦 Устанавливаю зависимости...'})}\n\n"
        result = _sp.run([pip, "install", "omnivoice>=0.1.3", "torch>=2.8.0", "torchaudio>=2.8.0", "torchcodec", "soundfile>=0.12.0"],
                         capture_output=True, text=True)
        if result.returncode != 0:
            yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ Ошибка установки: {result.stderr[:500]}'})}\n\n"
            return
        yield f"data: {_json.dumps({'type': 'log', 'message': '✅ Окружение создано'})}\n\n"
    # 2. Check model
    from pipeline import TTS_MODELS_DIR
    tts_hub = os.path.join(TTS_MODELS_DIR, "hub")
    if os.path.isdir(tts_hub) and any(d.startswith("models--") and "OmniVoice" in d for d in os.listdir(tts_hub)):
        yield f"data: {_json.dumps({'type': 'done', 'message': '✅ OmniVoice модель уже загружена'})}\n\n"
        return
    # 3. Download model
    yield f"data: {_json.dumps({'type': 'log', 'message': '⬇️ Загружаю модель OmniVoice...'})}\n\n"
    dl_script = f"import os; os.environ['HF_HOME']='{TTS_MODELS_DIR}'; from omnivoice import OmniVoice; OmniVoice.from_pretrained('k2-fsa/OmniVoice', device_map='cpu'); print('OK')"
    result = _sp.run([python_path, "-c", dl_script], capture_output=True, text=True, timeout=600, cwd="/")
    if result.returncode != 0 or "OK" not in result.stdout:
        err = result.stderr[:500] if result.stderr else result.stdout[:500]
        yield f"data: {_json.dumps({'type': 'error', 'message': f'❌ {err}'})}\n\n"
    else:
        yield f"data: {_json.dumps({'type': 'done', 'message': '✅ OmniVoice модель загружена'})}\n\n"


OMNIVOICE_VENV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".venv-omnivoice")

_omnivoice_proc = None  # Persistent worker process


def _setup_omnivoice_venv(log):
    """Create isolated venv for OmniVoice with transformers>=5.3."""
    if os.path.exists(os.path.join(OMNIVOICE_VENV, "bin", "python")):
        return  # Already set up
    log("   📦 Создаю изолированное окружение для OmniVoice...")
    import subprocess as _sp
    _sp.run([sys.executable, "-m", "venv", OMNIVOICE_VENV], check=True)
    pip = os.path.join(OMNIVOICE_VENV, "bin", "pip")
    log("   📦 Устанавливаю OmniVoice + зависимости...")
    _sp.run([pip, "install", "--quiet", "omnivoice>=0.1.3", "torch>=2.8.0", "torchaudio>=2.8.0", "torchcodec", "soundfile>=0.12.0"],
            check=True, capture_output=True)
    log("   ✅ OmniVoice окружение готово")


def _get_omnivoice_worker(log):
    """Get or start persistent OmniVoice worker process."""
    global _omnivoice_proc
    import subprocess as _sp

    if _omnivoice_proc and _omnivoice_proc.poll() is None:
        return _omnivoice_proc  # Still running

    _setup_omnivoice_venv(log)
    worker = os.path.join(os.path.dirname(__file__), "omnivoice_worker.py")
    python = os.path.join(OMNIVOICE_VENV, "bin", "python")
    from pipeline import TTS_MODELS_DIR
    cache_dir = TTS_MODELS_DIR
    os.makedirs(cache_dir, exist_ok=True)

    _omnivoice_proc = _sp.Popen(
        [python, worker, "--cache_dir", cache_dir],
        stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE,
        text=True, bufsize=1
    )

    # Wait for "ready" signal
    for line in _omnivoice_proc.stdout:
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
                raise RuntimeError(f"OmniVoice: {msg.get('message', '')}")
        except json.JSONDecodeError:
            pass

    # Check if process died during startup
    if _omnivoice_proc.poll() is not None:
        stderr = _omnivoice_proc.stderr.read() if _omnivoice_proc.stderr else ""
        _omnivoice_proc = None
        raise RuntimeError(f"OmniVoice worker не запустился: {stderr[:500]}")

    return _omnivoice_proc


def _omnivoice_send(proc, cmd, log, on_segment=None, subtitles=None, audio_dir=""):
    """Send command to worker and read response(s)."""
    # Check if worker is still alive
    if proc.poll() is not None:
        stderr = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"OmniVoice worker завершился (код {proc.returncode}): {stderr[:500]}")
    try:
        proc.stdin.write(json.dumps(cmd, ensure_ascii=False) + "\n")
        proc.stdin.flush()
    except BrokenPipeError:
        stderr = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"OmniVoice worker упал: {stderr[:500]}")

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("type") == "log":
                log(msg["message"])
            elif msg.get("type") in ("segment", "warmed_up"):
                return msg
            elif msg.get("type") == "error":
                log(f"   ❌ {msg.get('message', '')}")
                return msg
            elif msg.get("type") == "done":
                return msg
        except json.JSONDecodeError:
            pass
    return None


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
