"""TTS Plugin System. Each .py file in this directory is a plugin.
Each plugin must define:
  ENGINES: dict  -- {"engine_id": "Display Name", ...}
  def synthesize(subtitles, out_dir, log, voice="", voice_wav="", voice_text="", seed=-1, temperature=0.7, on_segment=None) -> list[dict]
Optional:
  MODELS: dict  -- {"engine_id": "hf_model_path", ...} for model download
  DOWNLOAD_ENGINES: list[dict] -- [{"value": "...", "label": "..."}] for download UI
  def check_available() -> bool  -- return False to hide engine
  def download_model(engine, model, log, models_dir) -> generator  -- for model download SSE
"""
import os, importlib, importlib.util, sys


def discover_plugins():
    """Discover and load all TTS plugins. Returns (engines, plugins_map)."""
    engines = {}  # engine_id -> display_name
    plugins = {}  # engine_id -> module

    plugin_dir = os.path.dirname(__file__)
    for fname in sorted(os.listdir(plugin_dir)):
        if not fname.endswith('.py') or fname.startswith('_'):
            continue
        mod_name = fname[:-3]
        try:
            spec = importlib.util.spec_from_file_location(
                f"plugins.tts.{mod_name}",
                os.path.join(plugin_dir, fname)
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            if not hasattr(mod, 'ENGINES'):
                continue

            # Check availability
            if hasattr(mod, 'check_available') and not mod.check_available():
                continue

            for eid, label in mod.ENGINES.items():
                engines[eid] = label
                plugins[eid] = mod
        except Exception as e:
            print(f"Warning: failed to load TTS plugin {fname}: {e}")

    return engines, plugins


def get_download_engines():
    """Get list of downloadable models from all plugins."""
    result = []
    _, plugins = discover_plugins()
    seen = set()
    for mod in plugins.values():
        if mod in seen:
            continue
        seen.add(mod)
        if hasattr(mod, 'DOWNLOAD_ENGINES'):
            result.extend(mod.DOWNLOAD_ENGINES)
    return result
