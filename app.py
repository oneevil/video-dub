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
    BUILD_AUDIO_BITRATES,
    BUILD_CODECS,
    BUILD_FORMATS,
    BUILD_PRESETS,
    LANGUAGES,
    SOURCE_LANGUAGES,
    TRANSCRIBE_ENGINES,
    TRANSLATE_ENGINES,
    TRANSLATE_PROVIDERS,
    TTS_ENGINES,
    VOICES_DIR,
    WHISPER_MODELS,
    get_voices,
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
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
CUSTOM_API_KEY = os.environ.get("CUSTOM_API_KEY", "")
CUSTOM_API_URL = os.environ.get("CUSTOM_API_URL", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
TRANSLATE_PROVIDER = os.environ.get("TRANSLATE_PROVIDER", "claude")
TRANSLATE_MODEL = os.environ.get("TRANSLATE_MODEL", "claude-haiku-4-5")
DEFAULT_TRANSCRIBE_ENGINE = os.environ.get("TRANSCRIBE_ENGINE", "openai-whisper")
DEFAULT_WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3")
DEFAULT_TTS_ENGINE = os.environ.get("TTS_ENGINE", "qwen3-1.7b-base")
DEFAULT_TTS_VOICE = os.environ.get("TTS_VOICE", "")
DEFAULT_TTS_SEED = int(os.environ.get("TTS_SEED", "44"))
DEFAULT_TTS_TEMPERATURE = float(os.environ.get("TTS_TEMPERATURE", "0.7"))
DEFAULT_OUTPUT_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("OUTPUT_DIR", str(Path.home() / "projects"))
))
DEFAULT_SEPARATE_VOCALS = os.environ.get("SEPARATE_VOCALS", "true").lower() == "true"
DEFAULT_BUILD_FORMAT = os.environ.get("BUILD_FORMAT", "mp4")
DEFAULT_BUILD_CODEC = os.environ.get("BUILD_CODEC", "copy")
DEFAULT_BUILD_PRESET = os.environ.get("BUILD_PRESET", "medium")
DEFAULT_BUILD_AUDIO_BITRATE = os.environ.get("BUILD_AUDIO_BITRATE", "128k")
DEFAULT_BUILD_MAX_SLOWDOWN = os.environ.get("BUILD_MAX_SLOWDOWN", "3.0")
DEFAULT_BUILD_ORIGINAL_AUDIO = os.environ.get("BUILD_ORIGINAL_AUDIO", "no_vocals")
DEFAULT_BUILD_ORIGINAL_VOLUME = os.environ.get("BUILD_ORIGINAL_VOLUME", "10")
DEFAULT_BUILD_NO_VOCALS_VOLUME = os.environ.get("BUILD_NO_VOCALS_VOLUME", "50")
DEFAULT_BUILD_VOCALS_VOLUME = os.environ.get("BUILD_VOCALS_VOLUME", "15")
DEFAULT_BUILD_BURN_SUBS = os.environ.get("BUILD_BURN_SUBS", "false").lower() == "true"

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

app = Flask(__name__, template_folder="app", static_folder="app", static_url_path="/static")

# ── Job storage ──────────────────────────────────────────────────────────────

jobs: dict[str, "Job"] = {}


class Job:
    def __init__(self, url: str, language: str, whisper_model: str,
                 source_language: str = "", transcribe_engine: str = "openai-whisper"):
        self.id = uuid.uuid4().hex[:8]
        self.url = url
        self.language = language
        self.source_language = source_language
        self.whisper_model = whisper_model
        self.transcribe_engine = transcribe_engine
        self.output_dir = DEFAULT_OUTPUT_DIR
        self.messages: queue.Queue[dict] = queue.Queue()
        self.subtitles: list[dict] | None = None
        self.translated: list[dict] | None = None
        self.source_video: str | None = None
        self.work_dir: str | None = None
        self.result_path: str | None = None
        self.project_name = ""
        self.state = "starting"
        self.resume_event = threading.Event()
        # Skip flags
        self.skip_transcribe = False
        self.skip_translate = False
        self.skip_tts = False
        self.skip_build = False
        self.separate_vocals = False
        # Translation settings
        self.translate_provider = TRANSLATE_PROVIDER
        self.translate_model = TRANSLATE_MODEL
        self.translate_base_url = ""
        # TTS settings
        self.tts_engine = "qwen3-tts"
        self.tts_voice = ""
        self.tts_voice_wav = ""
        self.tts_voice_text = ""
        self.tts_seed = DEFAULT_TTS_SEED
        self.tts_temperature = DEFAULT_TTS_TEMPERATURE
        self.num_speakers = 0
        self.speaker_voice_map = None
        # Build settings
        self.build_format = "mp4"
        self.build_codec = "libx264"
        self.build_preset = "fast"
        self.build_audio_bitrate = "128k"
        self.build_max_slowdown = 3.0
        self.build_original_audio = "none"  # "none", "full", "no_vocals", "voiceover"
        self.build_no_vocals_volume = 0.5
        self.build_vocals_volume = 0.15
        self.build_original_volume = 0.1
        self.build_burn_subs = False
        self.build_start_sec = 0
        self.build_end_sec = 0  # 0 = до конца


# ── Routes ───────────────────────────────────────────────────────────────────


def _get_tts_download_engines():
    """Get downloadable TTS model options from plugins."""
    try:
        from plugins.tts import get_download_engines
        return get_download_engines()
    except Exception:
        return []


def _get_translate_models_json():
    """Get model lists from translate plugins as JSON string."""
    import json
    result = {}
    try:
        from plugins.translate import discover_plugins as _dt
        _, plugins = _dt()
        seen = set()
        for eid, mod in plugins.items():
            if mod in seen:
                continue
            seen.add(mod)
            if hasattr(mod, 'MODELS'):
                result[eid] = mod.MODELS
    except Exception:
        pass
    return json.dumps(result, ensure_ascii=False)


def _get_transcribe_download_engines():
    """Get downloadable transcription model options from plugins."""
    try:
        from plugins.transcribe import get_download_engines
        return get_download_engines()
    except Exception:
        return []


def _find_download_plugin(engine):
    """Find plugin module that handles download for given engine value."""
    # Search across all plugin types
    for discover_fn in (_get_all_download_plugins,):
        plugin = discover_fn(engine)
        if plugin:
            return plugin
    return None


def _get_all_download_plugins(engine):
    """Search all plugin systems for a download handler matching engine."""
    # TTS plugins
    try:
        from plugins.tts import discover_plugins as _d
        _, plugins = _d()
        for mod in set(plugins.values()):
            if hasattr(mod, 'DOWNLOAD_ENGINES'):
                if any(d['value'] == engine for d in mod.DOWNLOAD_ENGINES):
                    return mod
    except Exception:
        pass
    # Transcribe plugins
    try:
        from plugins.transcribe import discover_plugins as _d
        _, plugins = _d()
        for mod in set(plugins.values()):
            if hasattr(mod, 'DOWNLOAD_ENGINES'):
                if any(d['value'] == engine for d in mod.DOWNLOAD_ENGINES):
                    return mod
    except Exception:
        pass
    return None


