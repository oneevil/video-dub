#!/usr/bin/env python3
"""Setup all isolated venvs for video-dub plugins.
Usage: uv run python setup_all.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
_VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"


# Создание окружений и установку берём из pipeline, а не повторяем здесь: своя
# копия уже разошлась с ним и тянула torch без CUDA, а окружение WhisperX
# строила на неподдерживаемом Python.
from pipeline import create_venv, ensure_cuda_torch, pip_install  # noqa: E402

VENVS = {
    ".venv-faster-whisper": {"deps": ["faster-whisper>=1.2.0"]},
    # whisperx объявляет requires-python <3.14
    ".venv-whisperx": {"deps": ["whisperx>=3.8.0", "pyannote-audio>=4.0.0",
                                "torch>=2.8.0", "torchaudio>=2.8.0"], "max_py": 13},
    ".venv-omnivoice": {"deps": ["omnivoice>=0.1.3", "torch>=2.8.0", "torchaudio>=2.8.0",
                                 "torchcodec", "soundfile>=0.12.0"]},
    # numpy: demucs объявляет его только для darwin x86_64, но импортирует всегда
    ".venv-demucs": {"deps": ["demucs>=4.0.0", "torch>=2.8.0", "torchaudio>=2.8.0",
                              "torchcodec", "numpy"]},
}


def setup_venv(name, cfg):
    deps = cfg["deps"]
    venv_path = os.path.join(ROOT, name)
    python = os.path.join(venv_path, _VENV_BIN, "python")
    pip = os.path.join(venv_path, _VENV_BIN, "pip")
    marker = os.path.join(venv_path, ".deps_ok")

    # Наличие python недостаточно: pip мог упасть на середине — тогда ставим заново
    if os.path.exists(python) and os.path.exists(marker):
        print(f"  ✅ {name} уже существует")
        ensure_cuda_torch(venv_path, deps, lambda m: print(f" {m}"))
        return True

    print(f"  📦 Создаю {name}...")
    try:
        create_venv(venv_path, cfg.get("max_py"))
    except Exception as e:
        print(f"  ❌ Ошибка создания venv: {e}")
        return False

    print(f"  📦 Устанавливаю: {', '.join(d.split('>=')[0] for d in deps)}")
    r = pip_install(pip, deps)
    if r.returncode != 0:
        print(f"  ❌ Ошибка установки: {r.stderr[:300]}")
        return False

    with open(marker, "w", encoding="utf-8") as f:
        f.write("ok\n")
    print(f"  ✅ {name} готов")
    return True


def main():
    print("🚀 Установка всех окружений video-dub\n")

    ok = 0
    fail = 0
    for name, cfg in VENVS.items():
        print(f"\n{'─' * 40}")
        print(f"📦 {name}")
        if setup_venv(name, cfg):
            ok += 1
        else:
            fail += 1

    print(f"\n{'─' * 40}")
    print(f"\n✅ Готово: {ok} окружений")
    if fail:
        print(f"❌ Ошибок: {fail}")
    else:
        print("🎉 Все окружения установлены!")


if __name__ == "__main__":
    main()
