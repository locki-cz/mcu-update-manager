from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .discovery import DiscoverOptions, discover
from .profiles import load_profiles


def verify_device(
    device_id: str,
    printer_cfg: str,
    serial_dir: str,
    profile_paths: list[str | Path],
    can_interface: str,
    katapult_path: str | None,
    klipper_path: str | None,
    kalico_path: str | None,
    moonraker_url: str | None,
    devices_path: str | None,
    artifact_manifest: str | Path | None = None,
    printer_objects_json: str | None = None,
    can_query_output: str | None = None,
) -> dict[str, Any]:
    profiles = load_profiles(profile_paths)
    discovery = discover(
        DiscoverOptions(
            printer_cfg=printer_cfg,
            serial_dir=serial_dir,
            can_interface=can_interface,
            katapult_path=katapult_path,
            klipper_path=klipper_path,
            kalico_path=kalico_path,
            moonraker_url=moonraker_url,
            printer_objects_json=printer_objects_json,
            devices_path=devices_path,
            can_query_output=can_query_output,
        ),
        profiles=profiles,
    )

    devices = {device["id"]: device for device in discovery.get("devices", [])}
    if device_id not in devices:
        raise ValueError(f"Device not found in discovery: {device_id}")

    device = devices[device_id]
    manifest = load_manifest(artifact_manifest)
    checks = runtime_checks(device, manifest)
    failed = [name for name, ok in checks.items() if not ok]

    return {
        "action": "verify_device",
        "device": {
            "id": device.get("id"),
            "mcu_section": device.get("mcu_section"),
            "transport": device.get("transport"),
            "can_interface": device.get("can_interface"),
            "canbus_uuid": device.get("canbus_uuid"),
            "detected_chip": device.get("detected_chip"),
            "firmware_version": device.get("firmware_version"),
            "runtime_app": device.get("runtime_app"),
            "confirmed_profile": device.get("confirmed_profile"),
            "confirmation_status": device.get("confirmation_status"),
        },
        "artifact": artifact_summary(manifest),
        "runtime_source": discovery.get("discovery", {}).get("runtime_source"),
        "checks": checks,
        "failed": failed,
        "status": "ok" if not failed else "failed",
        "warnings": discovery.get("discovery", {}).get("warnings", []),
    }


def load_manifest(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    manifest_path = Path(path).expanduser()
    if not manifest_path.exists():
        raise ValueError(f"Artifact manifest does not exist: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def runtime_checks(device: dict[str, Any], manifest: dict[str, Any] | None) -> dict[str, bool]:
    expected_chip = manifest.get("device", {}).get("detected_chip") if manifest else None
    expected_uuid = manifest.get("device", {}).get("canbus_uuid") if manifest else None
    expected_profile = manifest.get("profile", {}).get("id") if manifest else None

    return {
        "runtime_seen": bool(device.get("firmware_version") or device.get("detected_chip")),
        "runtime_app_supported": str(device.get("runtime_app") or "Klipper").lower() in ("klipper", "kalico"),
        "chip_matches_manifest": True if not expected_chip else device.get("detected_chip") == expected_chip,
        "uuid_matches_manifest": True if not expected_uuid else device.get("canbus_uuid") == expected_uuid,
        "profile_matches_manifest": True if not expected_profile else device.get("confirmed_profile") == expected_profile,
    }


def artifact_summary(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    if not manifest:
        return None
    return {
        "path": manifest.get("artifact", {}).get("path"),
        "sha256": manifest.get("artifact", {}).get("sha256"),
        "size": manifest.get("artifact", {}).get("size"),
        "firmware_source": manifest.get("firmware_source"),
        "profile": manifest.get("profile"),
    }
