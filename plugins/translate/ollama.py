"""Ollama translation plugin -- local LLM via OpenAI-compatible API."""
import os

ENGINES = {"ollama": "Ollama (локально)"}

NEEDS_MODEL = True
NEEDS_BASE_URL = True
DEFAULT_BASE_URL = "http://localhost:11434/v1"


def translate(subtitles: list[dict], target_lang: str, out_dir: str, log,
              model: str = "", base_url: str = "", on_chunk=None, **kwargs) -> list[dict]:
    import openai
    from ._helpers import build_translate_prompt, parse_numbered_response

    model = model or "llama3.1"
    base_url = base_url or DEFAULT_BASE_URL
    if not model:
        raise RuntimeError("Укажите модель для Ollama")

    log(f"🌐 Перевожу на {target_lang} через Ollama ({model})...")
    client = openai.OpenAI(api_key="ollama", base_url=base_url)

    chunk_size = 50
    translated = []

    for i in range(0, len(subtitles), chunk_size):
        chunk = subtitles[i:i + chunk_size]
        numbered = "\n".join(f"{sub['index']}|{sub['text']}" for sub in chunk)
        prompt = build_translate_prompt(numbered, target_lang)

        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content.strip()
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
