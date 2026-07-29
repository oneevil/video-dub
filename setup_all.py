#!/usr/bin/env python3
"""Setup all isolated venvs for video-dub plugins.
Usage: uv run python setup_all.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
_VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"


def _detect_cuda_tag():
    """Detect installed CUDA and return best matching PyTorch wheel tag."""
    try:
        r = subprocess.run(["nvcc", "--version"], capture_output=True, text=True, encoding="utf-8")
        if r.returncode == 0:
            import re
            m = re.search(r"release (\d+)\.(\d+)", r.stdout)
            if m:
                ver = int(m.group(1)) * 10 + int(m.group(2))
                available = [130, 129, 128, 126, 124, 121, 118]
                for tag in available:
                    if tag <= ver:
                        return f"cu{tag}"
    except FileNotFoundError:
        pass
    return "cu128"


def _torch_index_args():
    """Return pip args for installing CUDA-enabled PyTorch (Windows/Linux)."""
    if sys.platform == "darwin":
        return []
    tag = _detect_cuda_tag()
    return ["--extra-index-url", f"https://download.pytorch.org/whl/{tag}"]

VENVS = {
    ".venv-faster-whisper": ["faster-whisper>=1.2.0"],
    ".venv-whisperx": ["whisperx>=3.8.0", "pyannote-audio>=4.0.0", "torch>=2.8.0", "torchaudio>=2.8.0"],
    ".venv-qwen3": ["qwen-tts>=0.1.1", "transformers>=4.57.3", "torch>=2.8.0", "torchaudio>=2.8.0", "soundfile>=0.12.0", "numpy"],
    ".venv-omnivoice": ["omnivoice>=0.1.3", "torch>=2.8.0", "torchaudio>=2.8.0", "torchcodec", "soundfile>=0.12.0"],
    # numpy: demucs объявляет его только для darwin x86_64, но импортирует всегда
    ".venv-demucs": ["demucs>=4.0.0", "torch>=2.8.0", "torchaudio>=2.8.0", "torchcodec", "numpy"],
}


def setup_venv(name, deps):
    venv_path = os.path.join(ROOT, name)
    python = os.path.join(venv_path, _VENV_BIN, "python")
    pip = os.path.join(venv_path, _VENV_BIN, "pip")
    marker = os.path.join(venv_path, ".deps_ok")

    # Наличие python недостаточно: pip мог упасть на середине — тогда ставим заново
    if os.path.exists(python) and os.path.exists(marker):
        print(f"  ✅ {name} уже существует")
        return True

    print(f"  📦 Создаю {name}...")
    r = subprocess.run([sys.executable, "-m", "venv", venv_path], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print(f"  ❌ Ошибка создания venv: {r.stderr[:200]}")
        return False

    print(f"  📦 Устанавливаю: {', '.join(d.split('>=')[0] for d in deps)}")
    has_torch = any("torch" in d for d in deps)
    extra = _torch_index_args() if has_torch else []
    r = subprocess.run([pip, "install"] + extra + deps, capture_output=True, text=True, encoding="utf-8")
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
