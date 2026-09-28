from __future__ import annotations

from pathlib import Path
from typing import Any


def check_katapult(katapult_path: str | None) -> dict[str, Any]:
    if not katapult_path:
        return {
            "available": False,
            "status": "not_configured",
            "message": "Katapult path is not configured. CAN bootloader scan is disabled.",
            "install_hint": katapult_install_hint(),
        }

    root = Path(katapult_path).expanduser()
    flashtool = root / "scripts" / "flashtool.py"

    if not root.exists():
        return {
            "available": False,
            "status": "missing_path",
            "path": str(root),
            "message": f"Katapult directory does not exist: {root}",
            "install_hint": katapult_install_hint(),
        }

    if not flashtool.exists():
        return {
            "available": False,
            "status": "missing_flashtool",
            "path": str(root),
            "flashtool": str(flashtool),
            "message": f"Katapult flashtool.py was not found: {flashtool}",
            "install_hint": katapult_install_hint(),
        }

    return {
        "available": True,
        "status": "ok",
        "path": str(root),
        "flashtool": str(flashtool),
        "message": "Katapult flashtool.py is available.",
    }


def katapult_install_hint() -> dict[str, Any]:
    return {
        "action": "install_katapult",
        "default_path": "~/katapult",
        "commands": [
            "git clone https://github.com/Arksine/katapult.git ~/katapult",
            "cd ~/katapult && make menuconfig",
        ],
        "note": "The install action should only clone Katapult automatically. Board-specific Katapult firmware build/flash still needs an explicit profile and user confirmation.",
    }
