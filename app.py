#!/usr/bin/env python3
"""Video Translator — Flask web app."""

import json as json_mod
import os
import queue
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file

from pipeline import (
    LANGUAGES,
    WHISPER_MODELS,
    build_final_video,
    check_dependencies,
    download_video,
    extract_audio,
    parse_srt,
    synthesize_speech,
    transcribe_audio,
    translate_subtitles,
    write_srt,
)

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DEFAULT_WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")
DEFAULT_OUTPUT_DIR = os.path.expanduser(
    os.environ.get("OUTPUT_DIR", str(Path.home() / "translated_videos"))
)

app = Flask(__name__)

# ── Job storage ──────────────────────────────────────────────────────────────

jobs: dict[str, "Job"] = {}


class Job:
    def __init__(self, url: str, language: str, whisper_model: str):
        self.id = uuid.uuid4().hex[:8]
        self.url = url
        self.language = language
        self.whisper_model = whisper_model
        self.output_dir = DEFAULT_OUTPUT_DIR
        self.messages: queue.Queue[dict] = queue.Queue()
        self.subtitles: list[dict] | None = None
        self.translated: list[dict] | None = None
        self.source_video: str | None = None
        self.work_dir: str | None = None
        self.result_path: str | None = None
        self.state = "starting"
        self.resume_event = threading.Event()
        # Skip flags
        self.skip_download = False
        self.skip_transcribe = False
        self.skip_translate = False


# ── Routes ───────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template(
        "index.html",
        languages=LANGUAGES,
        whisper_models=WHISPER_MODELS,
        default_whisper=DEFAULT_WHISPER_MODEL,
        has_api_key=bool(ANTHROPIC_API_KEY),
    )


@app.route("/start", methods=["POST"])
def start():
    data = request.json
    url = (data.get("url") or "").strip()
    skip_download = data.get("skip_download", False)
    skip_transcribe = data.get("skip_transcribe", False)
    skip_translate = data.get("skip_translate", False)

    if not url and not skip_download:
        return jsonify(error="URL обязателен (или пропустите шаг скачивания)"), 400

    api_key = ANTHROPIC_API_KEY
    if not api_key and not skip_translate:
        return jsonify(error="ANTHROPIC_API_KEY не задан в .env"), 400

    language = data.get("language", "Russian")
    whisper_model = data.get("whisper_model", DEFAULT_WHISPER_MODEL)

    job = Job(url, language, whisper_model)
    job.skip_download = skip_download
    job.skip_transcribe = skip_transcribe
    job.skip_translate = skip_translate

    # Pre-loaded subtitles from upload
    if data.get("original_subs"):
        job.subtitles = data["original_subs"]
    if data.get("translated_subs"):
        job.translated = data["translated_subs"]

    # Resume: use existing work_dir
    if data.get("work_dir"):
        job.work_dir = data["work_dir"]

    jobs[job.id] = job

    thread = threading.Thread(target=_run_pipeline, args=(job, api_key), daemon=True)
    thread.start()

    return jsonify(job_id=job.id)


