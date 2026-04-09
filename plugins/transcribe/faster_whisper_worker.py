#!/usr/bin/env python3
"""Faster Whisper worker — runs in isolated venv.
Output: JSON lines to stdout (type: log/segment/result/error).
"""
import argparse
import json
import os
import sys
import subprocess


def _out(msg):
    print(json.dumps(msg, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--cache_dir", default="")
    parser.add_argument("--language", default="")
    args = parser.parse_args()

    from faster_whisper import WhisperModel

    os.makedirs(args.cache_dir, exist_ok=True)
    model = WhisperModel(args.model, download_root=args.cache_dir,
                         device="auto", compute_type="int8")
    _out({"type": "log", "message": "   Устройство: CTranslate2 (int8, оптимизированный)"})

    # Get duration for progress
    dur_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", args.audio]
    r = subprocess.run(dur_cmd, capture_output=True, text=True)
    total_duration = 1.0
    if r.returncode == 0:
        d = json.loads(r.stdout).get("format", {}).get("duration")
        if d:
            total_duration = max(float(d), 1.0)

    opts = {"task": "transcribe", "vad_filter": True, "word_timestamps": True}
    if args.language:
        opts["language"] = args.language
    segments_iter, info = model.transcribe(args.audio, **opts)

    subtitles = []
    last_pct = -1
    for i, seg in enumerate(segments_iter, 1):
        sub = {
            "index": i,
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        }
        subtitles.append(sub)
        # Emit each segment for real-time display
        _out({"type": "segment", "sub": sub})
        pct = min(100, int(seg.end / total_duration * 100))
        if pct >= last_pct + 5:
            last_pct = pct
            _out({"type": "log", "message": f"   Транскрипция: {pct}%"})

    _out({"type": "log", "message": f"✅ Транскрипция готова: {len(subtitles)} фраз"})
    _out({"type": "result", "subtitles": subtitles})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False), flush=True)
        sys.exit(1)
