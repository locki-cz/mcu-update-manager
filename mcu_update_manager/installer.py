from __future__ import annotations

from pathlib import Path
from typing import Any
import subprocess


KATAPULT_REPO = "https://github.com/Arksine/katapult.git"


def katapult_install_plan(path: str) -> dict[str, Any]:
    target = Path(path).expanduser()
    return {
        "action": "install_katapult",
        "repo": KATAPULT_REPO,
        "path": str(target),
        "commands": [
            f"git clone {KATAPULT_REPO} {target}",
        ],
        "requires_confirmation": True,
    }


def install_katapult(path: str) -> dict[str, Any]:
    plan = katapult_install_plan(path)
    target = Path(path).expanduser()

    if target.exists():
        return {
            **plan,
            "executed": False,
            "status": "already_exists",
            "message": f"Katapult path already exists: {target}",
        }

    result = subprocess.run(
        ["git", "clone", KATAPULT_REPO, str(target)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )

    return {
        **plan,
        "executed": True,
        "status": "ok" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
