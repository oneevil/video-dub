#!/usr/bin/env python3
"""OmniVoice TTS persistent worker — runs in isolated venv with transformers>=5.3.
Loads model once, accepts commands via stdin (JSON lines), outputs via stdout.

Protocol (stdin -> stdout, one JSON per line):
  -> {"cmd":"generate","text":"...","index":1,"out_path":"...","ref_audio":"...","ref_text":"...","instruct":"...","seed":44}
  <- {"type":"segment","index":1}
  -> {"cmd":"quit"}
  <- {"type":"done","message":"..."}

Start:
  python omnivoice_worker.py --cache_dir ./models/tts [--model k2-fsa/OmniVoice]
"""
import argparse
import json
import os
import sys


def _stdin_lines():
    """Читает команды из stdin в фоне и отдаёт их главному циклу.

    Отдельный поток нужен, чтобы заметить смерть родителя (EOF) даже пока
    главный поток занят загрузкой модели: иначе воркер остаётся сиротой,
    держит модель в памяти и конкурирует за GPU. Работает на всех ОС —
    в отличие от поиска процессов через ps.
    """
    import queue as _q
    import threading as _th
    q: "_q.Queue" = _q.Queue()

    def _read():
        try:
            for line in sys.stdin:
                q.put(line)
        except Exception:
            pass
        os._exit(0)  # родитель закрыл канал — уходим немедленно

    _th.Thread(target=_read, daemon=True).start()
    while True:
        yield q.get()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="k2-fsa/OmniVoice")
    parser.add_argument("--cache_dir", default="")
    args = parser.parse_args()

    # HF_HOME должен быть выставлен ДО импорта huggingface_hub: он читает его
    # один раз при импорте, иначе модель ищется/качается в ~/.cache
    if args.cache_dir:
        os.environ["HF_HOME"] = args.cache_dir

    import time
    import torch
    import soundfile as sf
    from omnivoice import OmniVoice
    # Monkey-patch: disable forced fade+pad (library bug — applies even with postprocess_output=False)
    import omnivoice.models.omnivoice as _omni_mod
    _omni_mod.fade_and_pad_audio = lambda audio, **kw: audio

    # Device detection
    if torch.cuda.is_available():
        device = "cuda:0"
        dtype = torch.float16
    elif torch.backends.mps.is_available():
        device = "mps"
        dtype = torch.float16
    else:
        device = "cpu"
        dtype = torch.float32

    _out({"type": "log", "message": f"📦 Загружаю OmniVoice ({device})..."})
    _t0 = time.monotonic()
    # Веса на диске в float32. Просить device_map=mps вместе с dtype=float16
    # нельзя: каст выполняется поэлементно Metal-шейдером и занимает минуты
    # (copy_cast_kernel_mps). Кастуем на CPU, а на устройство переносим готовые
    # тензоры одним куском.
    model = OmniVoice.from_pretrained(args.model, dtype=dtype)
    if device != "cpu":
        # higgs-audio-tokenizer не работает на MPS — оставляем его на CPU
        _tok = model.audio_tokenizer
        model.audio_tokenizer = None
        model.to(device)
        model.audio_tokenizer = _tok
    _out({"type": "log", "message": f"   ✅ Модель загружена за {int(time.monotonic() - _t0)}с"})

    # Cache voice clone prompts
    _clone_cache = {}  # ref_audio -> VoiceClonePrompt

    def _get_clone_prompt(ref_audio, ref_text):
        key = f"{ref_audio}:{ref_text}"
        if key not in _clone_cache:
            _out({"type": "log", "message": "   🎙️ Подготавливаю голосовой клон..."})
            _clone_cache[key] = model.create_voice_clone_prompt(
                ref_audio=ref_audio, ref_text=ref_text or None
            )
            # Warmup with this voice
            _out({"type": "log", "message": "   🔥 Прогрев голоса..."})
            for _ in range(2):
                model.generate(text="Проверка голоса, с помощью этого текста я буду озвучивать ваши видео.",
                               voice_clone_prompt=_clone_cache[key], postprocess_output=True, denoise=False)
        return _clone_cache[key]

    _out({"type": "ready"})

    # Main loop — read commands from stdin
    for line in _stdin_lines():
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            continue

        if cmd.get("cmd") == "quit":
            _out({"type": "done", "message": "OmniVoice worker stopped"})
            break

        if cmd.get("cmd") == "warmup":
            # Warmup with selected voice at load time
            ref_audio = cmd.get("ref_audio", "")
            ref_text = cmd.get("ref_text", "")
            instruct = cmd.get("instruct", "")
            try:
                if ref_audio and os.path.exists(ref_audio):
                    _get_clone_prompt(ref_audio, ref_text)
                else:
                    _out({"type": "log", "message": "   🔥 Прогрев модели..."})
                    gen_kw = {"text": "Проверка голоса, с помощью этого текста я буду озвучивать ваши видео.",
                              "postprocess_output": True, "denoise": False}
                    if instruct:
                        gen_kw["instruct"] = instruct
                    for _ in range(2):
                        model.generate(**gen_kw)
                _out({"type": "warmed_up"})
            except Exception as e:
                _out({"type": "error", "message": f"warmup: {e}"})
            continue

        if cmd.get("cmd") == "generate":
            text = cmd.get("text", "")
            out_path = cmd.get("out_path", "")
            index = cmd.get("index", 0)
            ref_audio = cmd.get("ref_audio", "")
            ref_text = cmd.get("ref_text", "")
            instruct = cmd.get("instruct", "")
            seed = cmd.get("seed", -1)

            if not text or not out_path:
                _out({"type": "error", "message": "text and out_path required"})
                continue

            # Skip if already exists
            if os.path.exists(out_path):
                _out({"type": "segment", "index": index})
                continue

            try:
                # Set seed for deterministic generation
                if seed >= 0:
                    torch.manual_seed(seed)
                    if torch.cuda.is_available():
                        torch.cuda.manual_seed_all(seed)
                    if torch.backends.mps.is_available():
                        torch.mps.manual_seed(seed)

                gen_kwargs = {"text": text}
                if ref_audio and os.path.exists(ref_audio):
                    gen_kwargs["voice_clone_prompt"] = _get_clone_prompt(ref_audio, ref_text)
                elif instruct:
                    gen_kwargs["instruct"] = instruct

                audio = model.generate(**gen_kwargs, postprocess_output=True, denoise=False)
                # omnivoice 0.2+ возвращает np.ndarray, ранние версии — torch.Tensor
                out = audio[0]
                wav = torch.as_tensor(out.cpu() if hasattr(out, "cpu") else out).squeeze()
                # Trim leading artifact — cut first 200ms then find speech onset
                sr = int(getattr(model, "sampling_rate", 0) or 24000)
                trim_200 = int(0.2 * sr)
                if wav.shape[0] > trim_200 + sr:
                    # Compute RMS energy in 20ms windows
                    win = int(0.02 * sr)  # 480 samples
                    search = wav[:trim_200]
                    best_cut = 0
                    min_energy = float('inf')
                    for pos in range(0, len(search) - win, win):
                        rms = (search[pos:pos+win] ** 2).mean().sqrt().item()
                        if rms < min_energy:
                            min_energy = rms
                            best_cut = pos + win
                    wav = wav[best_cut:]
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                sf.write(out_path, wav.numpy(), sr)
                _out({"type": "segment", "index": index})
            except Exception as e:
                _out({"type": "error", "message": f"seg {index}: {e}"})


def _out(msg):
    print(json.dumps(msg, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
