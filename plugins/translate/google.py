"""Google Translate plugin -- free, no API key needed."""
import os

ENGINES = {"google": "Google Translate"}

GOOGLE_LANG_CODES = {
    "Russian": "ru", "English": "en", "Spanish": "es", "French": "fr",
    "German": "de", "Chinese": "zh-CN", "Japanese": "ja", "Korean": "ko",
    "Portuguese": "pt", "Italian": "it", "Polish": "pl", "Turkish": "tr",
    "Arabic": "ar", "Hindi": "hi",
}


def translate(subtitles: list[dict], target_lang: str, out_dir: str, log,
              on_chunk=None, **kwargs) -> list[dict]:
    from deep_translator import GoogleTranslator
    lang_code = GOOGLE_LANG_CODES.get(target_lang, "ru")
    log(f"🌐 Перевожу на {target_lang} через Google Translate...")

    translator = GoogleTranslator(source="auto", target=lang_code)
    translated = []

    for i, sub in enumerate(subtitles):
        try:
            tr_text = translator.translate(sub["text"])
        except Exception:
            tr_text = sub["text"]
        t = {**sub, "text": tr_text or sub["text"]}
        translated.append(t)
        if on_chunk:
            on_chunk([t])
        if (i + 1) % 20 == 0:
            log(f"   Переведено {i+1}/{len(subtitles)} фраз")

    from pipeline import write_srt
    srt_path = os.path.join(out_dir, "translated.srt")
    write_srt(translated, srt_path)
    log("✅ Перевод готов")
    return translated
