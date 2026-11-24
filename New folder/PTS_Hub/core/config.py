"""
Application configuration, global constants, and asset resolution for PTS Hub.
"""
import json
import logging
import os
import sys
from typing import Any, Dict, Optional, Tuple

APP_NAME = "PTS Hub"
APP_VERSION = "v1.3"
APP_ID = "PTS_Hub"

# Directory paths
CORE_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLE_ROOT = os.path.dirname(CORE_DIR)


def is_frozen_app() -> bool:
    """Return True when running inside a Nuitka-compiled or PyInstaller-frozen app."""
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def get_app_root() -> str:
    """Resolve the persistent application root directory beside external assets, logs, and cache.

    Handles:
    - Nuitka --onefile: ``__compiled__.containing_dir`` (folder holding the outer .exe).
    - Nuitka --standalone: folder containing ``sys.executable``.
    - PyInstaller (--onedir / --onefile): folder containing ``sys.executable``
      (avoids ``_internal`` / ``_MEI*`` temp directories).
    - Unfrozen / Nuitka accelerated source execution: ``BUNDLE_ROOT``.
    """
    compiled_info = globals().get("__compiled__")
    if compiled_info is not None:
        if getattr(compiled_info, "onefile", False):
            containing_dir = getattr(compiled_info, "containing_dir", "")
            if containing_dir:
                return os.path.abspath(containing_dir)
        if getattr(compiled_info, "standalone", False):
            return os.path.dirname(os.path.abspath(sys.executable))
        return BUNDLE_ROOT

    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))

    return BUNDLE_ROOT


HUB_ROOT = get_app_root()
ASSETS_DIR = os.path.join(HUB_ROOT, "assets")


def get_asset_path(filename: str) -> str:
    """Resolve absolute path to a resource/asset file across environments.

    Search order:
    1. External assets directory beside app/exe: <app_root>/assets/<filename>
    2. App/exe root directory:                   <app_root>/<filename>
    3. Executable / script launch directory:     <launch_dir>/assets/<filename>, <launch_dir>/<filename>
    4. Parent of launch directory:               <parent_launch>/assets/<filename>, <parent_launch>/<filename>
    5. Bundled fallback root (e.g. _internal):   <BUNDLE_ROOT>/assets/<filename>, <BUNDLE_ROOT>/<filename>
    """
    app_root = get_app_root()
    app_assets_dir = os.path.join(app_root, "assets")
    candidates = [
        os.path.join(app_assets_dir, filename),
        os.path.join(app_root, filename),
    ]

    if sys.argv and sys.argv[0]:
        launch_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        candidates.extend([
            os.path.join(launch_dir, "assets", filename),
            os.path.join(launch_dir, filename),
            # Also check parent directory of launch dir (e.g. if invoked from a subpackage)
            os.path.join(os.path.dirname(launch_dir), "assets", filename),
            os.path.join(os.path.dirname(launch_dir), filename),
        ])

    if os.path.abspath(BUNDLE_ROOT) != os.path.abspath(app_root):
        candidates.extend([
            os.path.join(BUNDLE_ROOT, "assets", filename),
            os.path.join(BUNDLE_ROOT, filename),
        ])

    seen = set()
    for path in candidates:
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(path):
            return os.path.abspath(path)

    # Fallback to default expected path in external assets directory
    return os.path.abspath(os.path.join(app_assets_dir, filename))


ICON_PATH = get_asset_path("FTDC_Checker_icon.ico")


# ── Settings Configuration (Loaded from settings.json) ──────────────────────
APP_MODE_PRODUCTION = "production"
APP_MODE_DEVELOPER = "developer"

_SETTINGS_CACHE: Optional[Dict[str, Any]] = None

DEFAULT_SETTINGS: Dict[str, Any] = {
    "mes_url": "http://mth-vm-eaprd1/MPHL/getlot/mes.asmx/GetLotByLotId",
    "ftdc_fail_primary_url": "http://mph-vm-mphl2prd:8080/ftdc/filter.php",
    "ftdc_fail_backup_url": "http://mph-vm-mitprod.mchp-main.com:8080/ftdc/filter.php",
    "local_dest_base": r"C:\FTDC",
    "ppo_base_path": r"\\chip\PPOonline",
    "ppo_attach_timeout_sec": 60,
    "app_mode": APP_MODE_PRODUCTION,
    # Folder holding the Automatic FTDC Checker Help presentation (opened by the Help button).
    "help_dir": r"\\acpnetapp02\tsti\backend\data\Shared_Engineering_Directory\Automatic_FTDC_Checker",
}


