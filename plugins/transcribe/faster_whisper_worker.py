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
    parser.add_argument("--gpu_name", default="")
    args = parser.parse_args()

    from faster_whisper import WhisperModel

    os.makedirs(args.cache_dir, exist_ok=True)

    # Get duration for progress
    dur_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", args.audio]
    r = subprocess.run(dur_cmd, capture_output=True, text=True, encoding="utf-8")
    total_duration = 1.0
    if r.returncode == 0:
        d = json.loads(r.stdout).get("format", {}).get("duration")
        if d:
            total_duration = max(float(d), 1.0)

    opts = {"task": "transcribe", "vad_filter": True, "word_timestamps": True}
    if args.language:
        opts["language"] = args.language

    state = {"device": "", "emitted": 0}

    def run(device):
        model = WhisperModel(args.model, download_root=args.cache_dir,
                             device=device, compute_type="int8")
        # Реальное устройство, а не запрошенное: при device="auto" узнать его
        # иначе нельзя, а разница между GPU и CPU — это минуты против часа
        state["device"] = getattr(getattr(model, "model", None), "device", device)
        where = state["device"]
        if where == "cuda" and args.gpu_name:
            where += f" — {args.gpu_name}"
        _out({"type": "log", "message": f"   Устройство: {where} (int8, оптимизированный)"})

        segments_iter, _info = model.transcribe(args.audio, **opts)
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
            state["emitted"] += 1
            pct = min(100, int(seg.end / total_duration * 100))
            if pct >= last_pct + 5:
                last_pct = pct
                _out({"type": "log", "message": f"   Транскрипция: {pct}%"})
        return subtitles

    try:
        subtitles = run("auto")
    except Exception as e:
        # CUDA у CTranslate2 отваливается на первом вычислении, уже после
        # создания модели, — поэтому ловим здесь, а не вокруг WhisperModel.
        # Повторяем, только если работали на GPU и ещё ничего не отдали:
        # иначе сегменты задвоятся в списке.
        if state["device"] == "cpu" or state["emitted"]:
            raise
        _out({"type": "log", "message": f"   ⚠️ GPU недоступен ({e}); повторяю на процессоре"})
        subtitles = run("cpu")

    _out({"type": "log", "message": f"✅ Транскрипция готова: {len(subtitles)} фраз"})
    _out({"type": "result", "subtitles": subtitles})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False), flush=True)
        sys.exit(1)
