"""
Standalone entry point for Nuitka compilation.

This script sits in the 'optimized/' parent directory so that
'from optimized.<module>' imports resolve naturally. Nuitka's
--follow-imports will pull in all sub-modules automatically.
"""
import sys
import os

# Ensure the parent of 'optimized/' package is on sys.path
_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

from optimized.main import launch_desktop_app

if __name__ == "__main__":
    launch_desktop_app()
