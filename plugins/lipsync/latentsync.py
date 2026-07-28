"""LatentSync lip sync plugin -- best quality audio-driven lip sync (CUDA only)."""
import os
import sys
import subprocess as _sp

_VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"


ENGINES = {"latentsync": "LatentSync (CUDA, высокое качество)"}

LATENTSYNC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".latentsync")
LATENTSYNC_VENV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".venv-latentsync")
LATENTSYNC_REPO = "https://github.com/bytedance/LatentSync.git"
RUNNER_SCRIPT = os.path.join(os.path.dirname(__file__), "_latentsync_runner.py")


def _install_decord_shim():
    """Create opencv-based decord shim package in venv site-packages."""
    python = _get_python()
    r = _sp.run([python, "-c", "import site; print(site.getsitepackages()[0])"],
                capture_output=True, text=True, encoding="utf-8")
    site_dir = r.stdout.strip()
    pkg = os.path.join(site_dir, "decord")
    os.makedirs(pkg, exist_ok=True)

    with open(os.path.join(pkg, "__init__.py"), "w") as f:
        f.write("from .video_reader import VideoReader\nfrom .audio_reader import AudioReader\nfrom . import ndarray as _nd\ndef cpu(idx=0): return idx\n")

    with open(os.path.join(pkg, "video_reader.py"), "w") as f:
        f.write("""import cv2, numpy as np
class VideoReader:
    def __init__(self, uri, ctx=None, width=-1, height=-1, num_threads=0):
        self.cap = cv2.VideoCapture(str(uri))
        if not self.cap.isOpened(): raise RuntimeError(f"Cannot open video: {uri}")
        self._fps = self.cap.get(cv2.CAP_PROP_FPS) or 25
        self._count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._w, self._h = width, height
    def __len__(self): return self._count
    def __getitem__(self, idx):
        if isinstance(idx, (list, np.ndarray)):
            frames = []
            for i in idx:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
                ret, f = self.cap.read()
                f = cv2.cvtColor(f, cv2.COLOR_BGR2RGB) if ret else np.zeros((self._h or 256, self._w or 256, 3), dtype=np.uint8)
                if ret and self._w > 0 and self._h > 0: f = cv2.resize(f, (self._w, self._h))
                frames.append(f)
            return np.stack(frames)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ret, f = self.cap.read()
        if ret:
            f = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
            if self._w > 0 and self._h > 0: f = cv2.resize(f, (self._w, self._h))
            return f
        return np.zeros((self._h or 256, self._w or 256, 3), dtype=np.uint8)
    def get_batch(self, indices): return self[indices]
    def get_avg_fps(self): return self._fps
    def __del__(self):
        if hasattr(self, 'cap') and self.cap: self.cap.release()
""")

    with open(os.path.join(pkg, "audio_reader.py"), "w") as f:
        f.write("""import numpy as np
class AudioReader:
    def __init__(self, uri, ctx=None, sample_rate=16000, mono=True):
        import soundfile as sf
        data, sr = sf.read(str(uri), dtype='float32')
        if mono and data.ndim > 1: data = data.mean(axis=1)
        if sr != sample_rate:
            import subprocess, tempfile, os as _os
            tmp = tempfile.mktemp(suffix='.wav')
            subprocess.run(['ffmpeg','-y','-i',str(uri),'-ar',str(sample_rate),'-ac','1',tmp], capture_output=True)
            data, sr = sf.read(tmp, dtype='float32')
            _os.remove(tmp)
        self._data = data if data.ndim == 1 else data.reshape(1, -1)
    def __getitem__(self, idx):
        class _NDWrap:
            def __init__(self, d): self._d = d
            def asnumpy(self): return self._d
            def __getattr__(self, n): return getattr(self._d, n)
        return _NDWrap(self._data[idx])
    def __len__(self): return len(self._data)
    @property
    def shape(self): return self._data.shape
    def asnumpy(self): return self._data
""")

    with open(os.path.join(pkg, "ndarray.py"), "w") as f:
        f.write("def cpu(idx=0): return idx\n")

    with open(os.path.join(pkg, "bridge.py"), "w") as f:
        f.write("def bridge_out(x): return x\n")


