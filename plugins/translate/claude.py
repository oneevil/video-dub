"""Claude (Anthropic) translation plugin."""
import os

ENGINES = {"claude": "Claude (Anthropic)"}

API_KEY_ENV = "ANTHROPIC_API_KEY"
NEEDS_MODEL = True

MODELS = [
    {"id": "claude-sonnet-4-5", "name": "Claude Sonnet 4.5"},
    {"id": "claude-opus-4-6", "name": "Claude Opus 4.6"},
    {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5"},
]

DEFAULT_MODEL = "claude-sonnet-4-5-20250514"


def translate(subtitles: list[dict], target_lang: str, out_dir: str, log,
              api_key: str = "", model: str = "", on_chunk=None, **kwargs) -> list[dict]:
    import anthropic
    from ._helpers import build_translate_prompt, parse_numbered_response

    model = model or DEFAULT_MODEL
    log(f"🌐 Перевожу на {target_lang} через Claude ({model})...")
    client = anthropic.Anthropic(api_key=api_key)

    chunk_size = 50
    translated = []

    for i in range(0, len(subtitles), chunk_size):
        chunk = subtitles[i:i + chunk_size]
        numbered = "\n".join(f"{sub['index']}|{sub['text']}" for sub in chunk)
        prompt = build_translate_prompt(numbered, target_lang)

        resp = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
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
