"""macOS Say plugin -- system TTS."""
import os
import subprocess
import platform

from pipeline import ProcessingError

ENGINES = {"macos-say": "macOS Say (системный)"}


def check_available():
    return platform.system() == "Darwin"


def synthesize(subtitles: list[dict], out_dir: str, log,
               engine: str = "macos-say", voice: str = "",
               on_segment=None, **kwargs) -> list[dict]:
    if platform.system() != "Darwin":
        raise ProcessingError("macOS Say доступен только на macOS")

    voice_info = f", голос: {voice}" if voice else ""
    log(f"🔊 Синтезирую речь (macOS Say{voice_info})...")

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
        aiff_path = audio_path.replace(".wav", ".aiff")
        cmd = ["say"]
        if voice:
            cmd += ["-v", voice]
        cmd += ["-o", aiff_path, sub["text"]]
        subprocess.run(cmd, check=True)
        # Convert AIFF to WAV
        subprocess.run([
            "ffmpeg", "-y", "-i", aiff_path, audio_path
        ], check=True, capture_output=True)
        os.remove(aiff_path)

        result_subs.append({**sub, "audio_path": audio_path})
        if on_segment:
            on_segment(sub["index"])
        if sub["index"] % 5 == 0 or sub["index"] == total:
            log(f"   🔊 TTS: {sub['index']}/{total}")
    if skipped:
        log(f"   ⏭️ Пропущено {skipped} уже сгенерированных сегментов")

    log("✅ Синтез речи завершён")
    return result_subs
