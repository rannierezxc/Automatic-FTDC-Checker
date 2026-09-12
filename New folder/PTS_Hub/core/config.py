"""
Application configuration, global constants, and asset resolution for PTS Hub.
"""
import os
import sys

APP_NAME = "PTS Hub"
APP_VERSION = "v1.3"
APP_ID = "PTS_Hub"

# Directory paths
CORE_DIR = os.path.dirname(os.path.abspath(__file__))
HUB_ROOT = os.path.dirname(CORE_DIR)
ASSETS_DIR = os.path.join(HUB_ROOT, "assets")
ICON_PATH = os.path.join(ASSETS_DIR, "FTDC_Checker_icon.ico")


def get_asset_path(filename: str) -> str:
    """Resolve absolute path to a resource/asset file across environments.

    Search order:
    1. Direct assets directory in PTS_Hub: PTS_Hub/assets/<filename>
    2. Root PTS_Hub folder: PTS_Hub/<filename>
    3. Executable / script launch directory: <launch_dir>/assets/<filename>
    4. Executable / script launch directory: <launch_dir>/<filename>
    """
    candidates = [
        os.path.join(ASSETS_DIR, filename),
        os.path.join(HUB_ROOT, filename),
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

    for path in candidates:
        if os.path.isfile(path):
            return os.path.abspath(path)

    # Fallback to default expected path in assets
    return os.path.join(ASSETS_DIR, filename)
