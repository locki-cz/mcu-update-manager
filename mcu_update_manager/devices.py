from __future__ import annotations

from pathlib import Path
from typing import Any
import json


def load_devices(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {"devices": []}

    file = Path(path).expanduser()
    if not file.exists():
        return {"devices": []}

    try:
        import yaml  # type: ignore

        return yaml.safe_load(file.read_text(encoding="utf-8"))
    except ModuleNotFoundError:
        return parse_devices_yaml(file.read_text(encoding="utf-8"))


def devices_by_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(device.get("id")): dict(device) for device in data.get("devices", []) if device.get("id")}


def apply_confirmed_device(device: dict[str, Any], confirmed: dict[str, Any] | None) -> None:
    if not confirmed:
        device["confirmation_status"] = "needs_confirmation"
        device["confirmed_profile"] = None
        device["confirmed_by_user"] = False
        return

    profile = confirmed.get("confirmed_profile")
    device["confirmed_profile"] = profile
    device["confirmed_by_user"] = bool(confirmed.get("confirmed_by_user", True))
    device["confirmation_status"] = "confirmed" if profile else "needs_confirmation"
    device["confirmed_device"] = confirmed


def confirm_device(
    devices_path: str | Path,
    discovery_path: str | Path,
    device_id: str,
    profile_id: str,
) -> dict[str, Any]:
    discovery = json.loads(Path(discovery_path).read_text(encoding="utf-8"))
    discovered_devices = {device["id"]: device for device in discovery.get("devices", [])}
    if device_id not in discovered_devices:
        raise ValueError(f"Device not found in discovery: {device_id}")

    discovered = discovered_devices[device_id]
    data = load_devices(devices_path)
    existing = devices_by_id(data)
    existing[device_id] = clean_empty_values(
        {
            "id": device_id,
            "mcu_section": discovered.get("mcu_section"),
            "transport": discovered.get("transport"),
            "can_interface": discovered.get("can_interface"),
            "canbus_uuid": discovered.get("canbus_uuid"),
            "serial": discovered.get("serial"),
            "detected_chip": discovered.get("detected_chip"),
            "confirmed_profile": profile_id,
            "confirmed_by_user": True,
        }
    )

    output = {"devices": [clean_empty_values(existing[key]) for key in sorted(existing)]}
    Path(devices_path).write_text(dump_devices_yaml(output), encoding="utf-8")
    return output


def clean_empty_values(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in (None, "", [])}


def dump_devices_yaml(data: dict[str, Any]) -> str:
    lines = ["devices:"]
    for device in data.get("devices", []):
        lines.append(f"  - id: {device['id']}")
        for key, value in device.items():
            if key == "id":
                continue
            if isinstance(value, bool):
                value_text = "true" if value else "false"
            else:
                value_text = str(value)
            lines.append(f"    {key}: {value_text}")

    return "\n".join(lines) + "\n"


def parse_devices_yaml(text: str) -> dict[str, Any]:
    devices: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        line = raw_line.strip()
        if line == "devices:":
            continue

        if line.startswith("- "):
            if current:
                devices.append(current)
            current = {}
            key, value = split_key_value(line[2:])
            current[key] = parse_scalar(value)
            continue

        if current is not None and ":" in line:
            key, value = split_key_value(line)
            current[key] = parse_scalar(value)

    if current:
        devices.append(current)

    return {"devices": devices}


def split_key_value(text: str) -> tuple[str, str]:
    key, _, value = text.partition(":")
    return key.strip(), value.strip()


def parse_scalar(value: str) -> Any:
    if value == "true":
        return True
    if value == "false":
        return False
    return value
