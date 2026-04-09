"""Translate Plugin System. Each .py file in this directory is a plugin.
Each plugin must define:
  ENGINES: dict  -- {"engine_id": "Display Name", ...}
  def translate(subtitles, target_lang, out_dir, log, on_chunk=None, **kwargs) -> list[dict]
Optional:
  def check_available() -> bool  -- return False to hide engine
  API_KEY_ENV: str  -- environment variable name for API key (e.g. "ANTHROPIC_API_KEY")
  NEEDS_BASE_URL: bool  -- whether engine needs a base_url setting
  NEEDS_MODEL: bool  -- whether engine needs a model setting
  MODELS: list[dict]  -- [{"id": "model-id", "name": "Display Name"}] for model selector
"""
import os, importlib, importlib.util


def discover_plugins():
    """Discover and load all translate plugins. Returns (engines, plugins_map)."""
    engines = {}  # engine_id -> display_name
    plugins = {}  # engine_id -> module

    plugin_dir = os.path.dirname(__file__)
    for fname in sorted(os.listdir(plugin_dir)):
        if not fname.endswith('.py') or fname.startswith('_'):
            continue
        mod_name = fname[:-3]
        try:
            spec = importlib.util.spec_from_file_location(
                f"plugins.translate.{mod_name}",
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
            print(f"Warning: failed to load translate plugin {fname}: {e}")

    return engines, plugins
