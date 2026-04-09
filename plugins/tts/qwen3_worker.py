#!/usr/bin/env python3
"""Qwen3-TTS persistent worker — runs in isolated venv.
Loads model once, accepts commands via stdin (JSON lines), outputs via stdout.

Protocol:
  -> {"cmd":"load","model":"Qwen/...","seed":44,"temperature":0.7}
  <- {"type":"ready"}
  -> {"cmd":"clone","voice_wav":"...","voice_text":"...","seed":44,"temperature":0.7}
  <- {"type":"clone_ready"}
  -> {"cmd":"generate","text":"...","index":1,"out_path":"...","seed":44,"temperature":0.7,"speaker":"Vivian"}
  <- {"type":"segment","index":1}
  -> {"cmd":"reset_clone"}
  <- {"type":"done"}
  -> {"cmd":"quit"}
"""
import argparse
import json
import os
import sys
import io


def _out(msg):
    print(json.dumps(msg, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache_dir", default="")
    args = parser.parse_args()

    if args.cache_dir:
        os.environ["HF_HOME"] = args.cache_dir

    import torch
    import numpy as np
    import soundfile as sf

    # Suppress qwen_tts import noise
    _real_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        from qwen_tts import Qwen3TTSModel
    finally:
        sys.stdout = _real_stdout

    if torch.cuda.is_available():
        device = "cuda:0"
        dtype = torch.float16
    elif torch.backends.mps.is_available():
        device = "mps"
        dtype = torch.bfloat16
    else:
        device = "cpu"
        dtype = torch.float32

    tts_model = None
    current_model_id = None
    clone_prompt = None
    clone_key = None
    is_custom = False

    WARMUP_TEXT = "Проверка голоса, с помощью этого текста я буду озвучивать ваши видео."

    def _set_seed(seed):
        if seed >= 0:
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)
            if torch.backends.mps.is_available():
                torch.mps.manual_seed(seed)

    _out({"type": "log", "message": f"📦 Qwen3 worker запущен ({device})"})
    _out({"type": "ready"})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            continue

        action = cmd.get("cmd", "")

        if action == "quit":
            _out({"type": "done", "message": "Qwen3 worker stopped"})
            break

        elif action == "load":
            nonlocal_model = cmd.get("model", "")
            if nonlocal_model == current_model_id and tts_model is not None:
                _out({"type": "log", "message": f"   📦 Модель {nonlocal_model.split('/')[-1]} (кэш, {device})"})
            else:
                _out({"type": "log", "message": f"   📦 Загружаю модель {nonlocal_model.split('/')[-1]} ({device})..."})
                tts_model = Qwen3TTSModel.from_pretrained(nonlocal_model, device_map=device, dtype=dtype)
                current_model_id = nonlocal_model
                clone_prompt = None
                clone_key = None
                is_custom = "CustomVoice" in nonlocal_model

                # Warmup
                if is_custom:
                    _out({"type": "log", "message": "   🔥 Прогрев модели..."})
                    for _ in range(2):
                        _set_seed(cmd.get("seed", 44))
                        speaker = cmd.get("speaker", "Vivian")
                        tts_model.generate_custom_voice(text=WARMUP_TEXT, language="Auto",
                                                        speaker=speaker, temperature=cmd.get("temperature", 0.7))
            _out({"type": "loaded"})

        elif action == "clone":
            voice_wav = cmd.get("voice_wav", "")
            voice_text = cmd.get("voice_text", "")
            new_key = f"{voice_wav}:{voice_text}"
            if clone_key == new_key and clone_prompt is not None:
                _out({"type": "log", "message": "   🎙️ Голосовой клон (кэш)"})
            else:
                _out({"type": "log", "message": "   🎙️ Подготавливаю голосовой клон..."})
                # Pad reference with silence
                padded_ref = voice_wav
                try:
                    ref_data, ref_sr = sf.read(voice_wav)
                    silence = np.zeros(int(ref_sr * 0.5)) if ref_data.ndim == 1 else np.zeros((int(ref_sr * 0.5), ref_data.shape[1]))
                    padded = np.concatenate([ref_data, silence])
                    padded_ref = voice_wav + ".padded.wav"
                    sf.write(padded_ref, padded, ref_sr)
                except Exception:
                    padded_ref = voice_wav

                clone_prompt = tts_model.create_voice_clone_prompt(
                    ref_audio=padded_ref, ref_text=voice_text or "")
                clone_key = new_key

                # Warmup
                _out({"type": "log", "message": "   🔥 Прогрев голоса..."})
                for _ in range(2):
                    _set_seed(cmd.get("seed", 44))
                    tts_model.generate_voice_clone(
                        text=WARMUP_TEXT, language="Auto",
                        voice_clone_prompt=clone_prompt,
                        temperature=cmd.get("temperature", 0.7))
            _out({"type": "clone_ready"})

        elif action == "reset_clone":
            clone_prompt = None
            clone_key = None
            _out({"type": "done"})

        elif action == "generate":
            text = cmd.get("text", "")
            out_path = cmd.get("out_path", "")
            index = cmd.get("index", 0)
            seed = cmd.get("seed", -1)
            temperature = cmd.get("temperature", 0.7)
            speaker = cmd.get("speaker", "Vivian")

            if not text or not out_path:
                _out({"type": "error", "message": "text and out_path required"})
                continue

            if os.path.exists(out_path):
                _out({"type": "segment", "index": index})
                continue

            try:
                _set_seed(seed)

                if is_custom:
                    wavs, sr = tts_model.generate_custom_voice(
                        text=text, language="Auto", speaker=speaker, temperature=temperature)
                elif clone_prompt:
                    wavs, sr = tts_model.generate_voice_clone(
                        text=text, language="Auto",
                        voice_clone_prompt=clone_prompt, temperature=temperature)
                else:
                    _out({"type": "error", "message": "Base модель требует клонированный голос"})
                    continue

                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                sf.write(out_path, wavs[0], sr)
                _out({"type": "segment", "index": index})
            except Exception as e:
                _out({"type": "error", "message": f"seg {index}: {e}"})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False), flush=True)
        sys.exit(1)
