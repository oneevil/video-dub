#!/usr/bin/env python3
"""
Video Translator — pipeline functions.
Скачивание → транскрипция → перевод → TTS → сборка
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"


def _detect_cuda_tag():
    """Detect installed CUDA and return best matching PyTorch wheel tag."""
    try:
        r = subprocess.run(["nvcc", "--version"], capture_output=True, text=True, encoding="utf-8")
        if r.returncode == 0:
            import re as _re
            m = _re.search(r"release (\d+)\.(\d+)", r.stdout)
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


# ── Изолированные окружения ───────────────────────────────────────────────────
# Наличие bin/python ещё не значит, что зависимости встали: если pip упал на
# середине, окружение остаётся битым навсегда. Готовность помечаем файлом.
_VENV_MARKER = ".deps_ok"


def venv_python(venv_path: str) -> str:
    return os.path.join(venv_path, _VENV_BIN, "python")


def venv_ready(venv_path: str) -> bool:
    return (os.path.exists(os.path.join(venv_path, _VENV_MARKER))
            and os.path.exists(venv_python(venv_path)))


def mark_venv_ready(venv_path: str):
    with open(os.path.join(venv_path, _VENV_MARKER), "w", encoding="utf-8") as f:
        f.write("ok\n")


def _safe_remove(path: str):
    """Удаление, не падающее на Windows, если файл ещё держит другой процесс."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def dir_size(path: str) -> int:
    """Размер каталога в байтах без двойного счёта.

    В кэше HuggingFace snapshots/ — это симлинки на blobs/, а getsize идёт по
    ссылке и считает файл дважды (модель на 1.5 ГБ показывалась как 3 ГБ).
    """
    import stat as _stat
    total, seen = 0, set()
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                st = os.lstat(fp)          # lstat: не идём по симлинку
            except OSError:
                continue
            if _stat.S_ISLNK(st.st_mode):
                continue                    # цель уже посчитана в blobs/
            if st.st_nlink > 1:             # хардлинки — только один раз
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    continue
                seen.add(key)
            total += st.st_size
    return total


def rmtree_safe(path: str):
    """rmtree, устойчивый к read-only файлам Windows."""
    def _on_error(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=_on_error)


def safe_cwd() -> str:
    """Рабочий каталог для дочерних процессов.

    Запускать их из папки проекта нельзя: её модули (plugins/, pipeline.py)
    затеняют одноимённые библиотеки. Прежний «/» на Windows означает корень
    текущего диска — не всегда доступен для записи, поэтому берём temp.
    """
    import tempfile
    return tempfile.gettempdir()


# ── Принудительная остановка ──────────────────────────────────────────────────
# Останов между этапами недостаточен: ffmpeg, demucs, yt-dlp и TTS-воркеры живут
# минутами. Регистрируем все дочерние процессы и по команде убиваем их.

class Cancelled(Exception):
    """Работа прервана пользователем."""


class CancelScope:
    def __init__(self):
        import threading as _th
        self._event = _th.Event()
        self._procs = set()
        self._lock = _th.Lock()

    def register(self, proc):
        with self._lock:
            if self._event.is_set():
                _terminate(proc)          # отмена пришла, пока процесс стартовал
                raise Cancelled()
            self._procs.add(proc)

    def unregister(self, proc):
        with self._lock:
            self._procs.discard(proc)

    def cancel(self):
        self._event.set()
        with self._lock:
            procs = list(self._procs)
            self._procs.clear()
        for p in procs:
            _terminate(p)
        return len(procs)

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self):
        if self._event.is_set():
            raise Cancelled()


def _terminate(proc):
    """Мягко, затем жёстко — и то и другое работает на Windows."""
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
    except Exception:
        pass


_current_scope: "CancelScope | None" = None


def set_cancel_scope(scope):
    global _current_scope
    _current_scope = scope


def check_cancelled():
    if _current_scope is not None:
        _current_scope.check()


def run(cmd, **kwargs):
    """subprocess.run с регистрацией процесса — чтобы его можно было убить.

    Все внешние вызовы (ffmpeg, ffprobe, yt-dlp) идут через неё, иначе кнопка
    «Стоп» ждала бы окончания текущей операции.
    """
    check_cancelled()
    capture = kwargs.pop("capture_output", False)
    timeout = kwargs.pop("timeout", None)
    check = kwargs.pop("check", False)
    if capture:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    proc = subprocess.Popen(cmd, **kwargs)
    scope = _current_scope
    if scope is not None:
        scope.register(proc)
    try:
        out, err = proc.communicate(timeout=timeout)
    except BaseException:
        _terminate(proc)
        raise
    finally:
        if scope is not None:
            scope.unregister(proc)
    result = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    if scope is not None and scope.cancelled:
        raise Cancelled()
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, out, err)
    return result


def register_proc(proc):
    """Регистрирует внешне созданный процесс (TTS-воркеры, транскрипция)."""
    if _current_scope is not None:
        _current_scope.register(proc)
    return proc


def drain_stderr(proc, maxlines: int = 200):
    """Фоново вычитывает stderr процесса, возвращает deque с хвостом вывода.

    Обязательно для любого Popen(stderr=PIPE), чей stderr не читают сразу:
    труба всего 16 КБ (macOS), и как только tqdm/варнинги её заполнят, дочерний
    процесс навсегда блокируется на записи — снаружи это выглядит как зависание.
    """
    from collections import deque
    import threading as _th
    tail = deque(maxlen=maxlines)
    if getattr(proc, "stderr", None) is None:
        return tail

    def _read():
        try:
            for line in proc.stderr:
                tail.append(line.rstrip())
        except Exception:
            pass

    _th.Thread(target=_read, daemon=True).start()
    return tail


# Ensure all Python processes use UTF-8 on Windows (default is charmap/cp1252)
os.environ.setdefault("PYTHONUTF8", "1")

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
WHISPER_MODELS_DIR = os.path.join(MODELS_DIR, "whisper")
# Кэш HuggingFace общий для всех движков: туда качаются и TTS-модели, и
# whisper/pyannote (whisperx), demucs, latentsync
HF_CACHE_DIR = os.path.join(MODELS_DIR, "hf")
os.makedirs(HF_CACHE_DIR, exist_ok=True)
os.environ.setdefault("HF_HOME", HF_CACHE_DIR)

