"""Transcribe Plugin System. Each .py file in this directory is a plugin.
Each plugin must define:
  ENGINES: dict  -- {"engine_id": "Display Name", ...}
  def transcribe(audio_path, out_dir, model_name, log, source_language="", on_segment=None, **kwargs) -> list[dict]
Optional:
  MODELS: list  -- available model sizes
  DOWNLOAD_ENGINES: list[dict] -- [{"value": "...", "label": "..."}] for download UI
  def check_available() -> bool  -- return False to hide engine
  def download_model(engine, model, log) -> generator  -- for model download SSE
  def list_downloaded_models(models_dir) -> list[dict]  -- for downloaded models listing
"""
import os, importlib, importlib.util


def discover_plugins():
    """Discover and load all transcribe plugins. Returns (engines, plugins_map)."""
    engines = {}  # engine_id -> display_name
    plugins = {}  # engine_id -> module

    plugin_dir = os.path.dirname(__file__)
    for fname in sorted(os.listdir(plugin_dir)):
        if not fname.endswith('.py') or fname.startswith('_'):
            continue
        mod_name = fname[:-3]
        try:
            spec = importlib.util.spec_from_file_location(
                f"plugins.transcribe.{mod_name}",
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
            print(f"Warning: failed to load transcribe plugin {fname}: {e}")

    return engines, plugins


def get_download_engines():
    """Get list of downloadable models from all transcribe plugins."""
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
