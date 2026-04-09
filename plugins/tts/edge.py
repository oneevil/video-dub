"""Edge TTS plugin -- Microsoft cloud TTS."""
import os
import subprocess

ENGINES = {"edge-tts": "Edge TTS (Microsoft, облако)"}


def synthesize(subtitles: list[dict], out_dir: str, log,
               engine: str = "edge-tts", voice: str = "",
               on_segment=None, **kwargs) -> list[dict]:
    import asyncio
    import edge_tts

    voice = voice or "ru-RU-DmitryNeural"
    log(f"🔊 Синтезирую речь (Edge TTS, голос: {voice})...")

    audio_dir = os.path.join(out_dir, "tts_audio")
    os.makedirs(audio_dir, exist_ok=True)

    total = len(subtitles)
    skipped = 0
    result_subs = []

    async def generate(text, path):
        comm = edge_tts.Communicate(text=text, voice=voice)
        await comm.save(path)

    for sub in subtitles:
        audio_path = os.path.join(audio_dir, f"seg_{sub['index']:04d}.wav")
        if os.path.exists(audio_path):
            result_subs.append({**sub, "audio_path": audio_path})
            skipped += 1
            continue
        # Edge TTS saves as mp3, convert to wav
        mp3_path = audio_path.replace(".wav", ".mp3")
        asyncio.run(generate(sub["text"], mp3_path))
        subprocess.run([
            "ffmpeg", "-y", "-i", mp3_path, audio_path
        ], capture_output=True)
        if os.path.exists(mp3_path):
            os.remove(mp3_path)

        result_subs.append({**sub, "audio_path": audio_path})
        if on_segment:
            on_segment(sub["index"])
        if sub["index"] % 5 == 0 or sub["index"] == total:
            log(f"   🔊 TTS: {sub['index']}/{total}")
    if skipped:
        log(f"   ⏭️ Пропущено {skipped} уже сгенерированных сегментов")

    log("✅ Синтез речи завершён")
    return result_subs
