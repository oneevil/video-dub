"""Shared helpers for LLM-based translation plugins."""


def build_translate_prompt(numbered: str, target_lang: str) -> str:
    return (
        f"Translate the following subtitles to {target_lang}. "
        f"Keep the same line numbering format (number|text). "
        f"Preserve natural speech rhythm. Return ONLY the translated lines, no explanations.\n\n"
        f"{numbered}"
    )


def parse_numbered_response(raw: str, chunk: list[dict], log=None) -> dict[int, str]:
    """Parse 'number|text' lines from LLM response.

    Пропущенные номера вызывающий код молча заменяет исходным текстом, поэтому
    оборванный или испорченный ответ выглядел бы как успешный перевод. Если
    передан log — предупреждаем.
    """
    tr_map = {}
    for line in raw.splitlines():
        if "|" in line:
            parts = line.split("|", 1)
            try:
                tr_map[int(parts[0].strip())] = parts[1].strip()
            except ValueError:
                pass
    if log:
        missing = [s["index"] for s in chunk if s["index"] not in tr_map]
        if missing:
            log(f"   ⚠️ Осталось без перевода фраз: {len(missing)} (начиная с № {missing[0]})")
    return tr_map
