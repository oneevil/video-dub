#!/usr/bin/env python3
"""OpenAI Whisper worker — runs in isolated venv.
Output: JSON lines to stdout (type: log/segment/result/error).
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
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--cache_dir", default="")
    parser.add_argument("--language", default="")
    args = parser.parse_args()

    import torch
    import whisper
    import subprocess

    if torch.backends.mps.is_available():
        device = "cpu"
        _out({"type": "log", "message": "   Устройство: Apple Silicon (CPU, fp32)"})
    elif torch.cuda.is_available():
        device = "cuda"
        _out({"type": "log", "message": "   Устройство: CUDA GPU"})
    else:
        device = "cpu"

    os.makedirs(args.cache_dir, exist_ok=True)
    model = whisper.load_model(args.model, download_root=args.cache_dir, device=device)

    # Get audio duration for progress
    dur_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", args.audio]
    r = subprocess.run(dur_cmd, capture_output=True, text=True, encoding="utf-8")
    total_duration = 1.0
    if r.returncode == 0:
        d = json.loads(r.stdout).get("format", {}).get("duration")
        if d:
            total_duration = max(float(d), 1.0)

    _orig_decode = model.decode
    _state = {"offset": 0.0, "last_pct": -1}

    def _decode_with_progress(mel, options):
        result = _orig_decode(mel, options)
        _state["offset"] += 30.0
        pct = min(100, int(_state["offset"] / total_duration * 100))
        if pct >= _state["last_pct"] + 5:
            _state["last_pct"] = pct
            _out({"type": "log", "message": f"   Транскрипция: {pct}%"})
        return result

    model.decode = _decode_with_progress

    opts = {
        "task": "transcribe",
        "verbose": False,
        "word_timestamps": True,
        "condition_on_previous_text": True,
        "fp16": device == "cuda",
    }
    if args.language:
        opts["language"] = args.language

    result = model.transcribe(args.audio, **opts)
    model.decode = _orig_decode

    subtitles = []
    for i, seg in enumerate(result["segments"], 1):
        subtitles.append({
            "index": i,
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
        })

    _out({"type": "log", "message": f"✅ Транскрипция готова: {len(subtitles)} фраз"})
    _out({"type": "result", "subtitles": subtitles})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False), flush=True)
        sys.exit(1)
