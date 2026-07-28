"""Claude (Anthropic) translation plugin."""
import os

ENGINES = {"claude": "Claude (Anthropic)"}

API_KEY_ENV = "ANTHROPIC_API_KEY"
NEEDS_MODEL = True

def list_models(api_key: str = "") -> list[dict]:
    """Fetch available models from Anthropic API. Returns list of {id, name} dicts."""
    if not api_key:
        return []
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        result = []
        for m in client.models.list(limit=100).data:
            display = getattr(m, "display_name", None) or m.id
            result.append({"id": m.id, "name": display})
        return result
    except Exception:
        return []


def translate(subtitles: list[dict], target_lang: str, out_dir: str, log,
              api_key: str = "", model: str = "", on_chunk=None, **kwargs) -> list[dict]:
    import anthropic
    from ._helpers import build_translate_prompt, parse_numbered_response

    if not model:
        raise ValueError("Не выбрана модель Claude. Откройте настройки и выберите модель.")
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
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}]
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("Claude отклонил запрос на перевод этого фрагмента")
        # В ответе кроме текста могут быть блоки thinking — берём только текстовые
        raw = "".join(b.text for b in resp.content if b.type == "text").strip()
        if not raw:
            raise RuntimeError(
                f"Claude вернул пустой ответ (stop_reason={resp.stop_reason}). "
                "Попробуйте уменьшить размер фрагмента или сменить модель."
            )
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