LANGUAGES = {
    "Русский":     "Russian",
    "English":     "English",
    "Español":     "Spanish",
    "Français":    "French",
    "Deutsch":     "German",
    "中文":        "Chinese",
    "日本語":      "Japanese",
    "한국어":      "Korean",
    "Português":   "Portuguese",
    "Italiano":    "Italian",
    "Polski":      "Polish",
    "Türkçe":      "Turkish",
    "Українська":  "uk",
}

WHISPER_MODELS = ["turbo", "large-v3-turbo", "large-v3", "large-v2", "large", "medium", "small", "base", "tiny"]

SOURCE_LANGUAGES = {
    "English":     "en",
    "Русский":     "ru",
    "Español":     "es",
    "Français":    "fr",
    "Deutsch":     "de",
    "中文":        "zh",
    "日本語":      "ja",
    "한국어":      "ko",
    "Português":   "pt",
    "Italiano":    "it",
    "Polski":      "pl",
    "Türkçe":      "tr",
    "Українська":  "uk",
}

# Dynamic transcription engine discovery
from plugins.transcribe import discover_plugins as _discover_transcribe
TRANSCRIBE_ENGINES, _TRANSCRIBE_PLUGINS = _discover_transcribe()

# Dynamic translation engine discovery
from plugins.translate import discover_plugins as _discover_translate
TRANSLATE_ENGINES, _TRANSLATE_PLUGINS = _discover_translate()
TRANSLATE_PROVIDERS = list(TRANSLATE_ENGINES.keys())


# ──────────────────────────────────────────────────────────────────────────────
# УТИЛИТЫ — SRT
# ──────────────────────────────────────────────────────────────────────────────

def parse_srt(srt_text: str) -> list[dict]:
    """Парсит SRT-файл в список словарей {index, start, end, text}."""
    blocks = re.split(r'\n\s*\n', srt_text.strip())
    subtitles = []
    for block in blocks:
        lines = block.strip().splitlines()
        # 2 строки = субтитр с пустым текстом (его тоже нужно сохранить)
        if len(lines) < 2:
            continue
        try:
            idx = int(lines[0].strip())
            times = lines[1].strip()
            m = re.match(
                r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})',
                times
            )
            if not m:
                continue
            start = (int(m.group(1))*3600 + int(m.group(2))*60 +
                     int(m.group(3)) + int(m.group(4))/1000)
            end   = (int(m.group(5))*3600 + int(m.group(6))*60 +
                     int(m.group(7)) + int(m.group(8))/1000)
            text = "\n".join(lines[2:]).strip()
            subtitles.append({"index": idx, "start": start, "end": end, "text": text})
        except (ValueError, IndexError):
            continue
    return subtitles


