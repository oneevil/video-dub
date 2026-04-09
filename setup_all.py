#!/usr/bin/env python3
"""Setup all isolated venvs for video-dub plugins.
Usage: uv run python setup_all.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

VENVS = {
    ".venv-whisper": ["openai-whisper>=20250625", "torch>=2.8.0", "torchaudio>=2.8.0"],
    ".venv-faster-whisper": ["faster-whisper>=1.2.0"],
    ".venv-whisperx": ["whisperx>=3.8.0", "pyannote-audio>=4.0.0", "torch>=2.8.0", "torchaudio>=2.8.0"],
    ".venv-qwen3": ["qwen-tts>=0.1.1", "transformers>=4.57.3", "torch>=2.8.0", "torchaudio>=2.8.0", "soundfile>=0.12.0", "numpy"],
    ".venv-omnivoice": ["omnivoice>=0.1.3", "torch>=2.8.0", "torchaudio>=2.8.0", "torchcodec", "soundfile>=0.12.0"],
    ".venv-demucs": ["demucs>=4.0.0", "torch>=2.8.0", "torchaudio>=2.8.0"],
}


def setup_venv(name, deps):
    venv_path = os.path.join(ROOT, name)
    python = os.path.join(venv_path, "bin", "python")
    pip = os.path.join(venv_path, "bin", "pip")

    if os.path.exists(python):
        print(f"  ✅ {name} уже существует")
        return True

    print(f"  📦 Создаю {name}...")
    r = subprocess.run([sys.executable, "-m", "venv", venv_path], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ❌ Ошибка создания venv: {r.stderr[:200]}")
        return False

    print(f"  📦 Устанавливаю: {', '.join(d.split('>=')[0] for d in deps)}")
    r = subprocess.run([pip, "install"] + deps, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ❌ Ошибка установки: {r.stderr[:300]}")
        return False

    print(f"  ✅ {name} готов")
    return True


def main():
    print("🚀 Установка всех окружений video-dub\n")

    ok = 0
    fail = 0
    for name, deps in VENVS.items():
        print(f"\n{'─' * 40}")
        print(f"📦 {name}")
        if setup_venv(name, deps):
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
