"""OpenAI translation plugin."""
import os

ENGINES = {"openai": "OpenAI"}

API_KEY_ENV = "OPENAI_API_KEY"
NEEDS_MODEL = True

def list_models(api_key: str = "") -> list[dict]:
    """Fetch available models from OpenAI API. Returns list of {id, name} dicts."""
    if not api_key:
        return []
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        result = []
        for m in client.models.list().data:
            mid = m.id
            # Filter to chat/text models only (skip embeddings, tts, whisper, dall-e, etc.)
            if any(p in mid for p in ("embedding", "tts", "whisper", "dall-e", "audio", "moderation", "image")):
                continue
            if mid.startswith(("gpt-", "o1", "o3", "chatgpt-")):
                result.append({"id": mid, "name": mid})
        # Sort: gpt-5 first, then gpt-4, then o3/o1, then gpt-3
        def _sort_key(item):
            mid = item["id"]
            if mid.startswith("gpt-5"): return (0, mid)
            if mid.startswith("gpt-4"): return (1, mid)
            if mid.startswith("o3"): return (2, mid)
            if mid.startswith("o1"): return (3, mid)
            return (4, mid)
        result.sort(key=_sort_key)
        return result
    except Exception:
        return []


def translate(subtitles: list[dict], target_lang: str, out_dir: str, log,
              api_key: str = "", model: str = "", on_chunk=None, **kwargs) -> list[dict]:
    import openai
    from ._helpers import build_translate_prompt, parse_numbered_response

    if not model:
        raise ValueError("Не выбрана модель OpenAI. Откройте настройки и выберите модель.")
    log(f"🌐 Перевожу на {target_lang} через OpenAI ({model})...")
    client = openai.OpenAI(api_key=api_key)

    chunk_size = 50
    translated = []

    for i in range(0, len(subtitles), chunk_size):
        chunk = subtitles[i:i + chunk_size]
        numbered = "\n".join(f"{sub['index']}|{sub['text']}" for sub in chunk)
        prompt = build_translate_prompt(numbered, target_lang)

        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=16000,   # 4096 не хватало на фрагмент из 50 фраз
        )
        if resp.choices[0].finish_reason == "length":
            raise RuntimeError(
                "OpenAI не уместил перевод в лимит ответа — часть фраз осталась бы "
                "на исходном языке. Уменьшите размер фрагмента или смените модель."
            )
        raw = (resp.choices[0].message.content or "").strip()
        tr_map = parse_numbered_response(raw, chunk, log)
        chunk_translated = []
        for sub in chunk:
            t = {**sub, "text": tr_map.get(sub["index"], sub["text"])}
            translated.append(t)
            chunk_translated.append(t)
        if on_chunk:
            on_chunk(chunk_translated)
        log(f"   Переведено {min(i+chunk_size, len(subtitles))}/{len(subtitles)} фраз")

    from pipeline import write_srt
    srt_path = os.path.join(out_dir, "translated.srt")
    write_srt(translated, srt_path)
    log("✅ Перевод готов")
    return translated
