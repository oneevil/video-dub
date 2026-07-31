#!/usr/bin/env python3
"""WhisperX worker — runs in isolated venv.
Transcribes audio with WhisperX + alignment + diarization.

Output: JSON lines to stdout (type: log/segment/result/error).

Usage:
  python whisperx_worker.py --audio /path/to.wav --out_dir /path/ --model large-v3 \
    --cache_dir ./models/whisper/whisperx [--asr_cache_dir ./models/whisper/faster-whisper] \
    [--language en] [--num_speakers 2] [--hf_token ...]
"""
import argparse
import json
import os
import sys


def _out(msg):
    print(json.dumps(msg, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--out_dir", required=True)
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--cache_dir", default="")
    # Модели распознавания общие с Faster Whisper — они лежат отдельно от
    # моделей выравнивания и диаризации
    parser.add_argument("--asr_cache_dir", default="")
    parser.add_argument("--language", default="")
    parser.add_argument("--num_speakers", type=int, default=0)
    parser.add_argument("--hf_token", default="")
    args = parser.parse_args()

    import warnings
    import logging
    warnings.filterwarnings("ignore")
    for name in ("pyannote", "pytorch_lightning", "lightning", "lightning_fabric",
                 "lightning.pytorch", "lightning.pytorch.utilities",
                 "lightning.pytorch.utilities.migration",
                 "lightning.pytorch.utilities.upgrade_checkpoint"):
        logging.getLogger(name).setLevel(logging.CRITICAL)

    import io
    import torch
    import whisperx

    if torch.cuda.is_available():
        device = "cuda"
        try:
            card = torch.cuda.get_device_name(0)
        except Exception:
            card = ""
        _out({"type": "log", "message": "   Устройство: cuda" + (f" — {card}" if card else "")})
    else:
        device = "cpu"
        _out({"type": "log", "message": "   Устройство: CPU"})

    cache_dir = args.cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    asr_cache_dir = args.asr_cache_dir or cache_dir
    os.makedirs(asr_cache_dir, exist_ok=True)

    # 1. Transcribe
    _out({"type": "log", "message": "   📝 Транскрипция..."})
    _real_stdout, _real_stderr = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    _prev_level = logging.root.level
    logging.root.setLevel(logging.CRITICAL)
    try:
        model = whisperx.load_model(args.model, device, compute_type="int8", download_root=asr_cache_dir)
    finally:
        sys.stdout = _real_stdout
        sys.stderr = _real_stderr
        logging.root.setLevel(_prev_level)

    result = model.transcribe(args.audio, language=args.language or None)

    # 2. Align
    _out({"type": "log", "message": "   🔗 Выравнивание слов..."})
    sys.stdout = io.StringIO()
    try:
        model_a, metadata = whisperx.load_align_model(
            language_code=result["language"], device=device, model_dir=cache_dir)
        result = whisperx.align(result["segments"], model_a, metadata, args.audio, device)
    except Exception:
        sys.stdout = _real_stdout
        _out({"type": "log", "message": f"   ⚠️ Выравнивание недоступно для языка '{result.get('language', '?')}', пропускаю"})
    finally:
        sys.stdout = _real_stdout

    # 3. Diarize
    _out({"type": "log", "message": "   👥 Диаризация спикеров..."})
    hf_token = args.hf_token or os.environ.get("HF_TOKEN", "")
    if not hf_token:
        _out({"type": "log", "message": "   ⚠️ HF_TOKEN не задан — диаризация может не работать"})

    from whisperx.diarize import DiarizationPipeline
    sys.stdout = io.StringIO()
    try:
        diarize_model = DiarizationPipeline(token=hf_token, device=device, cache_dir=cache_dir)
    finally:
        sys.stdout = _real_stdout

    diarize_kwargs = {}
    if args.num_speakers > 0:
        diarize_kwargs["min_speakers"] = args.num_speakers
        diarize_kwargs["max_speakers"] = args.num_speakers
    diarize_segments = diarize_model(args.audio, **diarize_kwargs)
    result = whisperx.assign_word_speakers(diarize_segments, result)

    # 4. Build subtitles
    subtitles = []
    speakers_found = set()
    for i, seg in enumerate(result["segments"], 1):
        speaker = seg.get("speaker", "")
        if speaker:
            speakers_found.add(speaker)
        sub = {
            "index": i,
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
            "speaker": speaker,
        }
        subtitles.append(sub)

    _out({"type": "log", "message": f"   👥 Найдено спикеров: {len(speakers_found)} ({', '.join(sorted(speakers_found))})"})
    _out({"type": "log", "message": f"✅ Транскрипция готова: {len(subtitles)} фраз"})

    # 5. Output result
    _out({"type": "result", "subtitles": subtitles})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False), flush=True)
        sys.exit(1)
