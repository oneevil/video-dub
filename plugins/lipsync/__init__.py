"""Lip Sync Plugin System. Each .py file in this directory is a plugin.
Each plugin must define:
  ENGINES: dict  -- {"engine_id": "Display Name", ...}
  def process(video_path, audio_path, out_path, log, **kwargs) -> str  -- returns output video path
Optional:
  def check_available() -> bool  -- return False to hide engine (e.g. CUDA-only)
  def setup(log) -> None  -- install dependencies
"""
import os, importlib, importlib.util


def discover_plugins():
    """Discover and load all lip sync plugins. Returns (engines, plugins_map)."""
    engines = {}
    plugins = {}

    plugin_dir = os.path.dirname(__file__)
    for fname in sorted(os.listdir(plugin_dir)):
        if not fname.endswith('.py') or fname.startswith('_'):
            continue
        mod_name = fname[:-3]
        try:
            spec = importlib.util.spec_from_file_location(
                f"plugins.lipsync.{mod_name}",
                os.path.join(plugin_dir, fname)
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            if not hasattr(mod, 'ENGINES'):
                continue

            if hasattr(mod, 'check_available') and not mod.check_available():
                continue

            for eid, label in mod.ENGINES.items():
                engines[eid] = label
                plugins[eid] = mod
        except Exception as e:
            print(f"Warning: failed to load lipsync plugin {fname}: {e}")

    return engines, plugins