def check_cuda():
    """Check if CUDA is available."""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        if os.path.exists(os.path.join(LATENTSYNC_VENV, _VENV_BIN, "python")):
            r = _sp.run([os.path.join(LATENTSYNC_VENV, _VENV_BIN, "python"), "-c",
                         "import torch; print(torch.cuda.is_available())"],
                        capture_output=True, text=True, encoding="utf-8")
            return r.stdout.strip() == "True"
        return False


def _get_python():
    return os.path.join(LATENTSYNC_VENV, _VENV_BIN, "python")


def _get_base_python():
    """Find python3.12 for venv creation (decord requires <=3.12)."""
    import shutil
    for name in ("python3.12", "python3.11", "python3.10"):
        path = shutil.which(name)
        if path:
            return path
    return sys.executable  # fallback


def setup(log):
    """Clone repo and setup venv with all dependencies."""
    python = _get_python()

    # Clone repo if needed
    if not os.path.isdir(LATENTSYNC_DIR):
        log("   📦 Клонирую LatentSync...")
        _sp.run(["git", "clone", LATENTSYNC_REPO, LATENTSYNC_DIR], check=True)

    # Create venv if needed (marker file indicates completed setup)
    marker = os.path.join(LATENTSYNC_VENV, ".setup_done")
    if not os.path.exists(marker):
        # Clean up incomplete venv
        if os.path.exists(LATENTSYNC_VENV):
            import shutil
            __import__('pipeline').rmtree_safe(LATENTSYNC_VENV)

        base_py = _get_base_python()
        log(f"   📦 Создаю окружение LatentSync ({os.path.basename(base_py)})...")
        _sp.run([base_py, "-m", "venv", LATENTSYNC_VENV], check=True)
        pip = os.path.join(LATENTSYNC_VENV, _VENV_BIN, "pip")

        from pipeline import _torch_index_args
        log("   📦 Устанавливаю зависимости (torch)...")
        result = _sp.run([pip, "install"] + _torch_index_args() + [
                          "torch>=2.8.0", "torchaudio>=2.8.0", "torchvision",
                          "huggingface-hub"],
                         capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"Ошибка установки torch: {result.stderr[:500]}")

        log("   📦 Устанавливаю зависимости (ML)...")
        result = _sp.run([pip, "install",
                          "diffusers", "transformers", "accelerate",
                          "opencv-python", "mediapipe", "face-alignment",
                          "omegaconf", "einops", "soundfile", "librosa",
                          "python-speech-features", "scenedetect",
                          "ffmpeg-python", "imageio", "imageio-ffmpeg",
                          "lpips", "kornia", "insightface", "onnxruntime",
                          "numpy<2", "DeepCache"],
                         capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"Ошибка установки ML deps: {result.stderr[:500]}")

        # Install decord — try pip first, fall back to opencv shim
        r = _sp.run([pip, "install", "decord"], capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            log("   ⚠️ decord недоступен — создаю opencv shim...")
            _install_decord_shim()

        # Install LatentSync package if setup.py exists
        setup_py = os.path.join(LATENTSYNC_DIR, "setup.py")
        if os.path.exists(setup_py):
            _sp.run([pip, "install", "-e", LATENTSYNC_DIR],
                    capture_output=True, text=True, encoding="utf-8")

        # Mark setup as complete
        with open(marker, "w") as f:
            f.write("ok")
        log("   ✅ LatentSync окружение готово")

    # Download model weights if needed
    from pipeline import MODELS_DIR
    ckpt_dir = os.path.join(MODELS_DIR, "lipsync", "latentsync")
    unet_path = os.path.join(ckpt_dir, "latentsync_unet.pt")
    if not os.path.exists(unet_path):
        log("   ⬇️ Загружаю модель LatentSync...")
        os.makedirs(ckpt_dir, exist_ok=True)
        whisper_dir = os.path.join(ckpt_dir, "whisper")
        os.makedirs(whisper_dir, exist_ok=True)
        _cd = ckpt_dir.replace('\\', '/')
        dl_script = (
            f"from huggingface_hub import hf_hub_download; "
            f"hf_hub_download('ByteDance/LatentSync-1.6', 'latentsync_unet.pt', local_dir='{_cd}'); "
            f"hf_hub_download('ByteDance/LatentSync-1.6', 'whisper/tiny.pt', local_dir='{_cd}'); "
            f"print('OK')"
        )
        result = _sp.run([python, "-c", dl_script],
                         capture_output=True, text=True, encoding="utf-8", timeout=600)
        if result.returncode != 0 or "OK" not in result.stdout:
            err = result.stderr[:500] if result.stderr else result.stdout[:500]
            raise RuntimeError(f"Ошибка загрузки модели: {err}")
        log("   ✅ Модель загружена")


def process(video_path: str, audio_path: str, out_path: str, log,
            guidance_scale: float = 1.5, inference_steps: int = 20,
            **kwargs) -> str:
    """Run LatentSync lip sync on video+audio pair."""
    log("🎭 Синхронизация губ (LatentSync)...")

    if not check_cuda():
        log("   ⚠️ CUDA не обнаружена. LatentSync работает только на NVIDIA GPU. На CPU будет крайне медленно.")

    setup(log)
    _install_decord_shim()  # always refresh shim in case it was updated

    python = _get_python()
    from pipeline import MODELS_DIR
    config_path = os.path.join(LATENTSYNC_DIR, "configs", "unet", "stage2_512.yaml")
    ckpt_path = os.path.join(MODELS_DIR, "lipsync", "latentsync", "latentsync_unet.pt")

    log(f"   ⚙️ Steps: {inference_steps}, guidance: {guidance_scale}")

    cmd = [
        python, "-u", RUNNER_SCRIPT,
        "--latentsync_dir", LATENTSYNC_DIR,
        "--unet_config_path", config_path,
        "--inference_ckpt_path", ckpt_path,
        "--video_path", os.path.abspath(video_path),
        "--audio_path", os.path.abspath(audio_path),
        "--video_out_path", os.path.abspath(out_path),
        "--inference_steps", str(inference_steps),
        "--guidance_scale", str(guidance_scale),
        "--seed", "1247",
        "--temp_dir", os.path.join(LATENTSYNC_DIR, "temp"),
    ]

    env = {**os.environ, "PYTHONPATH": LATENTSYNC_DIR}
    if not check_cuda():
        env["CUDA_VISIBLE_DEVICES"] = ""

    proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE,
                     text=True, encoding="utf-8", bufsize=1, cwd=LATENTSYNC_DIR, env=env)
    # здесь читают stderr, значит вычитывать в фоне надо stdout — иначе
    # переполнится он и процесс встанет
    import threading as _th
    from pipeline import register_proc
    register_proc(proc)   # для принудительной остановки
    _th.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()

    # Read stderr for progress
    stderr_lines = []
    for line in proc.stderr:
        line = line.strip()
        if not line:
            continue
        stderr_lines.append(line)
        if "%" in line or "step" in line.lower() or "frame" in line.lower() or "processing" in line.lower() or line.startswith("[") or "Loading" in line or "Starting" in line or "device" in line.lower():
            log(f"   {line[:120]}")

    proc.wait()

    if proc.returncode != 0:
        err = "\n".join(stderr_lines[-10:])
        raise RuntimeError(f"LatentSync ошибка (код {proc.returncode}):\n{err[:500]}")

    if not os.path.exists(out_path):
        raise RuntimeError("LatentSync не создал выходной файл")

    log("✅ Синхронизация губ завершена")
    return out_path
