"""OpenAI Whisper API plugin -- cloud transcription."""
import os


ENGINES = {"whisper-api": "OpenAI Whisper API (облачно)"}


def transcribe(audio_path: str, out_dir: str, model_name: str, log,
               source_language: str = "", api_key: str = "",
               on_segment=None, **kwargs) -> list[dict]:
    log("🎙️ Транскрибирую — OpenAI Whisper API...")
    import openai
    client = openai.OpenAI(api_key=api_key)

    with open(audio_path, "rb") as f:
        opts = {"model": "whisper-1", "response_format": "verbose_json", "file": f}
        if source_language:
            opts["language"] = source_language
        result = client.audio.transcriptions.create(**opts)

    subtitles = []
    for i, seg in enumerate(result.segments, 1):
        subtitles.append({
            "index": i,
            "start": seg["start"],
            "end":   seg["end"],
            "text":  seg["text"].strip(),
        })

    from pipeline import write_srt
    srt_path = os.path.join(out_dir, "original.srt")
    write_srt(subtitles, srt_path)
    log(f"✅ Транскрипция готова: {len(subtitles)} фраз")
    return subtitles