def secs_to_srt_time(s: float) -> str:
    # округление ведём по всей величине, иначе 1.9999 даёт ms=1000
    total_ms = max(0, int(round(float(s) * 1000)))
    h, rem = divmod(total_ms, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    sec, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def write_srt(subtitles: list[dict], path: str):
    with open(path, "w", encoding="utf-8") as f:
        for sub in subtitles:
            f.write(f"{sub['index']}\n")
            f.write(f"{secs_to_srt_time(sub['start'])} --> {secs_to_srt_time(sub['end'])}\n")
            f.write(f"{sub['text']}\n\n")


_SENTENCE_END = ('.', '!', '?', '…', '"', '»', ')', ':', ';')


def _ends_sentence(text: str) -> bool:
    t = (text or "").rstrip()
    if not t:
        return True
    # «т.д.», «т.е.», инициалы — точка не заканчивает фразу
    if len(t) >= 2 and t[-1] == '.' and t[-2].isalpha() and t[-2].islower() and len(t.split()[-1]) <= 2:
        return False
    return t.endswith(_SENTENCE_END)


def merge_into_sentences(subtitles: list[dict], others: list[dict] | None = None,
                         max_gap: float = 1.0, max_dur: float = 25.0,
                         max_chars: int = 500, min_dur: float = 1.6,
                         min_chars: int = 28) -> tuple[list[dict], list[dict]]:
    """Склеивает соседние субтитры в законченные фразы.

    Транскрипция режет речь по паузам дыхания, а не по предложениям: 88%
    сегментов обрываются на полуслове. Синтез каждого куска отдельно даёт
    оборванную интонацию и рывки на стыках. Склеиваем, пока фраза не закончена.

    Группа разрывается на: знаке конца предложения, паузе больше max_gap,
    смене говорящего. `others` (оригинальные субтитры) склеиваются по тем же
    границам, чтобы пары строк в редакторе не разъехались.

    Возвращает (склеенные, склеенные_others) с новой сквозной нумерацией.
    """
    if not subtitles:
        return [], (others or [])

    # Предложения заканчиваются внутри сегментов, а не на их границах, поэтому
    # сперва режем сегменты по знакам препинания. Время куска считаем
    # пропорционально длине текста — слова внутри сегмента звучат равномерно.
    def _to_pieces(subs: list[dict]) -> list[dict]:
        out: list[dict] = []
        for sub in subs:
            text = (sub.get("text") or "").strip()
            start, end = sub.get("start", 0), sub.get("end", 0)
            parts = [p for p in re.split(r'(?<=[.!?…])\s+', text) if p.strip()]
            if len(parts) < 2 or end <= start or not text:
                out.append({**sub, "text": text, "start": start, "end": end})
                continue
            total = sum(len(p) for p in parts)
            pos = start
            for p in parts:
                share = (end - start) * (len(p) / total)
                out.append({**sub, "text": p.strip(), "start": pos,
                            "end": min(pos + share, end)})
                pos += share
        return out

    subtitles = _to_pieces(subtitles)  # дальше работаем с кусками предложений

    chains: list[list[int]] = []
    cur: list[int] = [0]
    for i in range(1, len(subtitles)):
        prev, sub = subtitles[i - 1], subtitles[i]
        gap = sub.get("start", 0) - prev.get("end", 0)
        same_speaker = prev.get("speaker", "") == sub.get("speaker", "")
        if _ends_sentence(prev.get("text", "")) or gap > max_gap or not same_speaker:
            chains.append(cur)
            cur = [i]
        else:
            cur.append(i)
    chains.append(cur)

    def _too_long(chain: list[int]) -> bool:
        dur = subtitles[chain[-1]].get("end", 0) - subtitles[chain[0]].get("start", 0)
        chars = sum(len(subtitles[j].get("text") or "") for j in chain)
        return dur > max_dur or chars > max_chars

    def _split(chain: list[int]) -> list[list[int]]:
        """Предложение нередко кончается в середине сегмента, поэтому цепочка
        «незавершённых» может тянуться минутами. Режем её по мягкой границе —
        запятая/тире или самая длинная пауза, — а не в случайном месте."""
        if len(chain) < 2 or not _too_long(chain):
            return [chain]
        best_k, best_score = 0, None
        for k in range(len(chain) - 1):
            j, nxt = chain[k], chain[k + 1]
            text = (subtitles[j].get("text") or "").rstrip()
            soft = 2.0 if text.endswith((',', '—', '–', '-', ':', ';')) else 0.0
            gap = min(subtitles[nxt].get("start", 0) - subtitles[j].get("end", 0), 1.0)
            centered = 1.0 - abs(k - (len(chain) - 1) / 2) / max(len(chain) - 1, 1)
            score = soft + gap + centered
            if best_score is None or score > best_score:
                best_k, best_score = k, score
        return _split(chain[:best_k + 1]) + _split(chain[best_k + 1:])

    groups: list[list[int]] = []
    for chain in chains:
        groups.extend(_split(chain))

    # Слишком короткие фразы («Поворот.») подтягиваем к соседям: на 0.5-секундном
    # тексте клонированный голос у TTS-моделей уплывает — сегмент звучит чужим.
    merged_short: list[list[int]] = []
    for g in groups:
        dur = subtitles[g[-1]].get("end", 0) - subtitles[g[0]].get("start", 0)
        chars = sum(len(subtitles[j].get("text") or "") for j in g)
        if merged_short and (dur < min_dur or chars < min_chars):
            prev_g = merged_short[-1]
            gap = subtitles[g[0]].get("start", 0) - subtitles[prev_g[-1]].get("end", 0)
            same_speaker = (subtitles[prev_g[0]].get("speaker", "")
                            == subtitles[g[0]].get("speaker", ""))
            prev_dur = subtitles[prev_g[-1]].get("end", 0) - subtitles[prev_g[0]].get("start", 0)
            prev_chars = sum(len(subtitles[j].get("text") or "") for j in prev_g)
            fits = (prev_dur + dur <= max_dur) and (prev_chars + chars <= max_chars)
            if gap <= max_gap and same_speaker and fits:
                merged_short[-1] = prev_g + g
                continue
        merged_short.append(g)
    groups = merged_short

    def _join(idxs: list[int], new_index: int) -> dict:
        parts = [(subtitles[i].get("text") or "").strip() for i in idxs]
        first, last = subtitles[idxs[0]], subtitles[idxs[-1]]
        merged = {**first,
                  "index": new_index,
                  "start": first.get("start", 0),
                  "end": last.get("end", 0),
                  "text": " ".join(p for p in parts if p)}
        merged.pop("_src", None)
        return merged

    merged_subs = [_join(g, n) for n, g in enumerate(groups, 1)]

    # Оригиналы режем не по тексту (границы предложений в другом языке иные),
    # а по времени итоговых фраз — чтобы пары строк в редакторе не разъехались.
    merged_others = []
    if others:
        other_pieces = _to_pieces(others)
        for n, m in enumerate(merged_subs, 1):
            texts = [(o.get("text") or "").strip() for o in other_pieces
                     if m["start"] - 0.01 <= (o.get("start", 0) + o.get("end", 0)) / 2 < m["end"] + 0.01]
            merged_others.append({"index": n, "start": m["start"], "end": m["end"],
                                  "text": " ".join(t for t in texts if t)})
    return merged_subs, merged_others


def segments_signature(subtitles: list[dict]) -> str:
    """Отпечаток разбивки: по нему видно, что готовые TTS-сегменты устарели."""
    import hashlib
    raw = ";".join(f"{s.get('index')}:{s.get('start', 0):.2f}-{s.get('end', 0):.2f}"
                   for s in subtitles or [])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def write_speaker_map(subtitles, path):
    """Save speaker assignments: {index_str: speaker_label}."""
    mapping = {str(sub["index"]): sub.get("speaker", "") for sub in subtitles if sub.get("speaker")}
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(mapping, ensure_ascii=False, indent=2))


# ──────────────────────────────────────────────────────────────────────────────
# ШАГИ ОБРАБОТКИ
# ──────────────────────────────────────────────────────────────────────────────

class ProcessingError(Exception):
    pass


def check_dependencies(log):
    """Проверяет наличие системных зависимостей."""
    missing = []
    for tool in ["yt-dlp", "ffmpeg"]:
        if not shutil.which(tool):
            missing.append(tool)
    if missing:
        raise ProcessingError(
            "Не найдены зависимости:\n" + "\n".join(f"  • {m}" for m in missing)
        )
    log("✅ Все зависимости найдены")


def download_video(url: str, out_dir: str, log) -> str:
    """Скачивает видео через yt-dlp, возвращает путь к файлу."""
    log(f"⬇️  Скачиваю видео: {url}")
    out_template = os.path.join(out_dir, "source.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", out_template,
        url,
    ]
    result = run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise ProcessingError(f"yt-dlp ошибка:\n{result.stderr}")
    # Найти скачанный файл
    for f in Path(out_dir).glob("source.*"):
        if f.suffix in (".mp4", ".mkv", ".webm", ".avi"):
            log(f"✅ Видео скачано: {f.name}")
            return str(f)
    raise ProcessingError("Не удалось найти скачанный файл")


