"""ElevenLabs STT plugin -- cloud transcription (Scribe v2)."""
import os


ENGINES = {"elevenlabs-stt": "ElevenLabs Scribe (облако)"}


def transcribe(audio_path: str, out_dir: str, model_name: str, log,
               source_language: str = "", on_segment=None, **kwargs) -> list[dict]:
    from elevenlabs.client import ElevenLabs

    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY не задан. Укажите в .env или настройках.")

    lang_msg = f", язык: {source_language}" if source_language else ", язык: auto"
    log(f"🎙️ Транскрибирую — ElevenLabs Scribe{lang_msg}...")

    client = ElevenLabs(api_key=api_key)

    with open(audio_path, "rb") as f:
        opts = {
            "file": f,
            "model_id": "scribe_v2",
            "timestamps_granularity": "word",
        }
        if source_language:
            opts["language_code"] = source_language
        result = client.speech_to_text.convert(**opts)

    # Build subtitles from words — group into sentences by punctuation
    subtitles = []
    words = result.words if hasattr(result, 'words') else []

    if words:
        # Group words into segments by sentence-ending punctuation
        current_text = []
        seg_start = None
        seg_end = None
        idx = 1

        for w in words:
            if w.type != "word":
                continue
            if seg_start is None:
                seg_start = w.start
            seg_end = w.end
            current_text.append(w.text)

            # Split on sentence-ending punctuation
            if w.text and w.text[-1] in '.!?…':
                sub = {
                    "index": idx,
                    "start": seg_start,
                    "end": seg_end,
                    "text": " ".join(current_text).strip(),
                }
                subtitles.append(sub)
                if on_segment:
                    on_segment(sub)
                idx += 1
                current_text = []
                seg_start = None

        # Flush remaining words
        if current_text and seg_start is not None:
            sub = {
                "index": idx,
                "start": seg_start,
                "end": seg_end,
                "text": " ".join(current_text).strip(),
            }
            subtitles.append(sub)
            if on_segment:
                on_segment(sub)
    else:
        # Fallback: single segment from full text
        subtitles = [{
            "index": 1,
            "start": 0,
            "end": 0,
            "text": result.text if hasattr(result, 'text') else str(result),
        }]

    from pipeline import write_srt
    write_srt(subtitles, os.path.join(out_dir, "original.srt"))
    log(f"✅ Транскрипция готова: {len(subtitles)} фраз")
    return subtitles
