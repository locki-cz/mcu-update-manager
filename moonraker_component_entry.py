"""Moonraker component entry point for an MCU Update Manager checkout."""

from pathlib import Path
import sys


REPO_PATH = Path.home() / "mcu-update-manager"
repo = str(REPO_PATH)
if repo not in sys.path:
    sys.path.insert(0, repo)

from mcu_update_manager.moonraker_component import load_component  # noqa: E402, F401