def extract_audio(video_path: str, out_dir: str, log) -> str:
    """Извлекает аудио из видео в WAV 16kHz mono."""
    log("🎵 Извлекаю аудио...")
    audio_path = os.path.join(out_dir, "audio.wav")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-ar", "16000", "-ac", "1", "-vn",
        "-acodec", "pcm_s16le",
        "-map", "0:a:0",
        audio_path
    ]
    result = run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise ProcessingError(f"ffmpeg (audio) ошибка:\n{result.stderr}")
    log("✅ Аудио извлечено")
    return audio_path


DEMUCS_VENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv-demucs")
# numpy: demucs объявляет его только для darwin x86_64, но импортирует всегда
_DEMUCS_DEPS = ["demucs>=4.0.0", "torch>=2.8.0", "torchaudio>=2.8.0", "torchcodec", "numpy"]


def _setup_demucs_venv(log):
    """Create isolated venv for demucs if needed."""
    if venv_ready(DEMUCS_VENV):
        return
    import sys as _sys
    log("   📦 Создаю окружение demucs...")
    run([_sys.executable, "-m", "venv", DEMUCS_VENV], check=True)
    log("   📦 Устанавливаю зависимости...")
    result = run(
        [os.path.join(DEMUCS_VENV, _VENV_BIN, "pip"), "install", "--quiet"] + _torch_index_args() + _DEMUCS_DEPS,
        capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise ProcessingError(f"Ошибка установки demucs: {result.stderr[:500]}")
    mark_venv_ready(DEMUCS_VENV)
    log("   ✅ Окружение demucs готово")


def separate_vocals(audio_path: str, out_dir: str, log) -> tuple[str, str]:
    """Разделяет аудио на голос и фон через demucs (isolated venv). Возвращает (vocals_path, no_vocals_path)."""
    vocals_path = os.path.join(out_dir, "vocals.wav")
    no_vocals_path = os.path.join(out_dir, "no_vocals.wav")

    # Skip if already separated
    if os.path.exists(vocals_path) and os.path.exists(no_vocals_path):
        log("⏭️ Разделение уже выполнено")
        return vocals_path, no_vocals_path

    _setup_demucs_venv(log)

    log("🎙️ Разделяю голос и фон (demucs)...")
    demucs_out = os.path.join(out_dir, "demucs_out")
    python = os.path.join(DEMUCS_VENV, _VENV_BIN, "python")

    # Detect GPU for demucs
    device_check = run(
        [python, "-c", "import torch; print('cuda' if torch.cuda.is_available() else ('mps' if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available() else 'cpu'))"],
        capture_output=True, text=True, encoding="utf-8")
    device = device_check.stdout.strip() if device_check.returncode == 0 else "cpu"
    log(f"   Устройство: {device}")

    cmd = [
        python, "-m", "demucs",
        "--two-stems=vocals",
        "-d", device,
        "-o", demucs_out,
        "--filename", "{stem}.{ext}",
        audio_path
    ]
    result = run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise ProcessingError(f"demucs ошибка:\n{result.stderr}")

    # Find output files — demucs creates subdir named after model
    for model_dir in Path(demucs_out).iterdir():
        if model_dir.is_dir():
            v = model_dir / "vocals.wav"
            nv = model_dir / "no_vocals.wav"
            if v.exists():
                shutil.move(str(v), vocals_path)
            if nv.exists():
                shutil.move(str(nv), no_vocals_path)
            break

    # Cleanup demucs temp
    shutil.rmtree(demucs_out, ignore_errors=True)

    if not os.path.exists(vocals_path):
        raise ProcessingError("demucs: vocals.wav не найден")

    log("✅ Голос и фон разделены")
    return vocals_path, no_vocals_path


def transcribe_audio(audio_path: str, out_dir: str, model_name: str, log,
                      source_language: str = "", engine: str = "faster-whisper",
                      num_speakers: int = 0, on_segment=None) -> list[dict]:
    """Транскрибирует аудио через выбранный движок (plugin system)."""
    plugin = _TRANSCRIBE_PLUGINS.get(engine)
    if not plugin:
        raise ProcessingError(f"Транскрипция движок '{engine}' не найден")
    return plugin.transcribe(audio_path, out_dir, model_name, log,
                             source_language=source_language,
                             num_speakers=num_speakers, on_segment=on_segment)


def translate_subtitles(subtitles: list[dict], target_lang: str,
                         api_key: str, out_dir: str, log,
                         provider: str = "claude",
                         model: str = "",
                         base_url: str = "",
                         on_chunk=None) -> list[dict]:
    """Переводит субтитры через выбранный провайдер (plugin system)."""
    plugin = _TRANSLATE_PLUGINS.get(provider)
    if not plugin:
        raise ProcessingError(f"Провайдер перевода '{provider}' не найден")
    return plugin.translate(subtitles, target_lang, out_dir, log,
                            api_key=api_key, model=model, base_url=base_url,
                            on_chunk=on_chunk)


from plugins.tts import discover_plugins
from plugins.lipsync import discover_plugins as _discover_lipsync
LIPSYNC_ENGINES, _LIPSYNC_PLUGINS = _discover_lipsync()

# Dynamic TTS engine discovery
TTS_ENGINES, _TTS_PLUGINS = discover_plugins()

VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")
os.makedirs(VOICES_DIR, exist_ok=True)


def get_voices() -> list[dict]:
    """List saved voice profiles from voices/ directory."""
    voices = []
    for d in sorted(Path(VOICES_DIR).iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        with open(meta_path, encoding="utf-8") as f:
            meta = json.loads(f.read())
        samples = []
        for s in meta.get("samples", []):
            wav_path = d / s["file"]
            if wav_path.exists():
                samples.append({
                    "file": s["file"],
                    "text": s.get("text", ""),
                    "path": str(wav_path),
                })
        if samples:
            voices.append({
                "name": d.name,
                "path": str(d),
                "samples": samples,
            })
    return voices


def _get_voice_reference(voice_name: str) -> tuple[str, str]:
    """Return (wav_path, text) for a voice — picks first sample."""
    voice_dir = Path(VOICES_DIR) / voice_name
    meta_path = voice_dir / "meta.json"
    if not meta_path.exists():
        return "", ""
    with open(meta_path, encoding="utf-8") as f:
        meta = json.loads(f.read())
    for s in meta.get("samples", []):
        wav = voice_dir / s["file"]
        if wav.exists():
            return str(wav), s.get("text", "")
    return "", ""


def synthesize_speech(subtitles: list[dict], out_dir: str, log,
                       engine: str = "qwen3-1.7b-base",
                       voice: str = "",
                       voice_wav: str = "",
                       voice_text: str = "",
                       seed: int = 44,
                       temperature: float = 0.7,
                       speed: float = 1.0,
                       speaker_voice_map: dict | None = None,
                       language: str = "",
                       on_segment=None) -> list[dict]:
    """Синтезирует речь через выбранный движок (plugin system)."""
    check_cancelled()
    if speaker_voice_map:
        results = _tts_multi_speaker(subtitles, out_dir, log, speaker_voice_map, seed, temperature,
                                     on_segment=on_segment, default_engine=engine, default_voice=voice,
                                     language=language)
    else:
        plugin = _TTS_PLUGINS.get(engine)
        if not plugin:
            raise ProcessingError(f"TTS движок '{engine}' не найден")
        results = plugin.synthesize(subtitles, out_dir, log, engine=engine, voice=voice,
                                 voice_wav=voice_wav, voice_text=voice_text, seed=seed,
                                 temperature=temperature, language=language,
                                 on_segment=on_segment)
    if speed != 1.0:
        _apply_tts_speed(results, speed, log)
    return results


def _apply_tts_speed(results: list[dict], speed: float, log):
    """Change playback speed of generated TTS audio files via ffmpeg atempo."""
    from concurrent.futures import ThreadPoolExecutor
    import threading as _th
    counter = [0]
    lock = _th.Lock()

    def _one(r):
        path = r.get("audio_path", "")
        if not path or not os.path.exists(path):
            return
        tmp = path + ".tmp.wav"
        # ffmpeg atempo supports 0.5–100.0; chain filters for extreme values
        filters = []
        s = speed
        while s > 2.0:
            filters.append("atempo=2.0")
            s /= 2.0
        while s < 0.5:
            filters.append("atempo=0.5")
            s *= 2.0
        filters.append(f"atempo={s:.4f}")
        cmd = ["ffmpeg", "-y", "-i", path, "-af", ",".join(filters), "-ar", "24000", tmp]
        try:
            result = run(cmd, capture_output=True, timeout=120)
            if result.returncode == 0 and os.path.exists(tmp):
                # На Windows замена падает, если файл открыт (например, плеером)
                try:
                    os.replace(tmp, path)
                except OSError as e:
                    log(f"   ⚠️ Не удалось заменить {os.path.basename(path)}: {e}")
                    _safe_remove(tmp)
                    return
                with lock:
                    counter[0] += 1
            elif os.path.exists(tmp):
                _safe_remove(tmp)
        except subprocess.TimeoutExpired:
            log(f"   ⚠️ Таймаут atempo для {os.path.basename(path)}")
            _safe_remove(tmp)

    # Файлы независимы — гоняем ffmpeg параллельно
    with ThreadPoolExecutor(max_workers=max(2, min(8, (os.cpu_count() or 4) // 2))) as pool:
        list(pool.map(_one, results))
    if counter[0]:
        log(f"   ⚡ Скорость речи: {speed}x ({counter[0]} файлов)")


def _tts_multi_speaker(subtitles: list[dict], out_dir: str, log,
                        speaker_voice_map: dict, seed: int = 44,
                        temperature: float = 0.7, on_segment=None,
                        default_engine: str = "", default_voice: str = "",
                        language: str = "") -> list[dict]:
    """Синтезирует речь для нескольких спикеров с разными голосами/движками."""
    log("🔊 Синтезирую речь (мульти-спикер)...")

    # Group subtitles by speaker
    from collections import defaultdict
    speaker_groups = defaultdict(list)
    for sub in subtitles:
        speaker = sub.get("speaker", "")
        if speaker and speaker in speaker_voice_map:
            speaker_groups[speaker].append(sub)
        else:
            # Fallback: use first speaker config or skip
            speaker_groups[speaker or "_default"].append(sub)

    log(f"   👥 Спикеров: {len(speaker_groups)}")
    all_results = []

    for speaker, subs in speaker_groups.items():
        voice_cfg = speaker_voice_map.get(speaker, {})
        if not voice_cfg:
            # Раньше подставлялся первый голос из карты — из-за этого фразы без
            # метки говорящего звучали чужим голосом. Берём выбранный в настройках.
            voice_cfg = ({"engine": default_engine, "voice": default_voice}
                         if default_engine or default_voice
                         else next(iter(speaker_voice_map.values()), {}))
        engine = voice_cfg.get("engine") or default_engine or "edge-tts"
        voice = voice_cfg.get("voice", "") or (default_voice if not voice_cfg.get("engine") else "")

        # Reset clone cache for qwen3 plugin if available
        plugin = _TTS_PLUGINS.get(engine)
        if plugin and hasattr(plugin, 'reset_clone_cache'):
            plugin.reset_clone_cache()

        log(f"   🎤 {speaker}: {engine}, голос: {voice} ({len(subs)} фраз)")

        if not plugin:
            raise ProcessingError(f"TTS движок '{engine}' не найден")

        voice_wav = ""
        voice_text = ""
        if voice:
            voice_wav, voice_text = _get_voice_reference(voice)

        result = plugin.synthesize(subs, out_dir, log, engine=engine, voice=voice,
                                   voice_wav=voice_wav, voice_text=voice_text,
                                   seed=seed, temperature=temperature, language=language,
                                   on_segment=on_segment)
        all_results.extend(result)

    # Sort by index to restore original order
    all_results.sort(key=lambda s: s["index"])
    log("✅ Мульти-спикер синтез завершён")
    return all_results


def lipsync_video(video_path: str, audio_path: str, out_path: str, log,
                   engine: str = "latentsync", **kwargs) -> str:
    """Apply lip sync to video using selected engine (plugin system)."""
    plugin = _LIPSYNC_PLUGINS.get(engine)
    if not plugin:
        raise ProcessingError(f"Lip sync движок '{engine}' не найден")
    return plugin.process(video_path, audio_path, out_path, log, **kwargs)


def get_audio_duration(audio_path: str) -> float:
    """Возвращает длительность аудиофайла в секундах через ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        audio_path
    ]
    result = run(cmd, capture_output=True, text=True, encoding="utf-8")
    data = json.loads(result.stdout)
    for stream in data.get("streams", []):
        if "duration" in stream:
            return float(stream["duration"])
    return 0.0


BUILD_FORMATS = ["mp4", "mkv", "webm"]
BUILD_CODECS = {
    "libx264": "H.264",
    "libx265": "H.265 (HEVC)",
    "copy": "Без перекодирования",
}
BUILD_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
BUILD_AUDIO_BITRATES = ["128k", "192k", "256k", "320k"]


def build_final_video(video_path: str, subtitles_with_audio: list[dict],
                       out_path: str, out_dir: str, log,
                       codec: str = "libx264",
                       preset: str = "fast",
                       audio_bitrate: str = "128k",
                       max_slowdown: float = 3.0,
                       original_audio_mode: str = "none",
                       original_audio_volume: float = 0.1,
                       no_vocals_volume: float = 0.5,
                       vocals_volume: float = 0.15,
                       burn_subtitles: bool = False,
                       srt_path: str = "",
                       start_sec: float = 0,
                       end_sec: float = 0):
    """
    Склеивает финальное видео с настройками.
    """
    log("🎬 Собираю финальное видео...")
    log(f"   ⚙️ Кодек: {codec}, пресет: {preset}, аудио: {audio_bitrate}, макс. замедление: {max_slowdown}x")

    tmp_dir = os.path.join(out_dir, "segments")
    os.makedirs(tmp_dir, exist_ok=True)

    # Get total video duration
    probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path]
    probe_r = run(probe_cmd, capture_output=True, text=True, encoding="utf-8")
    video_duration = float(json.loads(probe_r.stdout).get("format", {}).get("duration", 0))

    v_codec = codec if codec != "copy" else "libx264"

    # Apply time range
    range_start = start_sec if start_sec > 0 else 0.0
    range_end = end_sec if end_sec > 0 else video_duration
    range_end = min(range_end, video_duration)
    if range_start > 0 or range_end < video_duration:
        log(f"   ✂️ Диапазон: {range_start}с — {range_end}с")

    # Sort subtitles by start time
    subs_sorted = sorted(subtitles_with_audio, key=lambda s: s["start"])

    # Длительности читаем заранее и параллельно: это по отдельному запуску
    # ffprobe на каждую фразу, последовательно они складывались в секунды
    from concurrent.futures import ThreadPoolExecutor
    _in_range = [s for s in subs_sorted if s["end"] > range_start and s["start"] < range_end]
    with ThreadPoolExecutor(max_workers=min(16, max(4, (os.cpu_count() or 4)))) as _probe_pool:
        _durs = dict(zip((s["audio_path"] for s in _in_range),
                         _probe_pool.map(get_audio_duration, (s["audio_path"] for s in _in_range))))

    # Build list of all segments: gaps + TTS slots
    timeline = []  # (type, start, dur, sub_or_None, speed_factor)
    cursor = range_start

    for sub in subs_sorted:
        # Skip subtitles outside range
        if sub["end"] <= range_start or sub["start"] >= range_end:
            continue

        # Gap before this subtitle
        gap = sub["start"] - cursor
        if gap > 0.01:
            timeline.append(("gap", cursor, gap, None, 1.0))

        slot_dur = sub["end"] - sub["start"]
        audio_dur = _durs.get(sub["audio_path"], 0.0)
        if audio_dur < 0.05:
            audio_dur = slot_dur

        speed_factor = audio_dur / slot_dur if audio_dur > slot_dur else 1.0
        speed_factor = min(speed_factor, max_slowdown)

        timeline.append(("tts", sub["start"], slot_dur, sub, speed_factor))
        cursor = sub["end"]

    # Trailing gap after last subtitle
    if cursor < range_end - 0.01:
        timeline.append(("gap", cursor, range_end - cursor, None, 1.0))

    total = len(timeline)

    def _render_segment(i, item):
        check_cancelled()
        seg_type, seg_start, seg_dur, sub, speed_factor = item
        v_seg = os.path.join(tmp_dir, f"v_{i:04d}.mp4")
        a_seg = os.path.join(tmp_dir, f"a_{i:04d}.wav")

        if seg_type == "gap":
            # Normal speed gap — keep original video
            cmd_v = [
                "ffmpeg", "-y",
                "-ss", str(seg_start), "-t", str(seg_dur),
                "-i", video_path,
                "-c:v", v_codec, "-preset", preset,
                "-an", v_seg
            ]
            run(cmd_v, capture_output=True)
            # Gap audio depends on original_audio_mode
            if original_audio_mode in ("no_vocals", "voiceover"):
                bg_file = os.path.join(out_dir, "no_vocals.wav")
                vocals_file = os.path.join(out_dir, "vocals.wav")
                if os.path.exists(bg_file):
                    if original_audio_mode == "voiceover" and os.path.exists(vocals_file):
                        # Voiceover gap: bg (no_vocals_volume) + vocals (vocals_volume)
                        bg_tmp_g = os.path.join(tmp_dir, f"gbg_{i:04d}.wav")
                        voc_tmp_g = os.path.join(tmp_dir, f"gvoc_{i:04d}.wav")
                        run([
                            "ffmpeg", "-y", "-ss", str(seg_start), "-t", str(seg_dur),
                            "-i", bg_file, "-ar", "44100", "-ac", "2", bg_tmp_g
                        ], capture_output=True)
                        run([
                            "ffmpeg", "-y", "-ss", str(seg_start), "-t", str(seg_dur),
                            "-i", vocals_file, "-ar", "44100", "-ac", "2", voc_tmp_g
                        ], capture_output=True)
                        run([
                            "ffmpeg", "-y", "-i", bg_tmp_g, "-i", voc_tmp_g,
                            "-filter_complex",
                            f"[0:a]volume={no_vocals_volume:.2f}[bg];[1:a]volume={vocals_volume:.2f}[voc];[bg][voc]amix=inputs=2:duration=first[out]",
                            "-map", "[out]", "-ar", "44100", "-ac", "2", a_seg
                        ], capture_output=True)
                    else:
                        # no_vocals mode: bg at no_vocals_volume
                        run([
                            "ffmpeg", "-y",
                            "-ss", str(seg_start), "-t", str(seg_dur),
                            "-i", bg_file,
                            "-af", f"volume={no_vocals_volume:.2f}",
                            "-ar", "44100", "-ac", "2", a_seg
                        ], capture_output=True)
                else:
                    # Fallback to silence
                    cmd_a = [
                        "ffmpeg", "-y",
                        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
                        "-t", str(seg_dur), a_seg
                    ]
                    run(cmd_a, capture_output=True)
            elif original_audio_mode == "full":
                # Full audio at original_audio_volume
                run([
                    "ffmpeg", "-y",
                    "-ss", str(seg_start), "-t", str(seg_dur),
                    "-i", video_path,
                    "-vn", "-af", f"volume={original_audio_volume:.2f}",
                    "-ar", "44100", "-ac", "2", a_seg
                ], capture_output=True)
            else:
                # none — silence
                cmd_a = [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", str(seg_dur), a_seg
                ]
                run(cmd_a, capture_output=True)
        else:
            # TTS segment — possibly slowed down
            vf = f"setpts={speed_factor:.4f}*PTS"
            cmd_v = [
                "ffmpeg", "-y",
                "-ss", str(seg_start), "-t", str(seg_dur),
                "-i", video_path,
                "-vf", vf,
                "-c:v", v_codec, "-preset", preset,
                "-an", v_seg
            ]
            run(cmd_v, capture_output=True)

            # TTS audio padded/trimmed to match slowed video duration
            target_dur = seg_dur * speed_factor
            tts_tmp = os.path.join(tmp_dir, f"tts_{i:04d}.wav")
            cmd_a = [
                "ffmpeg", "-y",
                "-i", sub["audio_path"],
                "-t", str(target_dur),
                "-af", f"apad=whole_dur={target_dur:.3f}",
                "-ar", "44100", "-ac", "2",
                tts_tmp
            ]
            run(cmd_a, capture_output=True)

            # Mix TTS with background audio if requested
            if original_audio_mode in ("full", "no_vocals", "voiceover"):
                bg_file = os.path.join(out_dir, "no_vocals.wav") if original_audio_mode in ("no_vocals", "voiceover") else None
                if bg_file and os.path.exists(bg_file):
                    bg_src = bg_file
                elif original_audio_mode == "full":
                    bg_src = video_path
                else:
                    bg_src = None

                if bg_src:
                    vol = no_vocals_volume if original_audio_mode in ("no_vocals", "voiceover") else original_audio_volume
                    bg_tmp = os.path.join(tmp_dir, f"bg_{i:04d}.wav")
                    # Extract background for this time range (original timing, not slowed)
                    if bg_src == video_path:
                        run([
                            "ffmpeg", "-y", "-ss", str(seg_start), "-t", str(seg_dur),
                            "-i", bg_src, "-vn", "-ar", "44100", "-ac", "2", bg_tmp
                        ], capture_output=True)
                    else:
                        run([
                            "ffmpeg", "-y", "-ss", str(seg_start), "-t", str(seg_dur),
                            "-i", bg_src, "-ar", "44100", "-ac", "2", bg_tmp
                        ], capture_output=True)
                    # Stretch background to match slowed duration if needed
                    if speed_factor > 1.0:
                        bg_stretched = os.path.join(tmp_dir, f"bgs_{i:04d}.wav")
                        atempo = 1.0 / speed_factor
                        if atempo >= 0.5:
                            af_bg = f"atempo={atempo:.4f}"
                        else:
                            f1 = max(0.5, atempo * 2)
                            f2 = atempo / f1
                            af_bg = f"atempo={f1:.4f},atempo={f2:.4f}"
                        run([
                            "ffmpeg", "-y", "-i", bg_tmp,
                            "-af", af_bg, "-ar", "44100", "-ac", "2", bg_stretched
                        ], capture_output=True)
                        os.replace(bg_stretched, bg_tmp)

                    # Voiceover mode: mix 3 tracks (bg + original vocals + TTS)
                    if original_audio_mode == "voiceover":
                        vocals_file = os.path.join(out_dir, "vocals.wav")
                        if os.path.exists(vocals_file):
                            voc_tmp = os.path.join(tmp_dir, f"voc_{i:04d}.wav")
                            run([
                                "ffmpeg", "-y", "-ss", str(seg_start), "-t", str(seg_dur),
                                "-i", vocals_file, "-ar", "44100", "-ac", "2", voc_tmp
                            ], capture_output=True)
                            if speed_factor > 1.0:
                                voc_stretched = os.path.join(tmp_dir, f"vocs_{i:04d}.wav")
                                run([
                                    "ffmpeg", "-y", "-i", voc_tmp,
                                    "-af", af_bg, "-ar", "44100", "-ac", "2", voc_stretched
                                ], capture_output=True)
                                os.replace(voc_stretched, voc_tmp)
                            run([
                                "ffmpeg", "-y", "-i", tts_tmp, "-i", bg_tmp, "-i", voc_tmp,
                                "-filter_complex",
                                f"[0:a]volume=1.0[tts];[1:a]volume={vol:.2f}[bg];[2:a]volume={vocals_volume:.2f}[voc];"
                                f"[tts][bg][voc]amix=inputs=3:duration=first[out]",
                                "-map", "[out]", "-ar", "44100", "-ac", "2", a_seg
                            ], capture_output=True)
                        else:
                            # No vocals file — fallback to 2-track
                            run([
                                "ffmpeg", "-y", "-i", tts_tmp, "-i", bg_tmp,
                                "-filter_complex",
                                f"[0:a]volume=1.0[tts];[1:a]volume={vol:.2f}[bg];[tts][bg]amix=inputs=2:duration=first[out]",
                                "-map", "[out]", "-ar", "44100", "-ac", "2", a_seg
                            ], capture_output=True)
                    else:
                        # 2-track mix: TTS + background
                        run([
                            "ffmpeg", "-y", "-i", tts_tmp, "-i", bg_tmp,
                            "-filter_complex",
                            f"[0:a]volume=1.0[tts];[1:a]volume={vol:.2f}[bg];[tts][bg]amix=inputs=2:duration=first[out]",
                            "-map", "[out]", "-ar", "44100", "-ac", "2", a_seg
                        ], capture_output=True)
                else:
                    os.replace(tts_tmp, a_seg)
            else:
                os.replace(tts_tmp, a_seg)

        return v_seg, a_seg

    # Сегменты независимы, а каждый — это несколько запусков ffmpeg, поэтому
    # считаем их параллельно: на многоядерной машине сборка ускоряется в разы.
    from concurrent.futures import ThreadPoolExecutor
    import threading as _th
    workers = max(2, min(8, (os.cpu_count() or 4) // 2))
    done_lock = _th.Lock()
    done_count = [0]

    def _render_and_report(args):
        i, item = args
        result = _render_segment(i, item)
        with done_lock:
            done_count[0] += 1
            n = done_count[0]
        if n % max(1, total // 20) == 0 or n == total:
            log(f"   📦 Сегментов: {n}/{total} ({int(n / total * 100)}%)")
        return result

    log(f"   ⚙️ Рендер сегментов в {workers} потоков...")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        segment_files = list(pool.map(_render_and_report, enumerate(timeline)))

    # --- Apply volume crossfades at gap↔TTS boundaries ---
    if original_audio_mode in ("full", "voiceover"):
        fade_dur = 0.8  # seconds — match player lookahead

        def _apply_fade(i):
            seg_type = timeline[i][0]
            if seg_type != "gap":
                return
            _, a_seg = segment_files[i]
            seg_dur = timeline[i][2]
            if seg_dur < 0.1:
                return
            # Check if next segment is TTS → fade out end of gap
            fade_out = i + 1 < len(timeline) and timeline[i + 1][0] == "tts"
            # Check if previous segment is TTS → fade in start of gap
            fade_in = i > 0 and timeline[i - 1][0] == "tts"
            if not fade_out and not fade_in:
                return
            af_parts = []
            fd = min(fade_dur, seg_dur / 2)
            if fade_out:
                # Fade out last fd seconds of gap audio
                af_parts.append(f"afade=t=out:st={max(0, seg_dur - fd):.3f}:d={fd:.3f}")
            if fade_in:
                # Fade in first fd seconds of gap audio
                af_parts.append(f"afade=t=in:st=0:d={fd:.3f}")
            if af_parts:
                faded = a_seg + ".faded.wav"
                run([
                    "ffmpeg", "-y", "-i", a_seg,
                    "-af", ",".join(af_parts),
                    "-ar", "44100", "-ac", "2", faded
                ], capture_output=True)
                os.replace(faded, a_seg)

        with ThreadPoolExecutor(max_workers=workers) as fade_pool:
            list(fade_pool.map(_apply_fade, range(len(timeline))))

    # --- Concat ---
    log("🔗 Конкатенирую сегменты...")
    v_list = os.path.join(tmp_dir, "vlist.txt")
    a_list = os.path.join(tmp_dir, "alist.txt")
    # concat-demuxer трактует \ как escape, поэтому пути пишем через прямой слэш
    # (Windows их принимает), а одинарные кавычки внутри имени экранируем
    def _concat_path(p: str) -> str:
        return p.replace("\\", "/").replace("'", r"'\''")

    with open(v_list, "w", encoding="utf-8") as fv, open(a_list, "w", encoding="utf-8") as fa:
        for v_seg, a_seg in segment_files:
            fv.write(f"file '{_concat_path(v_seg)}'\n")
            fa.write(f"file '{_concat_path(a_seg)}'\n")

    v_concat = os.path.join(tmp_dir, "video_concat.mp4")
    a_concat = os.path.join(tmp_dir, "audio_concat.wav")

    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", v_list, "-c", "copy", v_concat
    ], capture_output=True)

    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", a_list, "-c", "copy", a_concat
    ], capture_output=True)

    # --- Final merge ---
    log("🎞️  Финальная сборка...")

    cmd_final = ["ffmpeg", "-y", "-i", v_concat, "-i", a_concat]
    cmd_final += ["-map", "0:v", "-map", "1:a"]

    # Burn subtitles
    if burn_subtitles and srt_path and os.path.exists(srt_path):
        # порядок важен: сначала обратный слэш (Windows-пути), потом остальное
        escaped_srt = (srt_path.replace("\\", "/")
                               .replace("'", r"\'")
                               .replace(":", r"\:"))
        cmd_final += ["-vf", f"subtitles='{escaped_srt}'"]
        log("   📺 Субтитры вшиты в видео")

    cmd_final += [
        "-c:v", codec if not burn_subtitles and codec == "copy" else ("libx265" if codec == "libx265" else "libx264"),
        "-preset", preset,
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-shortest",
        out_path
    ]
    result = run(cmd_final, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise ProcessingError(f"ffmpeg (финал) ошибка:\n{result.stderr}")

    # Cleanup temp segments
    import shutil
    if os.path.isdir(tmp_dir):
        rmtree_safe(tmp_dir)
        log("🧹 Временные файлы удалены")

    if original_audio_mode == "full":
        log(f"   🔉 Оригинальное аудио подмешано (громкость: {int(original_audio_volume*100)}%)")
    elif original_audio_mode == "voiceover":
        log(f"   🔉 Закадровый перевод: фон {int(no_vocals_volume*100)}%, голос {int(vocals_volume*100)}%")
    elif original_audio_mode == "no_vocals":
        log(f"   🔉 Фон без голоса подмешан (громкость: {int(no_vocals_volume*100)}%)")
    log(f"✅ Готово! Файл: {out_path}")
