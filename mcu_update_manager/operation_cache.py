from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import uuid

from .io_utils import atomic_write_json


TERMINAL_STATUSES = {"ok", "failed", "blocked", "interrupted", "cancelled"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_operation(action: str, device_id: str | None = None) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "schema_version": 1,
        "job_id": uuid.uuid4().hex,
        "action": action,
        "device_id": device_id,
        "status": "queued",
        "started_at": timestamp,
        "updated_at": timestamp,
        "completed_at": None,
        "result": None,
        "error": None,
    }


def read_operation(path: str | Path) -> dict[str, Any] | None:
    source = Path(path)
    if not source.exists():
        return None
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_operation(path: str | Path, operation: dict[str, Any]) -> None:
    operation["updated_at"] = now_iso()
    atomic_write_json(Path(path), operation)


def finish_operation(
    path: str | Path,
    operation: dict[str, Any],
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    operation["status"] = status
    operation["result"] = result
    operation["error"] = error
    operation["completed_at"] = now_iso()
    write_operation(path, operation)
    return operation


def mark_interrupted(path: str | Path) -> dict[str, Any] | None:
    operation = read_operation(path)
    if not operation or operation.get("status") not in {"queued", "running"}:
        return operation
    return finish_operation(
        path,
        operation,
        status="interrupted",
        error="Moonraker restarted while this operation was running. Check the device state and retry safely.",
    )


def is_active(operation: dict[str, Any] | None) -> bool:
    return bool(operation and operation.get("status") not in TERMINAL_STATUSES)