@app.route("/progress/<job_id>")
def progress(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return "Not found", 404

    def stream():
        while True:
            try:
                msg = job.messages.get(timeout=30)
            except queue.Empty:
                yield "event: ping\ndata: {}\n\n"
                continue
            yield f"event: {msg['event']}\ndata: {json_mod.dumps(msg['data'], ensure_ascii=False)}\n\n"
            if msg["event"] in ("done", "error"):
                break

    return Response(stream(), mimetype="text/event-stream")


@app.route("/subtitles/<job_id>")
def get_subtitles(job_id: str):
    job = jobs.get(job_id)
    if not job or not job.translated:
        return jsonify(error="Субтитры не готовы"), 404
    return jsonify(subtitles=job.translated)


@app.route("/subtitles/<job_id>", methods=["POST"])
def save_subtitles(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify(error="Job не найден"), 404
    data = request.json
    job.translated = data.get("subtitles", job.translated)
    if job.work_dir:
        write_srt(job.translated, os.path.join(job.work_dir, "translated.srt"))
    return jsonify(ok=True)


@app.route("/continue/<job_id>", methods=["POST"])
def continue_pipeline(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify(error="Job не найден"), 404
    data = request.json or {}
    if "subtitles" in data:
        job.translated = data["subtitles"]
        if job.work_dir:
            write_srt(job.translated, os.path.join(job.work_dir, "translated.srt"))
    job.resume_event.set()
    return jsonify(ok=True)


@app.route("/video/<job_id>")
def serve_video(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return "Not found", 404
    path = job.result_path or job.source_video
    if not path or not os.path.exists(path):
        return "Not found", 404
    return send_file(path, mimetype="video/mp4")


@app.route("/source/<job_id>")
def serve_source(job_id: str):
    job = jobs.get(job_id)
    if not job or not job.source_video or not os.path.exists(job.source_video):
        return "Not found", 404
    return send_file(job.source_video, mimetype="video/mp4")


@app.route("/upload-srt", methods=["POST"])
def upload_srt():
    """Parse uploaded SRT file, return subtitle list as JSON."""
    f = request.files.get("file")
    if not f:
        return jsonify(error="Файл не выбран"), 400
    text = f.read().decode("utf-8", errors="replace")
    subs = parse_srt(text)
    if not subs:
        return jsonify(error="Не удалось распарсить SRT"), 400
    return jsonify(subtitles=subs)


@app.route("/past-jobs")
def past_jobs():
    """List existing job folders for resume."""
    out = DEFAULT_OUTPUT_DIR
    if not os.path.isdir(out):
        return jsonify(jobs=[])
    result = []
    for name in sorted(os.listdir(out), reverse=True):
        d = os.path.join(out, name)
        if not os.path.isdir(d) or not name.startswith("job_"):
            continue
        info = {"name": name, "path": d, "files": {}}
        for fname in ("source.mp4", "audio.wav", "original.srt", "translated.srt"):
            if os.path.exists(os.path.join(d, fname)):
                info["files"][fname] = True
        # Check for any source video
        for src in Path(d).glob("source.*"):
            if src.suffix in (".mp4", ".mkv", ".webm", ".avi", ".mov"):
                info["files"]["source_video"] = src.name
                break
        if info["files"]:
            result.append(info)
    return jsonify(jobs=result[:20])


@app.route("/resume-job", methods=["POST"])
def resume_job():
    """Load data from an existing job folder to resume."""
    data = request.json
    work_dir = data.get("path", "")
    if not os.path.isdir(work_dir):
        return jsonify(error="Папка не найдена"), 404
    result = {"work_dir": work_dir, "files": {}}
    # Load subtitles if exist
    orig_srt = os.path.join(work_dir, "original.srt")
    if os.path.exists(orig_srt):
        with open(orig_srt, encoding="utf-8") as f:
            result["original_subs"] = parse_srt(f.read())
    trans_srt = os.path.join(work_dir, "translated.srt")
    if os.path.exists(trans_srt):
        with open(trans_srt, encoding="utf-8") as f:
            result["translated_subs"] = parse_srt(f.read())
    # Find source video
    for src in Path(work_dir).glob("source.*"):
        if src.suffix in (".mp4", ".mkv", ".webm", ".avi", ".mov"):
            result["source_video"] = str(src)
            break
    return jsonify(**result)


@app.route("/job-video")
def serve_job_video():
    """Serve video from an arbitrary path (for resume preview)."""
    path = request.args.get("path", "")
    if not path or not os.path.exists(path):
        return "Not found", 404
    # Security: only serve from output dir
    real = os.path.realpath(path)
    allowed = os.path.realpath(DEFAULT_OUTPUT_DIR)
    if not real.startswith(allowed):
        return "Forbidden", 403
    return send_file(real, mimetype="video/mp4")


# ── Pipeline ─────────────────────────────────────────────────────────────────


def _emit(job: Job, event: str, **data):
    job.messages.put({"event": event, "data": data})


def _run_pipeline(job: Job, api_key: str):
    try:
        os.makedirs(job.output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Reuse existing work_dir or create new
        if not job.work_dir:
            job.work_dir = os.path.join(job.output_dir, f"job_{timestamp}")
        os.makedirs(job.work_dir, exist_ok=True)

        def log(msg):
            _emit(job, "log", message=msg)

        # 1. Dependencies
        check_dependencies(log)

        # ── Download ──
        if job.skip_download:
            _emit(job, "step", key="download", state="done")
            log("Скачивание пропущено")
            # Try to find source video in work_dir
            if not job.source_video:
                for src in Path(job.work_dir).glob("source.*"):
                    if src.suffix in (".mp4", ".mkv", ".webm", ".avi", ".mov"):
                        job.source_video = str(src)
                        break
            if job.source_video:
                _emit(job, "source_ready")
        else:
            _emit(job, "step", key="download", state="active")
            url = job.url
            if url.startswith("http://") or url.startswith("https://"):
                video_path = download_video(url, job.work_dir, log)
            else:
                ext = Path(url).suffix
                video_path = os.path.join(job.work_dir, f"source{ext}")
                if not os.path.exists(video_path):
                    shutil.copy2(url, video_path)
                log(f"Файл: {Path(video_path).name}")
            job.source_video = video_path
            _emit(job, "step", key="download", state="done")
            _emit(job, "source_ready")

        # ── Transcribe ──
        if job.skip_transcribe:
            _emit(job, "step", key="transcribe", state="done")
            log("Транскрипция пропущена")
            # Load from file if not already set
            if not job.subtitles:
                orig_srt = os.path.join(job.work_dir, "original.srt")
                if os.path.exists(orig_srt):
                    with open(orig_srt, encoding="utf-8") as f:
                        job.subtitles = parse_srt(f.read())
                    log(f"Загружены субтитры из original.srt: {len(job.subtitles)} фраз")
        else:
            if not job.source_video:
                raise Exception("Нет исходного видео для транскрипции")
            _emit(job, "step", key="transcribe", state="active")
            audio_path = extract_audio(job.source_video, job.work_dir, log)
            job.subtitles = transcribe_audio(audio_path, job.work_dir, job.whisper_model, log)
            _emit(job, "step", key="transcribe", state="done")

        # ── Translate ──
        if job.skip_translate:
            _emit(job, "step", key="translate", state="done")
            log("Перевод пропущен")
            # Load from file if not already set
            if not job.translated:
                trans_srt = os.path.join(job.work_dir, "translated.srt")
                if os.path.exists(trans_srt):
                    with open(trans_srt, encoding="utf-8") as f:
                        job.translated = parse_srt(f.read())
                    log(f"Загружены субтитры из translated.srt: {len(job.translated)} фраз")
                elif job.subtitles:
                    # Use original as translated (user can edit)
                    job.translated = [s.copy() for s in job.subtitles]
                    log("Используются оригинальные субтитры (можно отредактировать)")
        else:
            if not job.subtitles:
                raise Exception("Нет субтитров для перевода")
            _emit(job, "step", key="translate", state="active")
            job.translated = translate_subtitles(
                job.subtitles, job.language, api_key, job.work_dir, log
            )
            _emit(job, "step", key="translate", state="done")

        # --- Pause: let user edit subtitles ---
        if job.translated:
            _emit(job, "subtitles_ready")
            log("Субтитры готовы к редактированию. Нажмите «Продолжить».")
            job.state = "waiting_edit"
            job.resume_event.wait()
        else:
            raise Exception("Нет субтитров для синтеза речи")

        # ── TTS ──
        _emit(job, "step", key="tts", state="active")
        subs_with_audio = synthesize_speech(job.translated, job.work_dir, log)
        _emit(job, "step", key="tts", state="done")

        # ── Build ──
        if not job.source_video:
            raise Exception("Нет исходного видео для сборки")
        _emit(job, "step", key="build", state="active")
        lang_lower = job.language.lower()
        out_name = f"translated_{lang_lower}_{timestamp}.mp4"
        job.result_path = os.path.join(job.output_dir, out_name)
        build_final_video(job.source_video, subs_with_audio, job.result_path, job.work_dir, log)
        _emit(job, "step", key="build", state="done")

        job.state = "done"
        _emit(job, "done", path=job.result_path)

    except Exception as e:
        job.state = "error"
        _emit(job, "error", message=str(e))


# ── Entry point ──────────────────────────────────────────────────────────────


def main():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