def load_settings() -> Dict[str, Any]:
    """Load settings from settings.json, falling back to defaults if missing or invalid."""
    global _SETTINGS_CACHE
    if _SETTINGS_CACHE is not None:
        return _SETTINGS_CACHE

    settings_path = get_asset_path("settings.json")
    loaded: Dict[str, Any] = dict(DEFAULT_SETTINGS)

    if os.path.isfile(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    loaded.update(data)
        except Exception as exc:
            logging.warning("Failed to load settings from '%s': %s", settings_path, exc)

    _SETTINGS_CACHE = loaded
    return _SETTINGS_CACHE


def reload_settings() -> Dict[str, Any]:
    """Force-reload settings from disk."""
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = None
    return load_settings()


def get_app_mode() -> str:
    """Return active operating mode ('production' or 'developer')."""
    raw = str(load_settings().get("app_mode") or APP_MODE_PRODUCTION).strip().lower()
    return APP_MODE_DEVELOPER if raw == APP_MODE_DEVELOPER else APP_MODE_PRODUCTION


def is_developer_mode() -> bool:
    """Return True when Developer Mode is active."""
    return get_app_mode() == APP_MODE_DEVELOPER


def set_app_mode(mode: str, persist: bool = True) -> str:
    """Set the operating mode ('production' or 'developer'), updating cache and settings.json."""
    raw = str(mode or "").strip().lower()
    if raw in (APP_MODE_PRODUCTION, "prod"):
        normalized = APP_MODE_PRODUCTION
    elif raw in (APP_MODE_DEVELOPER, "dev"):
        normalized = APP_MODE_DEVELOPER
    else:
        raise ValueError(f"Invalid app mode '{mode}'. Expected 'production' or 'developer'.")

    loaded = load_settings()
    loaded["app_mode"] = normalized

    if persist:
        settings_path = get_asset_path("settings.json")
        data: Dict[str, Any] = {}
        try:
            if os.path.isfile(settings_path):
                with open(settings_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    if isinstance(existing, dict):
                        data = existing
        except (OSError, ValueError) as exc:
            logging.warning("Failed to read settings from '%s' before saving app_mode: %s", settings_path, exc)
            data = dict(loaded)
        if not data:
            data = dict(loaded)
        data["app_mode"] = normalized
        tmp_path = settings_path + ".tmp"
        try:
            parent_dir = os.path.dirname(settings_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp_path, settings_path)
        except OSError as exc:
            logging.warning("Failed to persist app_mode to '%s': %s", settings_path, exc)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    return normalized


def get_mes_url() -> str:
    return str(load_settings().get("mes_url") or DEFAULT_SETTINGS["mes_url"]).strip()


def get_ftdc_fail_primary_url() -> str:
    return str(load_settings().get("ftdc_fail_primary_url") or DEFAULT_SETTINGS["ftdc_fail_primary_url"]).strip()


def get_ftdc_fail_backup_url() -> str:
    return str(load_settings().get("ftdc_fail_backup_url") or DEFAULT_SETTINGS["ftdc_fail_backup_url"]).strip()


def get_ftdc_fail_urls() -> Tuple[str, str]:
    return (get_ftdc_fail_primary_url(), get_ftdc_fail_backup_url())


def get_local_dest_base() -> str:
    return str(load_settings().get("local_dest_base") or DEFAULT_SETTINGS["local_dest_base"]).strip()


def get_ppo_base_path() -> str:
    return str(load_settings().get("ppo_base_path") or DEFAULT_SETTINGS["ppo_base_path"]).strip()


def get_ppo_attach_timeout_sec() -> float:
    """Return timeout (seconds) for auto-attaching results to PPO."""
    raw = load_settings().get("ppo_attach_timeout_sec", DEFAULT_SETTINGS["ppo_attach_timeout_sec"])
    try:
        val = float(raw)
        if val > 0:
            return val
    except (TypeError, ValueError):
        pass
    return float(DEFAULT_SETTINGS["ppo_attach_timeout_sec"])


def get_help_dir() -> str:
    return str(load_settings().get("help_dir") or DEFAULT_SETTINGS["help_dir"]).strip()


# Module-level convenience constants (read at import time, functions provide live values)
MES_URL = get_mes_url()
FTDC_FAIL_PRIMARY_URL = get_ftdc_fail_primary_url()
FTDC_FAIL_BACKUP_URL = get_ftdc_fail_backup_url()
FTDC_FAIL_URLS = get_ftdc_fail_urls()
LOCAL_DEST_BASE = get_local_dest_base()
PPO_BASE_PATH = get_ppo_base_path()
