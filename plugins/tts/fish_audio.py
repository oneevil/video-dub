"""Fish Audio TTS plugin -- cloud TTS with persistent voice cloning via SDK."""
import os


ENGINES = {"fish-audio": "Fish Audio (облако)"}

API_KEY_ENV = "FISH_API_KEY"

# Cache cloned model_id by WAV path
_clone_cache = {}  # voice_wav_path -> model_id


def reset_clone_cache():
    """Reset clone cache (used by multi-speaker)."""
    _clone_cache.clear()


def _get_or_create_clone(voice_wav, voice_text, session, log):
    """Get cached or create persistent voice model on Fish Audio."""
    import json as _json
    import hashlib

    # Memory cache
    if voice_wav in _clone_cache:
        log(f"   🎙️ Клонированный голос (кэш): {_clone_cache[voice_wav]}")
        return _clone_cache[voice_wav]

    # Persistent cache (JSON file next to WAV)
    wav_hash = hashlib.md5(voice_wav.encode()).hexdigest()[:8]
    cache_file = os.path.join(os.path.dirname(voice_wav), f".fish_clone_{wav_hash}.json")

    if os.path.exists(cache_file):
        try:
            with open(cache_file, encoding="utf-8") as cf:
                cached = _json.loads(cf.read())
            model_id = cached.get("model_id")
            # Verify model still exists
            try:
                session.get_model(model_id)
                _clone_cache[voice_wav] = model_id
                log(f"   🎙️ Клонированный голос (сохранён): {model_id}")
                return model_id
            except Exception:
                pass
        except Exception:
            pass

    # Create new voice model
    voice_name = os.path.basename(os.path.dirname(voice_wav))
    log(f"   🎙️ Создаю голосовую модель на Fish Audio: {voice_name}...")

    with open(voice_wav, "rb") as f:
        model = session.create_model(
            title=voice_name,
            type="tts",
            train_mode="fast",
            visibility="private",
            voices=[f],
        )

    model_id = model._id if hasattr(model, '_id') else str(model)
    _clone_cache[voice_wav] = model_id

    # Save persistent cache
    with open(cache_file, "w", encoding="utf-8") as cf:
        cf.write(_json.dumps({"model_id": model_id, "wav": voice_wav}))
    log(f"   ✅ Голосовая модель создана: {model_id}")
    return model_id


def synthesize(subtitles: list[dict], out_dir: str, log,
               engine: str = "fish-audio", voice: str = "",
               voice_wav: str = "", voice_text: str = "",
               seed: int = -1, on_segment=None, **kwargs) -> list[dict]:
    from fish_audio_sdk import Session, TTSRequest, ReferenceAudio

    api_key = os.environ.get("FISH_API_KEY", "")
    if not api_key:
        raise RuntimeError("FISH_API_KEY не задан. Укажите в .env или настройках.")

    session = Session(api_key)
    has_ref = voice_wav and os.path.exists(voice_wav)
    reference_id = voice if voice else ""

    # Clone: create persistent model
    if has_ref:
        reference_id = _get_or_create_clone(voice_wav, voice_text, session, log)

    if not reference_id:
        raise RuntimeError("Выберите голос из списка Fish Audio или укажите клонированный голос.")

    log(f"🔊 Синтезирую речь (Fish Audio, модель: {reference_id})...")

    audio_dir = os.path.join(out_dir, "tts_audio")
    os.makedirs(audio_dir, exist_ok=True)

    total = len(subtitles)
    skipped = 0
    result_subs = []

    for sub in subtitles:
        audio_path = os.path.join(audio_dir, f"seg_{sub['index']:04d}.wav")
        if os.path.exists(audio_path):
            result_subs.append({**sub, "audio_path": audio_path})
            skipped += 1
            continue

        try:
            req = TTSRequest(
                text=sub["text"],
                reference_id=reference_id,
                format="wav",
            )
            with open(audio_path, "wb") as f:
                for chunk in session.tts(req):
                    f.write(chunk)

            result_subs.append({**sub, "audio_path": audio_path})
            if on_segment:
                on_segment(sub["index"])
        except Exception as e:
            log(f"   ❌ Сегмент {sub['index']}: {e}")
            skipped += 1

        if sub["index"] % 5 == 0 or sub["index"] == total:
            log(f"   🔊 TTS: {sub['index']}/{total}")

    if skipped:
        log(f"   ⏭️ Пропущено: {skipped}")
    log("✅ Fish Audio синтез завершён")
    return result_subs
