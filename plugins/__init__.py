"""
Operation Zahnrad — Plugin Auto-Discovery System
Scans plugins/ directory and registers tools with the MCP server.
"""

import importlib.util
import json
import logging
from pathlib import Path

logger = logging.getLogger("plugins")

PLUGINS_DIR = Path(__file__).parent
CONFIG_PATH = PLUGINS_DIR.parent / "config" / "plugins.json"


def _load_config() -> dict:
    """Load plugin enable/disable config."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Plugin config load failed: %s", e)
    return {}


def discover_and_register(app, deps: dict) -> list:
    """
    Scan plugins/*.py, import valid plugins, register tools.

    Args:
        app: FastMCP instance
        deps: Injected dependencies dict (get_db, siliconflow config, etc.)

    Returns list of loaded plugin names.
    """
    config = _load_config()
    loaded = []

    for py_file in sorted(PLUGINS_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module_name = py_file.stem

        # Check config — default enabled if not in config
        plugin_conf = config.get(module_name, {})
        if not plugin_conf.get("enabled", True):
            logger.info("Plugin skipped (disabled): %s", module_name)
            continue

        try:
            spec = importlib.util.spec_from_file_location(f"plugins.{module_name}", py_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            meta = getattr(module, "__plugin_meta__", None)
            if not meta or not isinstance(meta, dict):
                logger.warning("Plugin '%s' has no __plugin_meta__, skipping", module_name)
                continue

            register_fn = getattr(module, "register_tools", None)
            if not callable(register_fn):
                logger.warning("Plugin '%s' has no register_tools(), skipping", module_name)
                continue

            register_fn(app, deps)
            name = meta.get("name", module_name)
            version = meta.get("version", "?")
            logger.info("Plugin loaded: %s v%s -- %s", name, version, meta.get("description", ""))
            loaded.append(name)

        except Exception as e:
            logger.error("Plugin failed: %s -- %s", module_name, e, exc_info=True)

    return loaded
