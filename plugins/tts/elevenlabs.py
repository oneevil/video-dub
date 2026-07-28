"""ElevenLabs TTS plugin -- cloud TTS with voice cloning."""
import os


ENGINES = {"elevenlabs": "ElevenLabs (облако)"}

API_KEY_ENV = "ELEVENLABS_API_KEY"

# Cache cloned voice_id by WAV path to avoid re-creating
_clone_cache = {}  # voice_wav_path -> voice_id


def reset_clone_cache():
    """Reset clone cache (used by multi-speaker)."""
    _clone_cache.clear()


def synthesize(subtitles: list[dict], out_dir: str, log,
               engine: str = "elevenlabs", voice: str = "",
               voice_wav: str = "", voice_text: str = "",
               seed: int = -1, on_segment=None, **kwargs) -> list[dict]:
    from elevenlabs.client import ElevenLabs

    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY не задан. Укажите в .env или настройках.")

    client = ElevenLabs(api_key=api_key)

    # Voice: ElevenLabs voice_id from preset selector, or clone from WAV
    voice_id = voice if voice else ""

    if voice_wav and os.path.exists(voice_wav):
        # Check memory cache first
        if voice_wav in _clone_cache:
            voice_id = _clone_cache[voice_wav]
            log(f"   🎙️ Клонированный голос (кэш): {voice_id}")
        else:
            # Check persistent cache (JSON file next to WAV)
            import json as _json
            import hashlib
            wav_hash = hashlib.md5(voice_wav.encode()).hexdigest()[:8]
            cache_file = os.path.join(os.path.dirname(voice_wav), f".elevenlabs_clone_{wav_hash}.json")
            cached_id = None
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, encoding="utf-8") as cf:
                        cached = _json.loads(cf.read())
                    cached_id = cached.get("voice_id")
                    # Verify voice still exists on ElevenLabs
                    try:
                        client.voices.get(cached_id)
                    except Exception:
                        cached_id = None  # Voice was deleted
                except Exception:
                    cached_id = None

            if cached_id:
                voice_id = cached_id
                _clone_cache[voice_wav] = voice_id
                log(f"   🎙️ Клонированный голос (сохранён): {voice_id}")
            else:
                log("   🎙️ Клонирую голос через ElevenLabs (IVC)...")
                try:
                    resp = client.voices.ivc.create(
                        name=os.path.basename(os.path.dirname(voice_wav)),
                        files=[open(voice_wav, "rb")],
                    )
                    voice_id = resp.voice_id
                    _clone_cache[voice_wav] = voice_id
                    # Save to persistent cache
                    with open(cache_file, "w", encoding="utf-8") as cf:
                        cf.write(_json.dumps({"voice_id": voice_id, "wav": voice_wav}))
                    log(f"   ✅ Голос клонирован: {voice_id}")
                except Exception as e:
                    err = str(e)
                    if "missing_permissions" in err or "permission" in err.lower():
                        raise RuntimeError(
                            "⚠️ API ключ не поддерживает клонирование (IVC). "
                            "Выберите предустановленный голос из списка ElevenLabs.")
                    raise

    if not voice_id:
        # No voice selected — use first available from account
        try:
            resp = client.voices.get_all()
            if resp.voices:
                voice_id = resp.voices[0].voice_id
                log(f"   🔊 Голос по умолчанию: {resp.voices[0].name}")
        except Exception:
            pass
    if not voice_id:
        raise RuntimeError("Выберите голос из списка ElevenLabs или укажите клонированный голос.")

    log(f"🔊 Синтезирую речь (ElevenLabs, голос: {voice_id})...")

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
            audio_iter = client.text_to_speech.convert(
                voice_id=voice_id,
                text=sub["text"],
                model_id="eleven_multilingual_v2",
                output_format="wav_24000",
            )
            with open(audio_path, "wb") as f:
                for chunk in audio_iter:
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
    log("✅ ElevenLabs синтез завершён")
    return result_subs
