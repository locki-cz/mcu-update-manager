from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json

from .devices import devices_by_id, load_devices
from .preflight import check_katapult
from .profiles import load_profiles


def create_flash_plan(
    device_id: str,
    devices_path: str | Path,
    discovery_path: str | Path,
    profile_paths: list[str | Path],
    katapult_path: str | None,
    artifact: str | Path | None = None,
    artifact_manifest: str | Path | None = None,
) -> dict[str, Any]:
    discovery = json.loads(Path(discovery_path).read_text(encoding="utf-8"))
    discovered = {device["id"]: device for device in discovery.get("devices", [])}
    if device_id not in discovered:
        raise ValueError(f"Device not found in discovery: {device_id}")

    confirmed = devices_by_id(load_devices(devices_path)).get(device_id)
    if not confirmed:
        raise ValueError(f"Device is not confirmed in {devices_path}: {device_id}")

    profile_id = confirmed.get("confirmed_profile")
    profiles = {profile.id: profile for profile in load_profiles(profile_paths)}
    if profile_id not in profiles:
        raise ValueError(f"Profile not found: {profile_id}")

    profile = profiles[profile_id]
    flash_method = str(profile.flash.get("method") or "")
    supported_methods = {"can_katapult", "usb_katapult_or_make_flash", "klipper_make_flash_usb", "usb_make_flash"}
    if flash_method not in supported_methods:
        raise ValueError(f"Unsupported flash method for {profile_id}: {profile.flash.get('method')}")

    manifest = load_manifest(artifact_manifest)
    artifact_path = resolve_artifact_path(artifact, manifest)
    device = discovered[device_id]
    uuid = confirmed.get("canbus_uuid") or device.get("canbus_uuid")
    can_interface = confirmed.get("can_interface") or device.get("can_interface") or "can0"
    serial = resolve_serial(confirmed, device)
    if flash_method in {"can_katapult", "usb_katapult_or_make_flash"} and not uuid:
        raise ValueError(f"Device does not have a canbus_uuid: {device_id}")
    if flash_method in {"klipper_make_flash_usb", "usb_make_flash"} and not serial:
        raise ValueError(f"Device does not have a USB serial path: {device_id}")

    katapult = check_katapult(katapult_path) if flash_method not in {"klipper_make_flash_usb", "usb_make_flash"} else {
        "status": "not_required",
        "message": "Katapult is not used for direct Klipper USB flashing.",
    }
    flashtool = katapult.get("flashtool") or str(Path(katapult_path or "~/katapult").expanduser() / "scripts" / "flashtool.py")
    artifact_info = inspect_artifact(artifact_path)
    manifest_checks = check_manifest(manifest, device_id, profile_id, uuid, serial, artifact_path, artifact_info)

    if flash_method == "can_katapult":
        commands = flash_commands(flashtool, can_interface, str(uuid), artifact_path)
        steps = can_katapult_steps(flashtool, can_interface, str(uuid), artifact_path)
    elif flash_method == "usb_katapult_or_make_flash":
        commands = usb_make_flash_commands(manifest, flashtool, can_interface, str(uuid))
        steps = usb_make_flash_steps(manifest, flashtool, can_interface, str(uuid))
    else:
        commands = direct_usb_flash_commands(manifest, str(serial), profile.build, artifact_path)
        steps = direct_usb_flash_steps(manifest, str(serial), profile.build, artifact_path)

    return {
        "action": "flash_plan",
        "execute": False,
        "status": "planned",
        "device": {
            "id": device_id,
            "mcu_section": device.get("mcu_section"),
            "transport": device.get("transport"),
            "can_interface": can_interface,
            "canbus_uuid": uuid,
            "serial": serial,
            "detected_chip": device.get("detected_chip"),
        },
        "profile": {
            "id": profile.id,
            "name": profile.name,
            "flash_method": flash_method,
        },
        "firmware_source": {"path": firmware_repo_path(manifest)},
        "preflight": {
            "katapult": katapult,
            "artifact": artifact_info,
            "manifest_checks": manifest_checks,
        },
        "commands_preview": commands,
        "steps": steps,
        "notes": [
            "This command is intentionally plan-only. Flash execution should require a separate explicit confirmation.",
            "If verify_katapult does not show the target UUID as Katapult, do not flash; use the board reset fallback from the hardware guide.",
        ],
    }