@app.route("/")
def index():
    # Collect past jobs for server-side rendering
    past_jobs = []
    if os.path.isdir(DEFAULT_OUTPUT_DIR):
        for name in sorted(os.listdir(DEFAULT_OUTPUT_DIR), reverse=True):
            d = os.path.join(DEFAULT_OUTPUT_DIR, name)
            if not os.path.isdir(d) or not name.startswith("job_"):
                continue
            info = {"name": name, "path": d, "title": "",
                    "has_video": False, "has_srt": False, "has_trans": False}
            # Read meta.json for title
            meta_path = os.path.join(d, "meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, encoding="utf-8") as mf:
                        meta = json_mod.loads(mf.read())
                    info["title"] = meta.get("title", "")
                except Exception:
                    pass
            for src in Path(d).glob("source.*"):
                if src.suffix in (".mp4", ".mkv", ".webm", ".avi", ".mov"):
                    info["has_video"] = True
                    break
            info["has_srt"] = os.path.exists(os.path.join(d, "original.srt"))
            info["has_trans"] = os.path.exists(os.path.join(d, "translated.srt"))
            past_jobs.append(info)
    past_jobs = past_jobs[:20]

    return render_template(
        "app.html",
        languages=LANGUAGES,
        whisper_models=WHISPER_MODELS,
        translate_providers=TRANSLATE_PROVIDERS,
        translate_engines=TRANSLATE_ENGINES,
        translate_models_json=_get_translate_models_json(),
        default_whisper=DEFAULT_WHISPER_MODEL,
        default_provider=TRANSLATE_PROVIDER,
        default_model=TRANSLATE_MODEL,
        has_anthropic_key=bool(ANTHROPIC_API_KEY),
        has_openai_key=bool(OPENAI_API_KEY),
        source_languages=SOURCE_LANGUAGES,
        transcribe_engines=TRANSCRIBE_ENGINES,
        default_engine=DEFAULT_TRANSCRIBE_ENGINE,
        tts_engines=TTS_ENGINES,
        default_tts_engine=DEFAULT_TTS_ENGINE,
        default_tts_voice=DEFAULT_TTS_VOICE,
        default_tts_seed=DEFAULT_TTS_SEED,
        default_tts_temperature=DEFAULT_TTS_TEMPERATURE,
        default_separate_vocals=DEFAULT_SEPARATE_VOCALS,
        voices=get_voices(),
        build_formats=BUILD_FORMATS,
        build_codecs=BUILD_CODECS,
        build_presets=BUILD_PRESETS,
        build_audio_bitrates=BUILD_AUDIO_BITRATES,
        default_build_format=DEFAULT_BUILD_FORMAT,
        default_build_codec=DEFAULT_BUILD_CODEC,
        default_build_preset=DEFAULT_BUILD_PRESET,
        default_build_audio_bitrate=DEFAULT_BUILD_AUDIO_BITRATE,
        default_build_max_slowdown=DEFAULT_BUILD_MAX_SLOWDOWN,
        default_build_original_audio=DEFAULT_BUILD_ORIGINAL_AUDIO,
        default_build_original_volume=DEFAULT_BUILD_ORIGINAL_VOLUME,
        default_build_no_vocals_volume=DEFAULT_BUILD_NO_VOCALS_VOLUME,
        default_build_vocals_volume=DEFAULT_BUILD_VOCALS_VOLUME,
        default_build_burn_subs=DEFAULT_BUILD_BURN_SUBS,
        past_jobs=past_jobs,
        hf_token=HF_TOKEN,
        tts_download_engines=_get_tts_download_engines(),
        transcribe_download_engines=_get_transcribe_download_engines(),
    )


@app.route("/start", methods=["POST"])
def start():
    data = request.json
    url = (data.get("url") or "").strip()
    skip_transcribe = data.get("skip_transcribe", False)
    skip_translate = data.get("skip_translate", False)
    skip_tts = data.get("skip_tts", False)
    skip_build = data.get("skip_build", False)

    provider = data.get("translate_provider", TRANSLATE_PROVIDER)
    translate_model = data.get("translate_model", TRANSLATE_MODEL)

    if not url and not data.get("work_dir"):
        return jsonify(error="URL обязателен"), 400

    # Pick API key and base_url based on provider
    base_url = ""
    if provider == "claude":
        api_key = ANTHROPIC_API_KEY
    elif provider == "openai":
        api_key = OPENAI_API_KEY
    elif provider == "ollama":
        api_key = "ollama"
        base_url = OLLAMA_URL
    elif provider == "custom":
        api_key = CUSTOM_API_KEY
        base_url = CUSTOM_API_URL
    else:
        api_key = ""  # google doesn't need a key

    if not api_key and provider in ("claude", "openai") and not skip_translate:
        key_name = "ANTHROPIC_API_KEY" if provider == "claude" else "OPENAI_API_KEY"
        return jsonify(error=f"{key_name} не задан в .env или настройках"), 400
    if provider == "custom" and not base_url and not skip_translate:
        return jsonify(error="CUSTOM_API_URL не задан в настройках"), 400

    language = data.get("language", "Russian")
    source_language = data.get("source_language", "")
    whisper_model = data.get("whisper_model", DEFAULT_WHISPER_MODEL)
    transcribe_engine = data.get("transcribe_engine", "openai-whisper")

    job = Job(url, language, whisper_model, source_language, transcribe_engine)
    job.project_name = data.get("project_name", "").strip()
    job.skip_transcribe = skip_transcribe
    job.skip_translate = skip_translate
    job.skip_tts = skip_tts
    job.skip_build = skip_build
    job.separate_vocals = data.get("separate_vocals", False)
    job.translate_provider = provider
    job.translate_model = translate_model
    job.translate_base_url = base_url
    job.tts_engine = data.get("tts_engine", "qwen3-tts")
    job.tts_voice = data.get("tts_voice", "")
    job.tts_seed = int(data.get("tts_seed", DEFAULT_TTS_SEED))
    job.tts_temperature = float(data.get("tts_temperature", DEFAULT_TTS_TEMPERATURE))
    job.num_speakers = int(data.get("num_speakers", 0))
    job.speaker_voice_map = data.get("speaker_voice_map")
    if job.tts_voice:
        from pipeline import _get_voice_reference
        job.tts_voice_wav, job.tts_voice_text = _get_voice_reference(job.tts_voice)
    job.build_format = data.get("build_format", "mp4")
    job.build_codec = data.get("build_codec", "libx264")
    job.build_preset = data.get("build_preset", "fast")
    job.build_audio_bitrate = data.get("build_audio_bitrate", "128k")
    job.build_max_slowdown = float(data.get("build_max_slowdown", 3.0))
    job.build_original_audio = data.get("build_original_audio", "none")
    job.build_original_volume = float(data.get("build_original_volume", 0.1))
    job.build_burn_subs = data.get("build_burn_subs", False)
    job.build_no_vocals_volume = float(data.get("build_no_vocals_volume", 0.5))
    job.build_vocals_volume = float(data.get("build_vocals_volume", 0.15))
    job.build_start_sec = float(data.get("build_start_sec", 0))
    job.build_end_sec = float(data.get("build_end_sec", 0))

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
    if not job:
        return jsonify(error="Job не найден"), 404
    result = {}
    if job.subtitles:
        result["original"] = job.subtitles
    if job.translated:
        result["translated"] = job.translated
    if not result:
        return jsonify(error="Субтитры не готовы"), 404
    return jsonify(**result)


@app.route("/subtitles/<job_id>", methods=["POST"])
def save_subtitles(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify(error="Job не найден"), 404
    data = request.json
    if "original" in data and job.work_dir:
        job.subtitles = data["original"]
        write_srt(job.subtitles, os.path.join(job.work_dir, "original.srt"))
    if "translated" in data and job.work_dir:
        job.translated = data["translated"]
        write_srt(job.translated, os.path.join(job.work_dir, "translated.srt"))
    return jsonify(ok=True)


@app.route("/save-srt", methods=["POST"])
def save_srt_direct():
    """Save subtitles to a work_dir (for resume, no active job needed)."""
    data = request.json
    work_dir = data.get("work_dir", "")
    if not work_dir or not os.path.isdir(work_dir):
        return jsonify(error="Папка не найдена"), 404
    # Security check
    real = os.path.realpath(work_dir)
    allowed = os.path.realpath(DEFAULT_OUTPUT_DIR)
    if not real.startswith(allowed):
        return jsonify(error="Недопустимый путь"), 403
    if "original" in data:
        write_srt(data["original"], os.path.join(work_dir, "original.srt"))
    if "translated" in data:
        write_srt(data["translated"], os.path.join(work_dir, "translated.srt"))
    return jsonify(ok=True)


@app.route("/save-speaker-mapping", methods=["POST"])
def save_speaker_mapping():
    data = request.json
    work_dir = data.get("work_dir", "")
    mapping = data.get("mapping", {})
    if not work_dir or not os.path.isdir(work_dir):
        return jsonify(error="Папка не найдена"), 404
    real = os.path.realpath(work_dir)
    allowed = os.path.realpath(DEFAULT_OUTPUT_DIR)
    if not real.startswith(allowed):
        return jsonify(error="Недопустимый путь"), 403
    map_type = data.get("type", "voice_mapping")
    if map_type == "speaker_map":
        path = os.path.join(work_dir, "speaker_map.json")
    else:
        path = os.path.join(work_dir, "speaker_voice_mapping.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json_mod.dumps(mapping, ensure_ascii=False, indent=2))
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


@app.route("/download-video")
def download_video_route():
    """Download video from URL via yt-dlp with SSE progress."""
    import re as _re
    import subprocess as _sp

    url = (request.args.get("url") or "").strip()
    project_name = (request.args.get("project_name") or "").strip()
    if not url:
        return jsonify(error="URL обязателен"), 400

    os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = os.path.join(DEFAULT_OUTPUT_DIR, f"job_{timestamp}")
    os.makedirs(work_dir, exist_ok=True)
    out_template = os.path.join(work_dir, "source.%(ext)s")

    def stream():
        cmd = [
            "yt-dlp", "--no-playlist", "--newline",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", out_template,
            url,
        ]
        proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True)
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            # Parse yt-dlp progress: [download]  45.2% of ...
            m = _re.search(r'\[download\]\s+([\d.]+)%', line)
            if m:
                pct = m.group(1)
                yield f"data: {json_mod.dumps({'type': 'progress', 'pct': pct})}\n\n"
            else:
                yield f"data: {json_mod.dumps({'type': 'log', 'message': line})}\n\n"

        proc.wait()
        if proc.returncode != 0:
            yield f"data: {json_mod.dumps({'type': 'error', 'message': 'yt-dlp ошибка'})}\n\n"
            return

        # Find downloaded file
        video_path = None
        for src in Path(work_dir).glob("source.*"):
            if src.suffix in (".mp4", ".mkv", ".webm", ".avi"):
                video_path = str(src)
                break
        if video_path:
            # Save meta.json with project name
            meta = {"url": url}
            if project_name:
                meta["title"] = project_name
            meta_path = os.path.join(work_dir, "meta.json")
            with open(meta_path, "w", encoding="utf-8") as mf:
                mf.write(json_mod.dumps(meta, ensure_ascii=False, indent=2))
            yield f"data: {json_mod.dumps({'type': 'done', 'path': video_path, 'work_dir': work_dir, 'filename': os.path.basename(video_path)})}\n\n"
        else:
            yield f"data: {json_mod.dumps({'type': 'error', 'message': 'Файл не найден после скачивания'})}\n\n"

    return Response(stream(), mimetype="text/event-stream")


@app.route("/upload-video", methods=["POST"])
def upload_video():
    """Save uploaded video to a temp work dir, return path."""
    f = request.files.get("file")
    project_name = request.form.get("project_name", "").strip()
    if not f or not f.filename:
        return jsonify(error="Файл не выбран"), 400
    os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = os.path.join(DEFAULT_OUTPUT_DIR, f"job_{timestamp}")
    os.makedirs(work_dir, exist_ok=True)
    ext = os.path.splitext(f.filename)[1] or ".mp4"
    dest = os.path.join(work_dir, f"source{ext}")
    f.save(dest)
    # Save meta.json
    meta = {}
    if project_name:
        meta["title"] = project_name
    meta_path = os.path.join(work_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as mf:
        mf.write(json_mod.dumps(meta, ensure_ascii=False, indent=2))
    return jsonify(path=dest, work_dir=work_dir, filename=f.filename)


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
        info = {"name": name, "path": d, "title": ""}
        meta_path = os.path.join(d, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as mf:
                    meta = json_mod.loads(mf.read())
                info["title"] = meta.get("title", "")
            except Exception:
                pass
        has_video = any(Path(d).glob("source.*"))
        info["has_video"] = has_video
        info["has_srt"] = os.path.exists(os.path.join(d, "original.srt"))
        info["has_trans"] = os.path.exists(os.path.join(d, "translated.srt"))
        if has_video or info["has_srt"]:
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
    # Load meta
    meta_path = os.path.join(work_dir, "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as mf:
            result["meta"] = json_mod.loads(mf.read())
    # Load subtitles if exist
    orig_srt = os.path.join(work_dir, "original.srt")
    if os.path.exists(orig_srt):
        with open(orig_srt, encoding="utf-8") as f:
            result["original_subs"] = parse_srt(f.read())
    trans_srt = os.path.join(work_dir, "translated.srt")
    if os.path.exists(trans_srt):
        with open(trans_srt, encoding="utf-8") as f:
            result["translated_subs"] = parse_srt(f.read())
    # Load speaker map if exists
    speaker_map_path = os.path.join(work_dir, "speaker_map.json")
    if os.path.exists(speaker_map_path):
        with open(speaker_map_path, encoding="utf-8") as f:
            sm = json_mod.loads(f.read())
        # Apply to subs
        for subs_key in ("original_subs", "translated_subs"):
            if subs_key in result:
                for sub in result[subs_key]:
                    key = str(sub["index"])
                    if key in sm:
                        sub["speaker"] = sm[key]
        result["speaker_map"] = sm

    voice_mapping_path = os.path.join(work_dir, "speaker_voice_mapping.json")
    if os.path.exists(voice_mapping_path):
        with open(voice_mapping_path, encoding="utf-8") as f:
            result["speaker_voice_mapping"] = json_mod.loads(f.read())

    # Find source video
    for src in Path(work_dir).glob("source.*"):
        if src.suffix in (".mp4", ".mkv", ".webm", ".avi", ".mov"):
            result["source_video"] = str(src)
            break
    # Check for TTS audio
    tts_dir = os.path.join(work_dir, "tts_audio")
    if os.path.isdir(tts_dir) and any(f.endswith(".wav") for f in os.listdir(tts_dir)):
        result["has_tts"] = True
    # Find output video
    for out in Path(work_dir).glob("output.*"):
        if out.suffix in (".mp4", ".mkv", ".webm", ".avi", ".mov"):
            result["output_video"] = str(out)
            break
    return jsonify(**result)


# ── Voice management ──────────────────────────────────────────────────────────


@app.route("/edge-voices")
def list_edge_voices():
    """List available Edge TTS voices."""
    import asyncio
    try:
        import edge_tts
        voices = asyncio.run(edge_tts.list_voices())
        result = [{"name": v["ShortName"], "lang": v.get("Locale", "")} for v in voices]
        return jsonify(voices=result)
    except Exception:
        return jsonify(voices=[])


@app.route("/macos-voices")
def list_macos_voices():
    """List available macOS say voices."""
    import subprocess, platform, re
    if platform.system() != "Darwin":
        return jsonify(voices=[])
    try:
        out = subprocess.check_output(["say", "-v", "?"], text=True)
        voices = []
        for line in out.strip().split("\n"):
            # Format: "Name (optional info)    lang_CODE  # description"
            m = re.match(r'^(.+?)\s{2,}(\S+)\s+#', line)
            if m:
                name = m.group(1).strip()
                lang = m.group(2).strip()
                voices.append({"name": name, "lang": lang})
        return jsonify(voices=voices)
    except Exception:
        return jsonify(voices=[])


@app.route("/voices")
def list_voices():
    return jsonify(voices=get_voices())


@app.route("/upload-voice", methods=["POST"])
def upload_voice():
    """Upload a reference WAV sample to a voice profile (creates if new)."""
    import json as _j

    f = request.files.get("file")
    name = request.form.get("name", "").strip()
    text = request.form.get("text", "").strip()
    if not f or not name:
        return jsonify(error="Файл и имя обязательны"), 400

    safe_name = "".join(c for c in name if c.isalnum() or c in "-_ ").strip().replace(" ", "_")
    if not safe_name:
        return jsonify(error="Некорректное имя"), 400

    voice_dir = os.path.join(VOICES_DIR, safe_name)
    os.makedirs(voice_dir, exist_ok=True)

    # Random filename
    file_id = uuid.uuid4().hex[:8]
    filename = f"{file_id}.wav"
    wav_path = os.path.join(voice_dir, filename)
    f.save(wav_path)

    # Update meta.json
    meta_path = os.path.join(voice_dir, "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as mf:
            meta = _j.loads(mf.read())
    else:
        meta = {"samples": []}

    meta["samples"].append({"file": filename, "text": text})
    with open(meta_path, "w", encoding="utf-8") as mf:
        mf.write(_j.dumps(meta, ensure_ascii=False, indent=2))

    return jsonify(ok=True, name=safe_name, file=filename, path=wav_path)


@app.route("/transcribe-voice", methods=["POST"])
def transcribe_voice():
    """Transcribe a voice reference WAV using current engine/model settings."""
    data = request.json
    wav_path = data.get("wav", "")
    if not wav_path or not os.path.exists(wav_path):
        return jsonify(error="WAV не найден"), 404
    engine = data.get("engine", DEFAULT_TRANSCRIBE_ENGINE)
    model = data.get("model", DEFAULT_WHISPER_MODEL)
    try:
        from pipeline import transcribe_audio as _ta
        api_key = OPENAI_API_KEY if engine == "whisper-api" else ""
        subs = _ta(wav_path, os.path.dirname(wav_path), model,
                    lambda msg: None, engine=engine, api_key=api_key)
        text = " ".join(s["text"] for s in subs)
        return jsonify(text=text)
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/update-voice-sample", methods=["POST"])
def update_voice_sample():
    """Update text for a voice sample."""
    import json as _j
    data = request.json
    name = data.get("name", "")
    filename = data.get("file", "")
    text = data.get("text", "")
    meta_path = os.path.join(VOICES_DIR, name, "meta.json")
    if not os.path.exists(meta_path):
        return jsonify(error="Голос не найден"), 404
    with open(meta_path, encoding="utf-8") as mf:
        meta = _j.loads(mf.read())
    for s in meta["samples"]:
        if s["file"] == filename:
            s["text"] = text
            break
    with open(meta_path, "w", encoding="utf-8") as mf:
        mf.write(_j.dumps(meta, ensure_ascii=False, indent=2))
    return jsonify(ok=True)


@app.route("/delete-voice-sample", methods=["POST"])
def delete_voice_sample():
    """Delete a single sample from a voice profile."""
    import json as _j

    data = request.json
    name = data.get("name", "")
    filename = data.get("file", "")
    voice_dir = os.path.join(VOICES_DIR, name)
    meta_path = os.path.join(voice_dir, "meta.json")
    if not os.path.exists(meta_path):
        return jsonify(error="Голос не найден"), 404

    # Remove file
    wav_path = os.path.join(voice_dir, filename)
    if os.path.exists(wav_path):
        os.remove(wav_path)

    # Update meta
    with open(meta_path, encoding="utf-8") as mf:
        meta = _j.loads(mf.read())
    meta["samples"] = [s for s in meta["samples"] if s["file"] != filename]
    with open(meta_path, "w", encoding="utf-8") as mf:
        mf.write(_j.dumps(meta, ensure_ascii=False, indent=2))

    return jsonify(ok=True, remaining=len(meta["samples"]))


@app.route("/delete-voice", methods=["POST"])
def delete_voice():
    data = request.json
    name = data.get("name", "")
    voice_dir = os.path.join(VOICES_DIR, name)
    if not os.path.isdir(voice_dir):
        return jsonify(error="Голос не найден"), 404
    shutil.rmtree(voice_dir)
    return jsonify(ok=True)


@app.route("/rename-job", methods=["POST"])
def rename_job():
    """Update project title in meta.json."""
    data = request.json
    path = data.get("path", "")
    title = data.get("title", "").strip()
    if not path or not os.path.isdir(path):
        return jsonify(error="Папка не найдена"), 404
    meta_path = os.path.join(path, "meta.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as mf:
            meta = json_mod.loads(mf.read())
    meta["title"] = title
    with open(meta_path, "w", encoding="utf-8") as mf:
        mf.write(json_mod.dumps(meta, ensure_ascii=False, indent=2))
    return jsonify(ok=True)


# ── Model management ──────────────────────────────────────────────────────────


@app.route("/voice-audio")
def serve_voice_audio():
    """Serve a voice reference WAV file."""
    name = request.args.get("name", "")
    file = request.args.get("file", "")
    if not name or not file:
        return "Not found", 404
    path = os.path.join(VOICES_DIR, name, file)
    real = os.path.realpath(path)
    allowed = os.path.realpath(VOICES_DIR)
    if not real.startswith(allowed) or not os.path.exists(real):
        return "Not found", 404
    return send_file(real, mimetype="audio/wav")


_tts_tasks: dict[str, queue.Queue] = {}

@app.route("/tts-test", methods=["POST"])
def tts_test():
    """Start TTS test — returns task_id for SSE progress."""
    data = request.json
    text = data.get("text", "").strip()
    if not text:
        return jsonify(error="Текст обязателен"), 400

    tts_engine = data.get("tts_engine", DEFAULT_TTS_ENGINE)
    tts_voice = data.get("tts_voice", DEFAULT_TTS_VOICE)
    voice_wav, voice_text = "", ""
    if tts_voice and not tts_engine.endswith("-custom"):
        from pipeline import _get_voice_reference
        voice_wav, voice_text = _get_voice_reference(tts_voice)

    tts_seed = int(data.get("tts_seed", DEFAULT_TTS_SEED))
    tts_temp = float(data.get("tts_temperature", DEFAULT_TTS_TEMPERATURE))

    task_id = uuid.uuid4().hex[:8]
    q: queue.Queue = queue.Queue()
    _tts_tasks[task_id] = q

    def run():
        import tempfile
        from pipeline import synthesize_speech
        tmp_dir = tempfile.mkdtemp(prefix="tts_test_")
        sub = {"index": 1, "text": text, "start": 0, "end": 0}
        try:
            result = synthesize_speech(
                [sub], tmp_dir, lambda msg: q.put({"event": "log", "data": {"message": msg}}),
                engine=tts_engine, voice=tts_voice,
                voice_wav=voice_wav, voice_text=voice_text,
                seed=tts_seed, temperature=tts_temp,
            )
            audio_path = result[0].get("audio_path", "")
            q.put({"event": "done", "data": {"audio_path": audio_path}})
        except Exception as e:
            q.put({"event": "error", "data": {"message": str(e)}})

    threading.Thread(target=run, daemon=True).start()
    return jsonify(task_id=task_id)


@app.route("/tts-task-progress/<task_id>")
def tts_task_progress(task_id: str):
    """SSE stream for TTS task progress (test & single)."""
    q = _tts_tasks.get(task_id)
    if not q:
        return "Not found", 404

    def stream():
        while True:
            try:
                msg = q.get(timeout=60)
            except queue.Empty:
                yield "event: ping\ndata: {}\n\n"
                continue
            yield f"event: {msg['event']}\ndata: {json_mod.dumps(msg['data'], ensure_ascii=False)}\n\n"
            if msg["event"] in ("done", "error"):
                _tts_tasks.pop(task_id, None)
                break

    return Response(stream(), mimetype="text/event-stream")


@app.route("/tts-test-audio")
def serve_tts_test_audio():
    """Serve a test TTS audio file."""
    path = request.args.get("path", "")
    if not path or not os.path.exists(path):
        return "Not found", 404
    return send_file(path, mimetype="audio/wav")


@app.route("/downloaded-models")
def list_downloaded_models():
    """List downloaded models, filterable by category (whisper or tts)."""
    from pipeline import TTS_MODELS_DIR
    category = request.args.get("category", "")  # "whisper" or "tts"
    result = []

    if category != "tts":
        # Delegate to transcribe plugins
        try:
            from plugins.transcribe import discover_plugins as _dt
            _, t_plugins = _dt()
            seen = set()
            for mod in t_plugins.values():
                if mod in seen:
                    continue
                seen.add(mod)
                if hasattr(mod, 'list_downloaded_models'):
                    result.extend(mod.list_downloaded_models())
        except Exception:
            pass

    if category != "whisper":
        # TTS models (HuggingFace hub inside tts dir)
        tts_hub = os.path.join(TTS_MODELS_DIR, "hub")
        if os.path.isdir(tts_hub):
            for d in sorted(os.listdir(tts_hub)):
                dp = os.path.join(tts_hub, d)
                if os.path.isdir(dp) and d.startswith("models--") and ".lock" not in d:
                    model_name = d.replace("models--", "").replace("--", "/")
                    total = sum(
                        os.path.getsize(os.path.join(root, f))
                        for root, _, files in os.walk(dp) for f in files
                    )
                    size_mb = total / 1024 / 1024
                    result.append({
                        "name": model_name,
                        "engine": "TTS",
                        "file": d,
                        "size": f"{size_mb:.0f} MB",
                        "path": dp,
                        "category": "tts",
                    })

    return jsonify(models=result)


@app.route("/download-model", methods=["POST"])
def download_model_route():
    """Download a whisper/TTS model in background, stream progress via SSE."""
    data = request.json
    engine = data.get("engine", "openai-whisper")
    model = data.get("model", "")
    if not model:
        return jsonify(error="Укажите модель"), 400

    def stream():
        try:
            # Universal plugin-based model download dispatcher
            plugin = _find_download_plugin(engine)
            if plugin and hasattr(plugin, 'download_model'):
                yield from plugin.download_model(engine, model, None)
            else:
                yield f"data: {json_mod.dumps({'type': 'error', 'message': f'Плагин загрузки не найден для {engine}'})}\n\n"

        except Exception as e:
            yield f"data: {json_mod.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(stream(), mimetype="text/event-stream")


@app.route("/delete-model", methods=["POST"])
def delete_model():
    """Delete a downloaded model."""
    data = request.json
    path = data.get("path", "")
    if not path or not os.path.exists(path):
        return jsonify(error="Модель не найдена"), 404
    from pipeline import WHISPER_MODELS_DIR, TTS_MODELS_DIR
    real = os.path.realpath(path)
    allowed_w = os.path.realpath(WHISPER_MODELS_DIR)
    allowed_t = os.path.realpath(TTS_MODELS_DIR)
    if not (real.startswith(allowed_w) or real.startswith(allowed_t)):
        return jsonify(error="Недопустимый путь"), 403
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
    return jsonify(ok=True)


@app.route("/delete-job", methods=["POST"])
def delete_job():
    """Delete a job folder from disk."""
    data = request.json
    path = data.get("path", "")
    if not path or not os.path.isdir(path):
        return jsonify(error="Папка не найдена"), 404
    # Security: only allow deleting from output dir
    real = os.path.realpath(path)
    allowed = os.path.realpath(DEFAULT_OUTPUT_DIR)
    if not real.startswith(allowed) or real == allowed:
        return jsonify(error="Недопустимый путь"), 403
    shutil.rmtree(path)
    return jsonify(ok=True)


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


@app.route("/tts-single", methods=["POST"])
def tts_single():
    """Synthesize TTS for a single subtitle segment."""
    data = request.json
    work_dir = data.get("work_dir", "")
    text = data.get("text", "")
    index = data.get("index", 1)
    if not work_dir or not text:
        return jsonify(error="work_dir и text обязательны"), 400
    # Security
    real = os.path.realpath(work_dir)
    allowed = os.path.realpath(DEFAULT_OUTPUT_DIR)
    if not real.startswith(allowed):
        return jsonify(error="Недопустимый путь"), 403

    tts_engine = data.get("tts_engine", DEFAULT_TTS_ENGINE)
    tts_voice = data.get("tts_voice", DEFAULT_TTS_VOICE)
    voice_wav, voice_text = "", ""
    if tts_voice and not tts_engine.endswith("-custom"):
        from pipeline import _get_voice_reference
        voice_wav, voice_text = _get_voice_reference(tts_voice)

    tts_seed = int(data.get("tts_seed", DEFAULT_TTS_SEED))
    tts_temp = float(data.get("tts_temperature", DEFAULT_TTS_TEMPERATURE))
    # Delete existing segment so it gets regenerated
    existing = os.path.join(work_dir, "tts_audio", f"seg_{index:04d}.wav")
    if os.path.exists(existing):
        os.remove(existing)

    task_id = uuid.uuid4().hex[:8]
    q: queue.Queue = queue.Queue()
    _tts_tasks[task_id] = q

    def run():
        from pipeline import synthesize_speech
        sub = {"index": index, "text": text, "start": 0, "end": 0}
        try:
            result = synthesize_speech(
                [sub], work_dir, lambda msg: q.put({"event": "log", "data": {"message": msg}}),
                engine=tts_engine, voice=tts_voice,
                voice_wav=voice_wav, voice_text=voice_text,
                seed=tts_seed, temperature=tts_temp,
            )
            audio_path = result[0].get("audio_path", "")
            q.put({"event": "done", "data": {"audio_path": audio_path}})
        except Exception as e:
            q.put({"event": "error", "data": {"message": str(e)}})

    threading.Thread(target=run, daemon=True).start()
    return jsonify(task_id=task_id)


@app.route("/video-info")
def video_info():
    """Get video file info via ffprobe."""
    import subprocess as _sp
    path = request.args.get("path", "")
    if not path or not os.path.exists(path):
        return jsonify(error="Файл не найден"), 404
    try:
        out = _sp.check_output([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", path
        ], text=True)
        import json as _j
        data = _j.loads(out)
        info = {}
        fmt = data.get("format", {})
        info["duration"] = float(fmt.get("duration", 0))
        info["size_mb"] = round(int(fmt.get("size", 0)) / 1024 / 1024, 1)
        info["bitrate"] = round(int(fmt.get("bit_rate", 0)) / 1000)
        info["format"] = fmt.get("format_long_name", fmt.get("format_name", ""))
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and "video" not in info:
                info["video"] = {
                    "codec": s.get("codec_name", ""),
                    "width": s.get("width", 0),
                    "height": s.get("height", 0),
                    "fps": eval(s["r_frame_rate"]) if s.get("r_frame_rate") and "/" in str(s.get("r_frame_rate", "")) else s.get("r_frame_rate", ""),
                    "bitrate": round(int(s.get("bit_rate", 0)) / 1000) if s.get("bit_rate") else 0,
                }
            elif s.get("codec_type") == "audio" and "audio" not in info:
                info["audio"] = {
                    "codec": s.get("codec_name", ""),
                    "sample_rate": s.get("sample_rate", ""),
                    "channels": s.get("channels", 0),
                    "bitrate": round(int(s.get("bit_rate", 0)) / 1000) if s.get("bit_rate") else 0,
                }
        return jsonify(**info)
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/audio-waveform")
def audio_waveform():
    """Return waveform peaks for audio file."""
    path = request.args.get("path", "")
    if not path or not os.path.exists(path):
        return jsonify(error="Файл не найден"), 404
    import subprocess as _sp
    import struct
    # Downsample to 8kHz mono, get raw PCM
    try:
        raw = _sp.check_output([
            "ffmpeg", "-i", path, "-ar", "8000", "-ac", "1",
            "-f", "s16le", "-acodec", "pcm_s16le", "-"
        ], stderr=_sp.DEVNULL)
        samples = struct.unpack(f"<{len(raw)//2}h", raw)
        # Reduce to ~800 peaks
        n = len(samples)
        bucket = max(1, n // 800)
        peaks = []
        for i in range(0, n, bucket):
            chunk = samples[i:i+bucket]
            peaks.append(max(abs(s) for s in chunk) / 32768.0)
        return jsonify(peaks=peaks, duration=n / 8000.0)
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/delete-output", methods=["POST"])
def delete_output():
    """Delete a built output video."""
    data = request.json
    path = data.get("path", "")
    if not path or not os.path.exists(path):
        return jsonify(error="Файл не найден"), 404
    real = os.path.realpath(path)
    allowed = os.path.realpath(DEFAULT_OUTPUT_DIR)
    if not real.startswith(allowed):
        return jsonify(error="Недопустимый путь"), 403
    os.remove(path)
    return jsonify(ok=True)


@app.route("/tts-segments")
def list_tts_segments():
    """List existing TTS segment indices for a work dir."""
    work_dir = request.args.get("work_dir", "")
    tts_dir = os.path.join(work_dir, "tts_audio")
    if not os.path.isdir(tts_dir):
        return jsonify(segments=[])
    indices = []
    for f in os.listdir(tts_dir):
        if f.startswith("seg_") and f.endswith(".wav"):
            try:
                indices.append(int(f[4:8]))
            except ValueError:
                pass
    return jsonify(segments=sorted(indices))


@app.route("/delete-tts-segment", methods=["POST"])
def delete_tts_segment():
    """Delete a single TTS audio segment."""
    data = request.json
    path = data.get("path", "")
    if not path or not os.path.exists(path):
        return jsonify(error="Файл не найден"), 404
    real = os.path.realpath(path)
    allowed = os.path.realpath(DEFAULT_OUTPUT_DIR)
    if not real.startswith(allowed):
        return jsonify(error="Недопустимый путь"), 403
    os.remove(path)
    return jsonify(ok=True)


@app.route("/tts-audio")
def serve_tts_audio():
    """Serve a TTS audio segment (no cache)."""
    path = request.args.get("path", "")
    if not path or not os.path.exists(path):
        return "Not found", 404
    real = os.path.realpath(path)
    allowed = os.path.realpath(DEFAULT_OUTPUT_DIR)
    if not real.startswith(allowed):
        return "Forbidden", 403
    resp = send_file(real, mimetype="audio/wav")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


# ── Settings ─────────────────────────────────────────────────────────────────


def _read_env() -> dict[str, str]:
    """Read .env file as dict."""
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _write_env(env: dict[str, str]):
    """Write dict to .env file."""
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        for k, v in env.items():
            f.write(f"{k}={v}\n")


@app.route("/settings")
def get_settings():
    return jsonify(
        anthropic_api_key=ANTHROPIC_API_KEY,
        openai_api_key=OPENAI_API_KEY,
        custom_api_key=CUSTOM_API_KEY,
        custom_api_url=CUSTOM_API_URL,
        ollama_url=OLLAMA_URL,
        hf_token=HF_TOKEN,
        translate_provider=TRANSLATE_PROVIDER,
        translate_model=TRANSLATE_MODEL,
        whisper_model=DEFAULT_WHISPER_MODEL,
        output_dir=DEFAULT_OUTPUT_DIR,
    )


@app.route("/settings", methods=["POST"])
def save_settings():
    global ANTHROPIC_API_KEY, OPENAI_API_KEY, CUSTOM_API_KEY, CUSTOM_API_URL, HF_TOKEN
    global OLLAMA_URL, TRANSLATE_PROVIDER, TRANSLATE_MODEL
    global DEFAULT_TRANSCRIBE_ENGINE, DEFAULT_WHISPER_MODEL
    global DEFAULT_TTS_ENGINE, DEFAULT_TTS_VOICE, DEFAULT_TTS_SEED, DEFAULT_TTS_TEMPERATURE, DEFAULT_OUTPUT_DIR

    data = request.json
    env = _read_env()

    # Map of setting name -> (env key, global var name)
    _SETTINGS = {
        "anthropic_api_key":  "ANTHROPIC_API_KEY",
        "openai_api_key":     "OPENAI_API_KEY",
        "custom_api_key":     "CUSTOM_API_KEY",
        "custom_api_url":     "CUSTOM_API_URL",
        "hf_token":           "HF_TOKEN",
        "ollama_url":         "OLLAMA_URL",
        "translate_provider": "TRANSLATE_PROVIDER",
        "translate_model":    "TRANSLATE_MODEL",
        "transcribe_engine":  "TRANSCRIBE_ENGINE",
        "whisper_model":      "WHISPER_MODEL",
        "separate_vocals":    "SEPARATE_VOCALS",
        "tts_engine":         "TTS_ENGINE",
        "tts_voice":          "TTS_VOICE",
        "tts_seed":           "TTS_SEED",
        "tts_temperature":    "TTS_TEMPERATURE",
        "output_dir":         "OUTPUT_DIR",
        "build_format":       "BUILD_FORMAT",
        "build_codec":        "BUILD_CODEC",
        "build_preset":       "BUILD_PRESET",
        "build_audio_bitrate":"BUILD_AUDIO_BITRATE",
        "build_max_slowdown": "BUILD_MAX_SLOWDOWN",
        "build_original_audio":"BUILD_ORIGINAL_AUDIO",
        "build_original_volume":"BUILD_ORIGINAL_VOLUME",
        "build_no_vocals_volume":"BUILD_NO_VOCALS_VOLUME",
        "build_vocals_volume":  "BUILD_VOCALS_VOLUME",
        "build_burn_subs":    "BUILD_BURN_SUBS",
    }

    for field, env_key in _SETTINGS.items():
        if field in data:
            val = data[field]
            if isinstance(val, bool):
                val = "true" if val else "false"
            else:
                val = str(val)
            env[env_key] = val
            os.environ[env_key] = val

    # Update globals
    if "anthropic_api_key" in data:
        ANTHROPIC_API_KEY = data["anthropic_api_key"]
    if "openai_api_key" in data:
        OPENAI_API_KEY = data["openai_api_key"]
    if "custom_api_key" in data:
        CUSTOM_API_KEY = data["custom_api_key"]
    if "custom_api_url" in data:
        CUSTOM_API_URL = data["custom_api_url"]
    if "hf_token" in data:
        HF_TOKEN = data["hf_token"]
        os.environ["HF_TOKEN"] = HF_TOKEN
    if "ollama_url" in data:
        OLLAMA_URL = data["ollama_url"]
    if "translate_provider" in data:
        TRANSLATE_PROVIDER = data["translate_provider"]
    if "translate_model" in data:
        TRANSLATE_MODEL = data["translate_model"]
    if "transcribe_engine" in data:
        DEFAULT_TRANSCRIBE_ENGINE = data["transcribe_engine"]
    if "whisper_model" in data:
        DEFAULT_WHISPER_MODEL = data["whisper_model"]
    if "tts_engine" in data:
        DEFAULT_TTS_ENGINE = data["tts_engine"]
    if "tts_voice" in data:
        DEFAULT_TTS_VOICE = data["tts_voice"]
    if "tts_seed" in data:
        DEFAULT_TTS_SEED = int(data["tts_seed"])
    if "tts_temperature" in data:
        DEFAULT_TTS_TEMPERATURE = float(data["tts_temperature"])
    if "output_dir" in data:
        DEFAULT_OUTPUT_DIR = os.path.abspath(os.path.expanduser(data["output_dir"]))

    _write_env(env)
    return jsonify(ok=True)


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

        # Save/update meta.json
        meta_path = os.path.join(job.work_dir, "meta.json")
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as mf:
                meta = json_mod.loads(mf.read())
        if job.project_name:
            meta["title"] = job.project_name
        meta["language"] = job.language
        meta["source_language"] = job.source_language
        meta["tts_engine"] = job.tts_engine
        meta["tts_voice"] = job.tts_voice
        meta["translate_provider"] = job.translate_provider
        meta["translate_model"] = job.translate_model
        with open(meta_path, "w", encoding="utf-8") as mf:
            mf.write(json_mod.dumps(meta, ensure_ascii=False, indent=2))

        def log(msg):
            _emit(job, "log", message=msg)

        # 1. Dependencies
        check_dependencies(log)

        # ── Download ──
        _emit(job, "step", key="download", state="active")
        url = job.url
        # Check if source already exists in work_dir (resume)
        existing_source = None
        for src in Path(job.work_dir).glob("source.*"):
            if src.suffix in (".mp4", ".mkv", ".webm", ".avi", ".mov"):
                existing_source = str(src)
                break
        if existing_source and not url:
            job.source_video = existing_source
            log(f"📁 Используется существующий файл: {Path(existing_source).name}")
        elif url.startswith("http://") or url.startswith("https://"):
            video_path = download_video(url, job.work_dir, log)
            job.source_video = video_path
        else:
            ext = Path(url).suffix
            video_path = os.path.join(job.work_dir, f"source{ext}")
            if not os.path.exists(video_path):
                shutil.copy2(url, video_path)
            log(f"📁 Файл: {Path(video_path).name}")
            job.source_video = video_path
        _emit(job, "step", key="download", state="done")
        _emit(job, "source_ready", path=job.source_video)

        # ── Transcribe ──
        if job.skip_transcribe:
            _emit(job, "step", key="transcribe", state="done")
            log("⏭️ Транскрипция пропущена")
            # Load from file if not already set
            if not job.subtitles:
                orig_srt = os.path.join(job.work_dir, "original.srt")
                if os.path.exists(orig_srt):
                    with open(orig_srt, encoding="utf-8") as f:
                        job.subtitles = parse_srt(f.read())
                    log(f"📝 Загружены субтитры из original.srt: {len(job.subtitles)} фраз")
        else:
            if not job.source_video:
                raise Exception("Нет исходного видео для транскрипции")
            _emit(job, "step", key="transcribe", state="active")
            audio_path = extract_audio(job.source_video, job.work_dir, log)
            transcribe_path = audio_path
            # Optionally separate vocals from background
            if job.separate_vocals:
                from pipeline import separate_vocals
                vocals_path, _ = separate_vocals(audio_path, job.work_dir, log)
                transcribe_path = vocals_path
            # For whisper-api, need OpenAI key
            _transcribe_key = OPENAI_API_KEY if job.transcribe_engine == "whisper-api" else ""
            def _on_transcribe_segment(sub):
                _emit(job, "sub_add", sub=sub, mode="original")

            job.subtitles = transcribe_audio(
                transcribe_path, job.work_dir, job.whisper_model, log,
                source_language=job.source_language,
                engine=job.transcribe_engine,
                api_key=_transcribe_key,
                num_speakers=job.num_speakers,
                on_segment=_on_transcribe_segment,
            )
            if any("speaker" in s for s in job.subtitles):
                from pipeline import write_speaker_map
                write_speaker_map(job.subtitles, os.path.join(job.work_dir, "speaker_map.json"))
            _emit(job, "step", key="transcribe", state="done")

        # Show original subtitles after transcription
        if job.subtitles:
            _emit(job, "original_ready")

        # ── Translate ──
        if job.skip_translate:
            _emit(job, "step", key="translate", state="done")
            log("⏭️ Перевод пропущен")
            # Load from file if not already set
            if not job.translated:
                trans_srt = os.path.join(job.work_dir, "translated.srt")
                if os.path.exists(trans_srt):
                    with open(trans_srt, encoding="utf-8") as f:
                        job.translated = parse_srt(f.read())
                    log(f"🌐 Загружены субтитры из translated.srt: {len(job.translated)} фраз")
                elif job.subtitles:
                    # Use original as translated (user can edit)
                    job.translated = [s.copy() for s in job.subtitles]
                    log("📝 Используются оригинальные субтитры (можно отредактировать)")
        else:
            if not job.subtitles:
                raise Exception("Нет субтитров для перевода")
            _emit(job, "step", key="translate", state="active")
            def _on_translate_chunk(subs):
                _emit(job, "sub_add", subs=subs, mode="translated")

            job.translated = translate_subtitles(
                job.subtitles, job.language, api_key, job.work_dir, log,
                provider=job.translate_provider, model=job.translate_model,
                base_url=job.translate_base_url,
                on_chunk=_on_translate_chunk,
            )
            _emit(job, "step", key="translate", state="done")

        # Show subtitles on screen
        if job.translated:
            _emit(job, "subtitles_ready")
        else:
            raise Exception("Нет субтитров для синтеза речи")

        # ── TTS ──
        if job.skip_tts:
            _emit(job, "step", key="tts", state="done")
            log("⏭️ Синтез речи пропущен")
            # Load existing TTS audio — skip missing segments
            tts_dir = os.path.join(job.work_dir, "tts_audio")
            subs_with_audio = []
            missing = 0
            for sub in job.translated:
                audio_path = os.path.join(tts_dir, f"seg_{sub['index']:04d}.wav")
                if os.path.exists(audio_path):
                    subs_with_audio.append({**sub, "audio_path": audio_path})
                else:
                    missing += 1
            if not subs_with_audio:
                raise Exception("TTS пропущен, ни одного аудио-файла не найдено")
            if missing:
                log(f"⚠️ {missing} сегментов без TTS — будут пропущены при сборке")
            log(f"🔊 Загружены {len(subs_with_audio)} TTS-файлов")
        else:
            _emit(job, "step", key="tts", state="active")
            def _on_tts_segment(index):
                _emit(job, "tts_segment", index=index)

            subs_with_audio = synthesize_speech(
                job.translated, job.work_dir, log,
                engine=job.tts_engine,
                voice=job.tts_voice,
                voice_wav=job.tts_voice_wav,
                voice_text=job.tts_voice_text,
                seed=job.tts_seed,
                temperature=job.tts_temperature,
                speaker_voice_map=job.speaker_voice_map,
                on_segment=_on_tts_segment,
            )
            _emit(job, "step", key="tts", state="done")
        _emit(job, "tts_ready", work_dir=job.work_dir,
              count=len(subs_with_audio))

        # ── Separate vocals for build if needed ──
        if job.build_original_audio in ("no_vocals", "voiceover"):
            no_vocals = os.path.join(job.work_dir, "no_vocals.wav")
            if not os.path.exists(no_vocals):
                audio_full = os.path.join(job.work_dir, "audio.wav")
                if not os.path.exists(audio_full):
                    audio_full = extract_audio(job.source_video, job.work_dir, log)
                from pipeline import separate_vocals
                separate_vocals(audio_full, job.work_dir, log)

        # ── Build ──
        if job.skip_build:
            _emit(job, "step", key="build", state="done")
            log("⏭️ Сборка пропущена")
        else:
            if not job.source_video:
                raise Exception("Нет исходного видео для сборки")
            _emit(job, "step", key="build", state="active")
            ext = job.build_format
            out_name = f"output.{ext}"
            job.result_path = os.path.join(job.work_dir, out_name)
            if os.path.exists(job.result_path):
                os.remove(job.result_path)
            srt_file = os.path.join(job.work_dir, "translated.srt")
            # Filter subtitles by time range
            build_subs = subs_with_audio
            if job.build_start_sec or job.build_end_sec:
                start_s = job.build_start_sec
                end_s = job.build_end_sec if job.build_end_sec > 0 else float('inf')
                build_subs = [s for s in subs_with_audio
                              if s['end'] > start_s and s['start'] < end_s]
                log(f"✂️ Диапазон сборки: {start_s}с — {'конец' if end_s == float('inf') else str(end_s) + 'с'} ({len(build_subs)} из {len(subs_with_audio)} субтитров)")
            build_final_video(
                job.source_video, build_subs, job.result_path, job.work_dir, log,
                codec=job.build_codec,
                preset=job.build_preset,
                audio_bitrate=job.build_audio_bitrate,
                max_slowdown=job.build_max_slowdown,
                original_audio_mode=job.build_original_audio,
                original_audio_volume=job.build_original_volume,
                no_vocals_volume=job.build_no_vocals_volume,
                vocals_volume=job.build_vocals_volume,
                burn_subtitles=job.build_burn_subs,
                srt_path=srt_file,
                start_sec=job.build_start_sec,
                end_sec=job.build_end_sec,
            )
            _emit(job, "step", key="build", state="done")

        job.state = "done"
        _emit(job, "done", path=job.result_path or job.work_dir)

    except Exception as e:
        job.state = "error"
        _emit(job, "error", message=str(e))


# ── Entry point ──────────────────────────────────────────────────────────────


def main():
    import signal

    from gevent.pywsgi import WSGIServer

    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Serving on http://{host}:{port}")
    server = WSGIServer((host, port), app)

    def shutdown(*_):
        print("\nОстановка...")
        server.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    server.serve_forever()


if __name__ == "__main__":
    main()
