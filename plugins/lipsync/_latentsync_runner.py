"""Standalone LatentSync inference runner.

Replaces the original scripts/inference.py with proper CPU/CUDA device handling.
Uses only public APIs from LatentSync — no monkey-patching of internals.

Usage:
  python latentsync_runner.py \
    --latentsync_dir /path/to/.latentsync \
    --unet_config_path configs/unet/stage2_512.yaml \
    --inference_ckpt_path /path/to/latentsync_unet.pt \
    --video_path input.mp4 --audio_path audio.wav \
    --video_out_path output.mp4
"""

import argparse
import math
import os
import shutil
import sys

import numpy as np
import soundfile as sf
import torch
import tqdm


def detect_device():
    """Select best available device."""
    if torch.cuda.is_available():
        return "cuda"
    # MPS is not supported by LatentSync ops (kornia, etc.)
    return "cpu"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--latentsync_dir", type=str, required=True)
    parser.add_argument("--unet_config_path", type=str, required=True)
    parser.add_argument("--inference_ckpt_path", type=str, required=True)
    parser.add_argument("--video_path", type=str, required=True)
    parser.add_argument("--audio_path", type=str, required=True)
    parser.add_argument("--video_out_path", type=str, required=True)
    parser.add_argument("--inference_steps", type=int, default=20)
    parser.add_argument("--guidance_scale", type=float, default=1.0)
    parser.add_argument("--temp_dir", type=str, default="temp")
    parser.add_argument("--seed", type=int, default=1247)
    args = parser.parse_args()

    # Add LatentSync to path and remove this script's directory
    # (plugins/lipsync/latentsync.py would shadow the real latentsync package)
    ls_dir = args.latentsync_dir
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path = [p for p in sys.path if os.path.abspath(p) != script_dir]
    if ls_dir not in sys.path:
        sys.path.insert(0, ls_dir)

    device = detect_device()
    print(f"Using device: {device}", flush=True, file=sys.stderr)

    # Prevent multiprocessing segfault on macOS (fork + GPU = crash)
    import multiprocessing
    if sys.platform == "darwin":
        multiprocessing.set_start_method("spawn", force=True)
    # Limit torch threads on CPU to avoid contention
    if device != "cuda":
        torch.set_num_threads(1)

    # --- Force ONNX Runtime to CPU if no CUDA ---
    if device != "cuda":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        try:
            import onnxruntime as ort
            # Set default providers before any session is created
            ort.set_default_logger_severity(3)  # suppress warnings
            if hasattr(ort, 'set_default_execution_provider'):
                ort.set_default_execution_provider("CPUExecutionProvider")
            _orig_sess_init = ort.InferenceSession.__init__
            def _cpu_sess_init(self, *a, **kw):
                # Override both positional and keyword providers
                if len(a) > 1:
                    a = (a[0],) + (["CPUExecutionProvider"],) + a[2:]
                kw.pop("providers", None)
                kw["providers"] = ["CPUExecutionProvider"]
                return _orig_sess_init(self, *a, **kw)
            ort.InferenceSession.__init__ = _cpu_sess_init
        except ImportError:
            pass

    # --- Import LatentSync components ---
    from omegaconf import OmegaConf
    from diffusers import AutoencoderKL, DDIMScheduler
    from accelerate.utils import set_seed
    from latentsync.models.unet import UNet3DConditionModel
    from latentsync.pipelines.lipsync_pipeline import LipsyncPipeline
    from latentsync.whisper.audio2feature import Audio2Feature
    from latentsync.utils.image_processor import ImageProcessor, load_fixed_mask
    from latentsync.utils.face_detector import FaceDetector
    from latentsync.utils.util import read_video, read_audio, write_video, check_ffmpeg_installed

    # --- Patch for CPU ---
    if device != "cuda":
        from latentsync.utils.affine_transform import AlignRestore

        # AlignRestore defaults to float16 which causes segfault in kornia on CPU
        _orig_ar_init = AlignRestore.__init__
        def _cpu_ar_init(self, align_points=3, resolution=256, device="cpu", dtype=torch.float32):
            _orig_ar_init(self, align_points=align_points, resolution=resolution,
                          device="cpu", dtype=torch.float32)
        AlignRestore.__init__ = _cpu_ar_init

        # FaceDetector: use CPUExecutionProvider for insightface
        def _cpu_fd_init(self, device="cpu"):
            from insightface.app import FaceAnalysis
            self.app = FaceAnalysis(
                allowed_modules=["detection", "landmark_2d_106"],
                root=os.path.join(ls_dir, "checkpoints", "auxiliary"),
                providers=["CPUExecutionProvider"],
            )
            self.app.prepare(ctx_id=-1, det_size=(512, 512))
        FaceDetector.__init__ = _cpu_fd_init

        # ImageProcessor: always create FaceDetector (original skips it on CPU)
        _orig_ip_init = ImageProcessor.__init__
        def _cpu_ip_init(self, resolution=512, device="cpu", mask_image=None):
            _orig_ip_init(self, resolution=resolution, device="cpu", mask_image=mask_image)
            if self.face_detector is None:
                self.face_detector = FaceDetector(device="cpu")
        ImageProcessor.__init__ = _cpu_ip_init

    # --- Validate inputs ---
    if not os.path.exists(args.video_path):
        raise RuntimeError(f"Video not found: {args.video_path}")
    if not os.path.exists(args.audio_path):
        raise RuntimeError(f"Audio not found: {args.audio_path}")

    check_ffmpeg_installed()

    # --- Load config ---
    config = OmegaConf.load(args.unet_config_path)

    # --- Determine dtype ---
    if device == "cuda":
        is_fp16 = torch.cuda.get_device_capability()[0] > 7
    else:
        is_fp16 = False
    dtype = torch.float16 if is_fp16 else torch.float32

    print(f"Input video: {args.video_path}", flush=True, file=sys.stderr)
    print(f"Input audio: {args.audio_path}", flush=True, file=sys.stderr)
    print(f"Checkpoint: {args.inference_ckpt_path}", flush=True, file=sys.stderr)
    print(f"dtype: {dtype}", flush=True, file=sys.stderr)

    # --- Build pipeline components ---
    print("[1/5] Loading scheduler...", flush=True, file=sys.stderr)
    scheduler = DDIMScheduler.from_pretrained(os.path.join(ls_dir, "configs"))

    # Whisper audio encoder
    print("[2/5] Loading whisper audio encoder...", flush=True, file=sys.stderr)
    if config.model.cross_attention_dim == 768:
        whisper_model_path = os.path.join(ls_dir, "checkpoints", "whisper", "small.pt")
    elif config.model.cross_attention_dim == 384:
        whisper_model_path = os.path.join(ls_dir, "checkpoints", "whisper", "tiny.pt")
    else:
        raise NotImplementedError("cross_attention_dim must be 768 or 384")

    audio_encoder = Audio2Feature(
        model_path=whisper_model_path,
        device=device,
        num_frames=config.data.num_frames,
        audio_feat_length=config.data.audio_feat_length,
    )

    # VAE
    print("[3/5] Loading VAE...", flush=True, file=sys.stderr)
    vae = AutoencoderKL.from_pretrained("stabilityai/sd-vae-ft-mse", torch_dtype=dtype)
    vae.config.scaling_factor = 0.18215
    vae.config.shift_factor = 0

    # UNet
    print("[4/5] Loading UNet...", flush=True, file=sys.stderr)
    unet, _ = UNet3DConditionModel.from_pretrained(
        OmegaConf.to_container(config.model),
        args.inference_ckpt_path,
        device="cpu",
    )
    unet = unet.to(dtype=dtype)

    # --- Assemble pipeline ---
    print("[5/5] Assembling pipeline...", flush=True, file=sys.stderr)
    pipeline = LipsyncPipeline(
        vae=vae,
        audio_encoder=audio_encoder,
        unet=unet,
        scheduler=scheduler,
    ).to(device)

    # --- Seed ---
    if args.seed != -1:
        set_seed(args.seed)
    else:
        torch.seed()
    print(f"Seed: {torch.initial_seed()}", flush=True, file=sys.stderr)

    # --- Run inference ---
    print("Starting inference...", flush=True, file=sys.stderr)
    pipeline(
        video_path=args.video_path,
        audio_path=args.audio_path,
        video_out_path=args.video_out_path,
        num_frames=config.data.num_frames,
        num_inference_steps=args.inference_steps,
        guidance_scale=args.guidance_scale,
        weight_dtype=dtype,
        width=config.data.resolution,
        height=config.data.resolution,
        mask_image_path=os.path.join(ls_dir, config.data.mask_image_path),
        temp_dir=args.temp_dir,
    )

    print("Inference complete.", flush=True, file=sys.stderr)


if __name__ == "__main__":
    main()