def direct_usb_flash_steps(manifest: dict[str, Any] | None, serial: str, build: dict[str, Any], artifact_path: str) -> list[dict[str, Any]]:
    flash_command = usb_make_flash_command(manifest, serial, build, artifact_path)
    return [
        {
            "id": "stop_klipper",
            "label": "Stop Klipper service",
            "command": "sudo -n systemctl stop klipper",
            "reason": "Klipper must release the USB MCU before flashing.",
        },
        {
            "id": "flash_firmware",
            "label": "Enter STM32 DFU and flash firmware",
            "command": flash_command,
            "expect": "Klipper requests the internal STM32 DFU bootloader and flashes the selected firmware.",
        },
        {
            "id": "verify_usb",
            "label": "Verify USB MCU returned",
            "command": f"test -e {serial}",
            "expect": f"{serial} should reappear after flashing.",
        },
        {
            "id": "start_klipper",
            "label": "Start Klipper service",
            "command": "sudo -n systemctl start klipper",
            "expect": "Klipper starts cleanly with the updated USB MCU.",
        },
    ]


def direct_usb_flash_commands(manifest: dict[str, Any] | None, serial: str, build: dict[str, Any], artifact_path: str) -> list[str]:
    return [
        "sudo -n systemctl stop klipper",
        usb_make_flash_command(manifest, serial, build, artifact_path),
        f"test -e {serial}",
        "sudo -n systemctl start klipper",
    ]


def can_katapult_steps(flashtool: str, can_interface: str, uuid: str, artifact_path: str) -> list[dict[str, Any]]:
    return [
            {
                "id": "stop_klipper",
                "label": "Stop Klipper service",
                "command": "sudo -n systemctl stop klipper",
                "reason": "Klipper must release the CAN MCU before Katapult reset and flash.",
            },
            {
                "id": "enter_katapult",
                "label": "Reset MCU into Katapult",
                "command": f"python3 {flashtool} -i {can_interface} -r -u {uuid}",
                "expect": "The command may print Flash success, but this step only reboots the MCU.",
            },
            {
                "id": "verify_katapult",
                "label": "Verify Katapult mode",
                "command": f"python3 {flashtool} -i {can_interface} -q",
                "expect": f"UUID {uuid} should report application Katapult.",
            },
            {
                "id": "flash_firmware",
                "label": "Flash firmware artifact",
                "command": f"python3 {flashtool} -i {can_interface} -f {artifact_path} -u {uuid}",
                "expect": "Flash completes without errors.",
            },
            {
                "id": "verify_klipper",
                "label": "Verify Klipper mode",
                "command": f"python3 {flashtool} -i {can_interface} -q",
                "expect": f"UUID {uuid} should report application Klipper.",
            },
            {
                "id": "start_klipper",
                "label": "Start Klipper service",
                "command": "sudo -n systemctl start klipper",
                "expect": "Klipper starts cleanly; then run FIRMWARE_RESTART from the UI if needed.",
            },
        ]


def usb_make_flash_steps(manifest: dict[str, Any] | None, flashtool: str, can_interface: str, uuid: str) -> list[dict[str, Any]]:
    artifact_path = resolve_artifact_path(None, manifest)
    return [
        {
            "id": "stop_klipper",
            "label": "Stop Klipper service",
            "command": "sudo -n systemctl stop klipper",
            "reason": "Klipper must release the USB-CAN bridge before make flash.",
        },
        {
            "id": "enter_katapult",
            "label": "Reset MCU into Katapult",
            "command": f"python3 {flashtool} -i {can_interface} -r -u {uuid}",
            "expect": "The command reboots the USB-CAN bridge mainboard into Katapult mode.",
        },
        {
            "id": "wait_usb_katapult",
            "label": "Find usb-katapult serial",
            "command": "wait for /dev/serial/by-id/usb-katapult*",
            "expect": "A usb-katapult device should appear. Double-click reset is only the fallback if soft reset fails.",
        },
        {
            "id": "flash_firmware",
            "label": "Flash firmware over USB Katapult",
            "command": f"python3 {flashtool} -f {artifact_path} -d <usb-katapult-serial>",
            "expect": "Katapult USB flash completes without errors.",
        },
        {
            "id": "verify_klipper",
            "label": "Verify CAN UUID after reboot",
            "command": f"python3 {firmware_repo_path(manifest)}/scripts/canbus_query.py {can_interface}",
            "expect": f"UUID {uuid} should report application Klipper.",
        },
        {
            "id": "start_klipper",
            "label": "Start Klipper service",
            "command": "sudo -n systemctl start klipper",
            "expect": "Klipper starts cleanly; then run FIRMWARE_RESTART from the UI if needed.",
        },
    ]


def resolve_serial(confirmed: dict[str, Any], device: dict[str, Any]) -> str | None:
    if confirmed.get("serial"):
        return str(confirmed["serial"])
    if device.get("serial"):
        return str(device["serial"])
    for candidate in device.get("matching_serial_devices", []) or []:
        if isinstance(candidate, dict) and candidate.get("path"):
            return str(candidate["path"])
    return None


