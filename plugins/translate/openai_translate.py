"""OpenAI translation plugin."""
import os

ENGINES = {"openai": "OpenAI"}

API_KEY_ENV = "OPENAI_API_KEY"
NEEDS_MODEL = True

MODELS = [
    {"id": "gpt-4.1-mini", "name": "GPT-4.1 Mini"},
    {"id": "gpt-4.1-nano", "name": "GPT-4.1 Nano"},
    {"id": "gpt-4.1", "name": "GPT-4.1"},
    {"id": "gpt-4o", "name": "GPT-4o"},
    {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
    {"id": "o3-mini", "name": "o3 Mini"},
]

DEFAULT_MODEL = "gpt-4.1-mini"


def translate(subtitles: list[dict], target_lang: str, out_dir: str, log,
              api_key: str = "", model: str = "", on_chunk=None, **kwargs) -> list[dict]:
    import openai
    from ._helpers import build_translate_prompt, parse_numbered_response

    model = model or DEFAULT_MODEL
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
            max_tokens=4096,
        )
        raw = resp.choices[0].message.content.strip()
        tr_map = parse_numbered_response(raw, chunk)
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