def usb_make_flash_commands(manifest: dict[str, Any] | None, flashtool: str, can_interface: str, uuid: str) -> list[str]:
    artifact_path = resolve_artifact_path(None, manifest)
    return [
        "sudo -n systemctl stop klipper",
        f"python3 {flashtool} -i {can_interface} -r -u {uuid}",
        "wait for /dev/serial/by-id/usb-katapult*",
        f"python3 {flashtool} -f {artifact_path} -d <usb-katapult-serial>",
        f"python3 {firmware_repo_path(manifest)}/scripts/canbus_query.py {can_interface}",
        "sudo -n systemctl start klipper",
    ]


def usb_make_flash_command(manifest: dict[str, Any] | None, serial: str | None, build: dict[str, Any], artifact_path: str) -> str:
    if not serial:
        raise ValueError("USB serial path is required for make flash.")
    processor = str(build.get("processor") or "").lower()
    offset = str(build.get("bootloader_offset") or "No bootloader")
    offsets = {"No bootloader": 0, "8KiB": 8192, "16KiB": 16384,
               "32KiB": 32768, "64KiB": 65536, "128KiB": 131072}
    if not processor.startswith("stm32") or offset not in offsets:
        raise ValueError("Direct USB artifact flashing requires a verified STM32 processor and bootloader offset.")
    script = Path(firmware_repo_path(manifest)) / "scripts" / "flash_usb.py"
    command = ["python3", str(script), "-t", processor, "-d", serial,
               "-s", hex(0x08000000 + offsets[offset]), artifact_path]
    return " ".join(command)


def firmware_repo_path(manifest: dict[str, Any] | None) -> str:
    return str((manifest or {}).get("firmware_source", {}).get("path") or "/home/pi/klipper")


def load_manifest(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    manifest_path = Path(path).expanduser()
    if not manifest_path.exists():
        raise ValueError(f"Firmware artifact manifest does not exist: {manifest_path}. Build firmware for this device and selected version before flashing.")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["_manifest_path"] = str(manifest_path.resolve())
    return data


def resolve_artifact_path(artifact: str | Path | None, manifest: dict[str, Any] | None) -> str:
    if artifact:
        return str(Path(artifact).expanduser())
    if manifest:
        manifest_artifact = manifest.get("artifact", {}).get("path")
        if manifest_artifact:
            return str(Path(manifest_artifact).expanduser())
    raise ValueError("Artifact path is required unless artifact_manifest contains artifact.path")


def inspect_artifact(path: str) -> dict[str, Any]:
    artifact_path = Path(path).expanduser()
    exists = artifact_path.exists()
    return {
        "path": str(artifact_path),
        "exists": exists,
        "size": artifact_path.stat().st_size if exists else None,
        "sha256": file_sha256(artifact_path) if exists else None,
        "status": "ok" if exists else "missing",
    }


def check_manifest(
    manifest: dict[str, Any] | None,
    device_id: str,
    profile_id: str,
    uuid: str | None,
    serial: str | None,
    artifact_path: str,
    artifact_info: dict[str, Any],
) -> dict[str, Any]:
    if not manifest:
        return {
            "status": "not_provided",
            "warnings": ["No artifact manifest was provided; GUI should ask for extra confirmation before flashing."],
        }

    manifest_sha = manifest.get("artifact", {}).get("sha256")
    checks = {
        "device_id": manifest.get("device", {}).get("id") == device_id,
        "profile_id": manifest.get("profile", {}).get("id") == profile_id,
        "artifact_path": str(manifest.get("artifact", {}).get("path")) == artifact_path,
        "artifact_sha256": True if not manifest_sha else manifest_sha == artifact_info.get("sha256"),
    }
    if uuid:
        checks["canbus_uuid"] = manifest.get("device", {}).get("canbus_uuid") == uuid
    else:
        checks["serial"] = manifest.get("device", {}).get("serial") == serial
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "status": "ok" if not failed else "failed",
        "manifest": str((manifest or {}).get("_manifest_path") or "") or None,
        "checks": checks,
        "failed": failed,
    }


def flash_commands(flashtool: str, can_interface: str, uuid: str, artifact_path: str) -> list[str]:
    return [
        "sudo -n systemctl stop klipper",
        f"python3 {flashtool} -i {can_interface} -r -u {uuid}",
        f"python3 {flashtool} -i {can_interface} -q",
        f"python3 {flashtool} -i {can_interface} -f {artifact_path} -u {uuid}",
        f"python3 {flashtool} -i {can_interface} -q",
        "sudo -n systemctl start klipper",
    ]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
