from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import csv
import re
import json
import os
import shlex
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import urlopen

from .io_utils import atomic_write_json


SEMVER_RE = re.compile(r"(?P<version>\d+\.\d+\.\d+)")


@dataclass(frozen=True)
class CartographerFirmware:
    path: str
    filename: str
    firmware_type: str
    protocol: str
    process: str
    probe_version: str
    firmware_version: str
    can_speed: str
    is_lite: bool
    min_plugin_version: str | None

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "CartographerFirmware":
        return cls(
            path=row.get("filepath", ""),
            filename=row.get("filename", ""),
            firmware_type=row.get("firmware_type", ""),
            protocol=row.get("protocol", ""),
            process=row.get("process", ""),
            probe_version=row.get("probe_version", ""),
            firmware_version=row.get("firmware_version", ""),
            can_speed=row.get("can_speed", ""),
            is_lite=row.get("is_lite", "").lower() == "yes",
            min_plugin_version=row.get("min_plugin_version") or None,
        )


def inspect_cartographer_device(
    device: dict[str, Any],
    *,
    cartographer_firmware_path: str | None,
    can_bitrate: str = "1000000",
) -> dict[str, Any] | None:
    if not is_cartographer_device(device):
        return None

    probe_version = cartographer_probe_version(device)
    protocol = "CAN" if device.get("transport") in {"can", "pending_can"} else "USB" if device.get("transport") == "usb" else None
    current_version = extract_cartographer_version(device.get("firmware_version"))
    identity = query_cartographer_identity(device) if not current_version else None
    identity_firmware = identity.get("firmware_version") if identity else None
    identity_version = extract_cartographer_version(identity_firmware)
    current_version = current_version or identity_version
    firmware_root = Path(cartographer_firmware_path or "~/cartographer_firmware").expanduser()
    catalog_path = firmware_root / "firmware_list.csv"
    switch_script = firmware_root / "v4_switch.sh"

    result: dict[str, Any] = {
        "manager": "cartographer",
        "firmware_root": str(firmware_root),
        "catalog": str(catalog_path),
        "catalog_available": catalog_path.exists(),
        "probe_version": probe_version,
        "protocol": protocol,
        "current_version": current_version,
        "identity": identity,
        "can_speed": str(can_bitrate) if protocol == "CAN" else None,
        "flavour": "full",
        "update_mode": "vendor_prebuilt_bin",
        "usb_to_can": {
            "available": switch_script.exists(),
            "script": str(switch_script),
            "supported": protocol == "USB" or device.get("transport") == "usb",
            "message": "Cartographer USB to CAN switch script is available."
            if switch_script.exists()
            else f"Cartographer USB to CAN switch script was not found: {switch_script}",
        },
    }

    if not catalog_path.exists():
        result["status"] = "missing_catalog"
        result["message"] = f"Cartographer firmware catalog was not found: {catalog_path}"
        return result

    entries = load_firmware_catalog(catalog_path)
    matches = matching_update_firmware(
        entries,
        probe_version=probe_version,
        protocol=protocol,
        can_speed=str(can_bitrate),
    )
    can_to_usb_matches = matching_update_firmware(
        entries,
        probe_version=probe_version,
        protocol="USB",
        can_speed=str(can_bitrate),
    )
    dfu_usb_matches = matching_dfu_firmware(
        entries,
        probe_version=probe_version,
        protocol="USB",
        can_speed=str(can_bitrate),
    )
    dfu_can_matches = matching_dfu_firmware(
        entries,
        probe_version=probe_version,
        protocol="CAN",
        can_speed=str(can_bitrate),
    )
    versions = version_options(matches)
    selected = select_preferred_firmware(matches, current_version)

    result.update(
        {
            "status": "ok" if matches else "no_matching_firmware",
            "available_versions": versions,
            "dfu_available_versions": {
                "USB": version_options(dfu_usb_matches),
                "CAN": version_options(dfu_can_matches),
            },
            "can_to_usb": {
                "available": bool(protocol == "CAN" and can_to_usb_matches),
                "available_versions": version_options(can_to_usb_matches),
                "message": "USB firmware can be flashed over CAN Katapult."
                if can_to_usb_matches
                else "No USB update firmware was found for this Cartographer probe.",
            },
            "selected": selected,
            "update_available": bool(selected and current_version and selected.get("version") != current_version),
        }
    )
    if not matches:
        result["message"] = "No Cartographer firmware matched detected probe version, protocol, and CAN speed."

    return result


def is_cartographer_device(device: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(device.get(key) or "")
        for key in ("id", "name", "mcu_section", "runtime_app", "firmware_version", "confirmed_profile")
    ).lower()
    return "cartographer" in haystack or "scanner" in haystack


def query_cartographer_identity(device: dict[str, Any]) -> dict[str, Any] | None:
    uuid = device.get("canbus_uuid")
    serial = device.get("serial")
    if uuid:
        url = f"https://api.cartographer3d.com/q/uuid/{quote(str(uuid), safe='')}?update=1"
    elif serial:
        url = f"https://api.cartographer3d.com/q/device_name/{quote(Path(str(serial)).name, safe='')}?update=1"
    else:
        return None

    try:
        with urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    if not any(key in payload for key in ("serial_number", "device_uuid", "mcu_type", "firmware_version")):
        return None

    return {
        "source": "cartographer_api",
        "device_uuid": payload.get("device_uuid"),
        "serial_number": payload.get("serial_number"),
        "mcu_type": payload.get("mcu_type"),
        "firmware_version": payload.get("firmware_version"),
    }


def cartographer_probe_version(device: dict[str, Any]) -> str | None:
    firmware = str(device.get("firmware_version") or "").lower()
    chip = str(device.get("detected_chip") or "").lower()
    if "v4" in firmware or chip in {"stm32g431xx", "stm32g474xx"}:
        return "v4"
    if "v3" in firmware or chip.startswith("stm32f042"):
        return "v3"
    if device.get("transport") == "usb" and is_cartographer_device(device):
        return "v4"
    return None


def extract_cartographer_version(value: str | None) -> str | None:
    if not value:
        return None
    match = SEMVER_RE.search(value)
    return match.group("version") if match else None


def load_firmware_catalog(path: Path) -> list[CartographerFirmware]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return [CartographerFirmware.from_row(row) for row in csv.DictReader(file)]


def matching_update_firmware(
    entries: list[CartographerFirmware],
    *,
    probe_version: str | None,
    protocol: str | None,
    can_speed: str,
) -> list[CartographerFirmware]:
    matches: list[CartographerFirmware] = []
    for entry in entries:
        if entry.process != "Update" or entry.firmware_type != "Cartographer":
            continue
        if probe_version and entry.probe_version != probe_version:
            continue
        if protocol and entry.protocol != protocol:
            continue
        if protocol == "CAN" and entry.can_speed != can_speed:
            continue
        matches.append(entry)
    return sorted(matches, key=lambda item: semver_key(item.firmware_version), reverse=True)


def matching_dfu_firmware(
    entries: list[CartographerFirmware],
    *,
    probe_version: str | None,
    protocol: str,
    can_speed: str,
) -> list[CartographerFirmware]:
    matches: list[CartographerFirmware] = []
    for entry in entries:
        if entry.process != "DFU" or entry.firmware_type != "Cartographer + Katapult":
            continue
        if probe_version and entry.probe_version != probe_version:
            continue
        if entry.protocol != protocol:
            continue
        if protocol == "CAN" and entry.can_speed != can_speed:
            continue
        matches.append(entry)
    return sorted(matches, key=lambda item: semver_key(item.firmware_version), reverse=True)


def version_options(entries: list[CartographerFirmware]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for entry in entries:
        option = grouped.setdefault(
            entry.firmware_version,
            {
                "version": entry.firmware_version,
                "label": f"Cartographer {entry.probe_version} {entry.firmware_version}",
                "protocol": entry.protocol,
                "can_speed": entry.can_speed or None,
                "min_plugin_version": entry.min_plugin_version,
                "firmware": {},
            },
        )
        flavour = "lite" if entry.is_lite else "full"
        option["firmware"][flavour] = firmware_payload(entry)
        if entry.min_plugin_version and not option.get("min_plugin_version"):
            option["min_plugin_version"] = entry.min_plugin_version

    return sorted(grouped.values(), key=lambda item: semver_key(str(item["version"])), reverse=True)


def select_preferred_firmware(entries: list[CartographerFirmware], current_version: str | None) -> dict[str, Any] | None:
    options = version_options(entries)
    if not options:
        return None

    selected = options[0]
    if current_version:
        same_version = [option for option in options if option["version"] == current_version]
        if same_version:
            selected = same_version[0]

    firmware = selected.get("firmware", {})
    preferred = firmware.get("full") or firmware.get("lite")
    return {
        "version": selected["version"],
        "label": selected["label"],
        "min_plugin_version": selected.get("min_plugin_version"),
        "flavour": "full" if firmware.get("full") else "lite",
        "firmware": preferred,
    }


def firmware_payload(entry: CartographerFirmware) -> dict[str, Any]:
    return {
        "path": entry.path,
        "filename": entry.filename,
        "is_lite": entry.is_lite,
        "probe_version": entry.probe_version,
        "protocol": entry.protocol,
        "can_speed": entry.can_speed or None,
        "version": entry.firmware_version,
        "min_plugin_version": entry.min_plugin_version,
    }


def semver_key(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    numbers = []
    for part in parts[:3]:
        try:
            numbers.append(int(part))
        except ValueError:
            numbers.append(0)
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)  # type: ignore[return-value]


def create_cartographer_flash_plan(
    *,
    device_id: str,
    discovery_path: str | Path,
    devices_path: str | Path,
    cartographer_firmware_path: str | None,
    katapult_path: str | None,
    klipper_path: str | None,
    can_bitrate: str,
    firmware_version: str,
    flavour: str = "full",
    target_protocol: str | None = None,
) -> dict[str, Any]:
    discovery = json.loads(Path(discovery_path).read_text(encoding="utf-8"))
    discovered = {device["id"]: device for device in discovery.get("devices", [])}
    if device_id not in discovered:
        raise ValueError(f"Device not found in discovery: {device_id}")

    from .devices import devices_by_id, load_devices

    confirmed = devices_by_id(load_devices(devices_path)).get(device_id)
    if not confirmed:
        raise ValueError(f"Device is not confirmed in {devices_path}: {device_id}")

    device = discovered[device_id]
    vendor = device.get("vendor_firmware") or inspect_cartographer_device(
        device,
        cartographer_firmware_path=cartographer_firmware_path,
        can_bitrate=can_bitrate,
    )
    if not vendor or vendor.get("manager") != "cartographer":
        raise ValueError(f"Device is not a Cartographer device: {device_id}")

    firmware_root = Path(vendor.get("firmware_root") or cartographer_firmware_path or "~/cartographer_firmware").expanduser()
    target_protocol = target_protocol.upper() if target_protocol else None
    if target_protocol and target_protocol not in {"USB", "CAN"}:
        raise ValueError(f"Unsupported Cartographer target protocol: {target_protocol}")
    if target_protocol:
        catalog_path = firmware_root / "firmware_list.csv"
        if not catalog_path.exists():
            raise ValueError(f"Cartographer firmware catalog was not found: {catalog_path}")
        entries = matching_update_firmware(
            load_firmware_catalog(catalog_path),
            probe_version=vendor.get("probe_version"),
            protocol=target_protocol,
            can_speed=str(can_bitrate),
        )
        vendor_for_selection = {
            "available_versions": version_options(entries),
        }
    else:
        vendor_for_selection = vendor
    selected = select_vendor_firmware(vendor_for_selection, firmware_version, flavour)
    firmware_path = firmware_root / "firmware" / str(selected["path"])
    if not firmware_path.exists():
        raise ValueError(f"Cartographer firmware file does not exist: {firmware_path}")

    transport = str(device.get("transport") or "").lower()
    uuid = confirmed.get("canbus_uuid") or device.get("canbus_uuid")
    serial = confirmed.get("serial") or device.get("serial")
    can_interface = confirmed.get("can_interface") or device.get("can_interface") or "can0"
    if transport == "can" and not uuid:
        raise ValueError(f"Device does not have a canbus_uuid: {device_id}")
    if transport == "usb" and not serial:
        raise ValueError(f"Device does not have a USB serial path: {device_id}")
    if transport not in {"can", "usb"}:
        raise ValueError(f"Unsupported Cartographer transport for firmware flash: {transport}")

    runner = cartographer_flash_runner(katapult_path=katapult_path, klipper_path=klipper_path)
    command = cartographer_flash_command(
        transport=transport,
        firmware_path=firmware_path,
        runner=runner,
        can_interface=str(can_interface),
        canbus_uuid=str(uuid) if uuid else None,
        serial=str(serial) if serial else None,
        klipper_path=klipper_path,
    )

    return {
        "action": "cartographer_flash",
        "execute": False,
        "status": "planned",
        "device": {
            "id": device_id,
            "name": device.get("name"),
            "mcu_section": device.get("mcu_section"),
            "transport": device.get("transport"),
            "target_protocol": target_protocol or vendor.get("protocol"),
            "can_interface": can_interface,
            "canbus_uuid": uuid,
            "serial": serial,
            "detected_chip": device.get("detected_chip"),
            "firmware_version": device.get("firmware_version"),
        },
        "vendor_firmware": {
            "manager": "cartographer",
            "version": firmware_version,
            "flavour": flavour,
            "target_protocol": target_protocol or selected.get("protocol"),
            "firmware": selected,
            "path": str(firmware_path),
            "runner": runner,
        },
        "steps": [
            {
                "id": "stop_klipper",
                "command": "sudo service klipper stop",
            },
            {
                "id": "flash_cartographer",
                "command": command,
            },
            {
                "id": "start_klipper",
                "command": "sudo service klipper start",
            },
        ],
    }


def cartographer_flash_command(
    *,
    transport: str,
    firmware_path: Path,
    runner: dict[str, str],
    can_interface: str,
    canbus_uuid: str | None,
    serial: str | None,
    klipper_path: str | None,
) -> list[str]:
    if transport == "can":
        if not canbus_uuid:
            raise ValueError("Cartographer CAN flash requires canbus_uuid.")
        return [
            runner["python"],
            runner["flashtool"],
            "-i",
            can_interface,
            "-f",
            str(firmware_path),
            "-u",
            normalize_can_uuid(canbus_uuid),
        ]

    if not serial:
        raise ValueError("Cartographer USB flash requires serial.")
    klipper = Path(klipper_path or "~/klipper").expanduser()
    firmware_dir = firmware_path.parent
    firmware_name = firmware_path.name
    enter_code = f"import flash_usb as u; u.enter_bootloader({serial!r})"
    script = " && ".join(
        [
            f"cd {shlex.quote(str(klipper / 'scripts'))}",
            f"{shlex.quote(runner['python'])} -c {shlex.quote(enter_code)}",
            "for i in $(seq 1 15); do KATAPULT_DEVICE=$(ls /dev/serial/by-id/*katapult* 2>/dev/null | head -n1); test -n \"$KATAPULT_DEVICE\" && break; sleep 2; done",
            "test -n \"$KATAPULT_DEVICE\"",
            f"cd {shlex.quote(str(firmware_dir))}",
            f"{shlex.quote(runner['python'])} {shlex.quote(runner['flashtool'])} -f {shlex.quote(firmware_name)} -d \"$KATAPULT_DEVICE\"",
        ]
    )
    return ["bash", "-lc", script]


def flash_cartographer_firmware(
    *,
    device_id: str,
    discovery_path: str | Path,
    devices_path: str | Path,
    cartographer_firmware_path: str | None,
    katapult_path: str | None,
    klipper_path: str | None,
    can_bitrate: str,
    firmware_version: str,
    flavour: str = "full",
    target_protocol: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    plan = create_cartographer_flash_plan(
        device_id=device_id,
        discovery_path=discovery_path,
        devices_path=devices_path,
        cartographer_firmware_path=cartographer_firmware_path,
        katapult_path=katapult_path,
        klipper_path=klipper_path,
        can_bitrate=can_bitrate,
        firmware_version=firmware_version,
        flavour=flavour,
        target_protocol=target_protocol,
    )
    result = {
        **plan,
        "execute": execute,
        "flash": {
            "status": "planned",
            "started_at": None,
            "completed_at": None,
        },
    }
    log_dir = Path("builds").expanduser() / device_id / "cartographer"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "flash.log"
    result_path = log_dir / "flash-result.json"
    result["flash"]["flash_log"] = str(log_path)
    result["flash"]["metadata"] = str(result_path)

    if not execute:
        return result

    stopped_klipper = False
    result["flash"]["started_at"] = now_iso()
    reset_vendor_log(log_path, result)
    try:
        run_logged(["sudo", "service", "klipper", "stop"], log_path)
        stopped_klipper = True
        run_logged(command_from_step(plan, "flash_cartographer"), log_path)
        run_logged(["sudo", "service", "klipper", "start"], log_path)
        stopped_klipper = False
        result["flash"]["status"] = "ok"
    except Exception as exc:
        result["flash"]["status"] = "failed"
        result["flash"]["error"] = str(exc)
        if stopped_klipper:
            try:
                run_logged(["sudo", "service", "klipper", "start"], log_path)
                result["flash"]["recovery"] = "klipper_start_attempted"
            except Exception as recovery_exc:
                result["flash"]["recovery"] = f"klipper_start_failed: {recovery_exc}"
    finally:
        result["flash"]["completed_at"] = now_iso()
        atomic_write_json(result_path, result)

    return result


def switch_cartographer_usb_to_can(
    *,
    cartographer_firmware_path: str | None,
    katapult_path: str | None = None,
    klipper_path: str | None = None,
    can_interface: str = "can0",
    firmware_version: str | None = None,
    flavour: str = "full",
    phase: str = "deploy_katapult",
    device_serial: str | None = None,
    canbus_uuid: str | None = None,
    can_bitrate: str = "1000000",
    execute: bool = False,
    device_id: str = "cartographer_usb",
) -> dict[str, Any]:
    root = Path(cartographer_firmware_path or "~/cartographer_firmware").expanduser()
    script = root / "v4_switch.sh"
    deployer = root / "firmware" / "v4" / "katapult-deployer" / "katapult_deployer_v4_CAN_1M.bin"
    runner = cartographer_flash_runner(katapult_path=katapult_path, klipper_path=klipper_path)
    result: dict[str, Any] = {
        "action": "cartographer_usb_to_can",
        "execute": execute,
        "status": "planned",
        "phase": phase,
        "repository": str(root),
        "script": str(script),
        "manual_steps": [
            "Keep Cartographer connected by USB for the Katapult deployer step.",
            "After the deployer is flashed, power down if needed and reconnect Cartographer to the CAN harness.",
            f"Confirm that the probe is visible on {can_interface} before flashing the selected CAN firmware.",
        ],
    }
    if phase == "deploy_katapult":
        result.update(cartographer_usb_deployer_plan(root, deployer, runner, device_serial))
    elif phase == "flash_can":
        result.update(cartographer_can_firmware_plan(root, runner, can_interface, firmware_version, flavour, canbus_uuid, can_bitrate))
    else:
        raise ValueError(f"Unsupported Cartographer USB to CAN phase: {phase}")

    if not execute:
        return result

    if result.get("status") not in {"planned", "ready"}:
        return result

    from .prepare_build import sanitize_path_part

    log_dir = Path("builds").expanduser() / sanitize_path_part(device_id) / "usb_to_can" / phase
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "switch.log"
    result_path = log_dir / "switch-result.json"
    result["log"] = str(log_path)
    result["metadata"] = str(result_path)
    result["started_at"] = now_iso()
    try:
        existing_katapult = set(Path("/dev/serial/by-id").glob("*katapult*")) if phase == "deploy_katapult" else set()
        katapult_serial = None
        for index, command in enumerate(result.get("commands", [])):
            if phase == "deploy_katapult" and index == 1:
                katapult_serial = wait_for_new_cartographer_katapult(existing_katapult)
                with log_path.open("a", encoding="utf-8") as log:
                    log.write(f"Detected Cartographer USB Katapult: {katapult_serial}\n")
                continue
            if phase == "deploy_katapult" and index == 2:
                command = [runner["python"], runner["flashtool"], "-f", str(deployer), "-d", str(katapult_serial)]
            run_logged([str(item) for item in command], log_path)
        result["status"] = "awaiting_can_reconnect" if phase == "deploy_katapult" else "ok"
        if phase == "deploy_katapult":
            write_usb_to_can_state(
                {
                    "status": "awaiting_can_reconnect",
                    "phase": phase,
                    "usb_serial": result.get("usb_serial") or device_serial,
                    "can_interface": can_interface,
                    "can_bitrate": str(can_bitrate),
                    "firmware_version": firmware_version,
                    "flavour": flavour,
                    "updated_at": now_iso(),
                    "message": "Reconnect Cartographer to CAN, refresh scan, then flash CAN firmware.",
                }
            )
        elif phase == "flash_can":
            clear_usb_to_can_state()
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
    finally:
        result["completed_at"] = now_iso()
        atomic_write_json(result_path, result)

    return result


def wait_for_new_cartographer_katapult(existing: set[Path], timeout: int = 15) -> str:
    serial_dir = Path("/dev/serial/by-id")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = sorted(set(serial_dir.glob("*katapult*")) - existing) if serial_dir.exists() else []
        if len(matches) > 1:
            raise RuntimeError("Multiple USB Katapult devices appeared; cannot identify Cartographer.")
        if matches:
            return str(matches[0])
        time.sleep(1)
    raise RuntimeError("Cartographer USB Katapult did not appear after entering the bootloader.")


def usb_to_can_state_path(build_root: str | Path = "builds") -> Path:
    return Path(build_root).expanduser() / "cartographer" / "usb_to_can" / "state.json"


def write_usb_to_can_state(state: dict[str, Any], build_root: str | Path = "builds") -> None:
    path = usb_to_can_state_path(build_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, state)


def read_usb_to_can_state(build_root: str | Path = "builds") -> dict[str, Any] | None:
    path = usb_to_can_state_path(build_root)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if state.get("status") != "awaiting_can_reconnect":
        return None
    return state


def clear_usb_to_can_state(build_root: str | Path = "builds") -> None:
    try:
        usb_to_can_state_path(build_root).unlink()
    except FileNotFoundError:
        pass


def flash_cartographer_dfu(
    *,
    cartographer_firmware_path: str | None,
    firmware_version: str,
    target_protocol: str,
    probe_version: str = "v4",
    flavour: str = "full",
    can_bitrate: str = "1000000",
    dfu_vid_pid: str = "0483:df11",
    execute: bool = False,
    dfu_device_id: str | None = None,
    moonraker_url: str = "http://127.0.0.1:7125",
) -> dict[str, Any]:
    root = Path(cartographer_firmware_path or "~/cartographer_firmware").expanduser()
    catalog_path = root / "firmware_list.csv"
    protocol = target_protocol.upper()
    if protocol not in {"USB", "CAN"}:
        raise ValueError(f"Unsupported Cartographer DFU target protocol: {target_protocol}")
    if not catalog_path.exists():
        raise ValueError(f"Cartographer firmware catalog was not found: {catalog_path}")

    entries = matching_dfu_firmware(
        load_firmware_catalog(catalog_path),
        probe_version=probe_version,
        protocol=protocol,
        can_speed=str(can_bitrate),
    )
    selected = select_vendor_firmware({"available_versions": version_options(entries)}, firmware_version, flavour)
    firmware_path = root / "firmware" / str(selected["path"])
    if not firmware_path.exists():
        raise ValueError(f"Cartographer DFU firmware file does not exist: {firmware_path}")

    command = [
        "sudo",
        "dfu-util",
        "-R",
        "-a",
        "0",
        "-s",
        "0x08000000:mass-erase:force:leave",
        "-D",
        str(firmware_path),
        "-d",
        dfu_vid_pid,
    ]
    result: dict[str, Any] = {
        "action": "cartographer_dfu_flash",
        "execute": execute,
        "status": "planned",
        "target": {
            "probe_version": probe_version,
            "protocol": protocol,
            "can_bitrate": str(can_bitrate) if protocol == "CAN" else None,
            "firmware_version": firmware_version,
            "flavour": flavour,
        },
        "firmware": {
            **selected,
            "path": str(firmware_path),
        },
        "dfu": {
            "vid_pid": dfu_vid_pid,
            "tool": "dfu-util",
        },
        "manual_steps": [
            "Connect Cartographer to the host by USB.",
            "Put Cartographer into STM32 DFU mode before running this action.",
            "After DFU flash completes, reconnect it using the selected USB or CAN wiring.",
        ],
        "commands": [command],
        "commands_preview": [" ".join(command)],
    }
    if not execute:
        return result

    log_dir = Path("builds").expanduser() / (dfu_device_id or "cartographer_dfu") / "cartographer_dfu" / protocol.lower()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "dfu-flash.log"
    result_path = log_dir / "dfu-flash-result.json"
    result["log"] = str(log_path)
    result["metadata"] = str(result_path)
    result["started_at"] = now_iso()
    result["status"] = "running"
    result["steps"] = [{"id": "flash_dfu", "label": "Flash Cartographer through STM32 DFU", "status": "running"}]
    log_path.write_text("# Cartographer DFU flash log\n", encoding="utf-8")
    atomic_write_json(result_path, result)
    try:
        from .dfu_target import select_dfu_port
        from .dfu_flash import require_idle_printer

        require_idle_printer(moonraker_url)
        command.extend(["-p", select_dfu_port(dfu_device_id, dfu_vid_pid)])
        result["commands_preview"] = [" ".join(command)]
        result["dfu_device_id"] = dfu_device_id
        atomic_write_json(result_path, result)
        run_logged(command, log_path)
        result["status"] = "ok"
        result["steps"][0]["status"] = "done"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        result["steps"][0]["status"] = "failed"
    finally:
        result["completed_at"] = now_iso()
        atomic_write_json(result_path, result)

    return result


def cartographer_usb_deployer_plan(
    root: Path,
    deployer: Path,
    runner: dict[str, str],
    device_serial: str | None,
) -> dict[str, Any]:
    serial = device_serial or first_cartographer_usb_serial()
    if not deployer.exists():
        return {
            "status": "missing_deployer",
            "message": f"Cartographer Katapult deployer was not found: {deployer}",
        }
    if not serial:
        return {
            "status": "missing_usb_device",
            "message": "Cartographer V4 USB serial device was not found in /dev/serial/by-id.",
        }

    return {
        "status": "planned",
        "usb_serial": serial,
        "deployer": str(deployer),
        "commands": [
            ["bash", "-lc", f"cd ~/klipper/scripts && ~/klippy-env/bin/python -c {shlex.quote(f'import flash_usb as u; u.enter_bootloader({serial!r})')}"],
            ["wait", "for", "new", "USB", "Katapult"],
            [runner["python"], runner["flashtool"], "-f", str(deployer), "-d", "<new-usb-katapult-serial>"],
        ],
        "commands_preview": [
            f"enter Cartographer bootloader through {serial}",
            "wait for the newly enumerated USB Katapult serial",
            f"{runner['python']} {runner['flashtool']} -f {deployer} -d <new-usb-katapult-serial>",
        ],
    }


def cartographer_can_firmware_plan(
    root: Path,
    runner: dict[str, str],
    can_interface: str,
    firmware_version: str | None,
    flavour: str,
    canbus_uuid: str | None,
    can_bitrate: str,
) -> dict[str, Any]:
    catalog_path = root / "firmware_list.csv"
    if not catalog_path.exists():
        return {
            "status": "missing_catalog",
            "message": f"Cartographer firmware catalog was not found: {catalog_path}",
        }
    entries = matching_update_firmware(
        load_firmware_catalog(catalog_path),
        probe_version="v4",
        protocol="CAN",
        can_speed=str(can_bitrate),
    )
    selected = select_preferred_firmware(entries, firmware_version)
    if firmware_version:
        selected = select_vendor_firmware({"available_versions": version_options(entries)}, firmware_version, flavour)
    elif selected:
        selected = selected["firmware"]
    if not selected:
        return {
            "status": "no_matching_firmware",
            "message": "No Cartographer V4 CAN firmware matched the configured CAN bitrate.",
        }
    firmware_path = root / "firmware" / str(selected["path"])
    if not firmware_path.exists():
        return {
            "status": "missing_firmware",
            "message": f"Cartographer CAN firmware file was not found: {firmware_path}",
        }
    if not canbus_uuid:
        return {
            "status": "awaiting_can_uuid",
            "message": "Reconnect Cartographer to CAN, scan can0, then confirm the detected UUID before flashing CAN firmware.",
            "firmware": str(firmware_path),
            "commands_preview": [f"{runner['python']} {runner['flashtool']} -i {can_interface} -f {firmware_path} -u <canbus_uuid>"],
        }
    return {
        "status": "planned",
        "firmware": str(firmware_path),
        "can_interface": can_interface,
        "canbus_uuid": normalize_can_uuid(str(canbus_uuid)),
        "commands": [
            [runner["python"], runner["flashtool"], "-i", can_interface, "-f", str(firmware_path), "-u", normalize_can_uuid(str(canbus_uuid))],
        ],
        "commands_preview": [f"{runner['python']} {runner['flashtool']} -i {can_interface} -f {firmware_path} -u {normalize_can_uuid(str(canbus_uuid))}"],
    }


def first_cartographer_usb_serial() -> str | None:
    serial_dir = Path("/dev/serial/by-id")
    if not serial_dir.exists():
        return None
    matches = sorted(
        path for path in serial_dir.iterdir()
        if path.name.lower().startswith("usb-") and
        ("cartographer" in path.name.lower() or "scanner" in path.name.lower())
    )
    if len(matches) > 1:
        raise ValueError("Multiple Cartographer USB devices found. Select the exact serial device in the panel.")
    return str(matches[0]) if matches else None


def select_vendor_firmware(vendor: dict[str, Any], version: str, flavour: str) -> dict[str, Any]:
    for option in vendor.get("available_versions", []):
        if str(option.get("version")) != str(version):
            continue
        firmware = option.get("firmware", {})
        selected = firmware.get(flavour) or firmware.get("full") or firmware.get("lite")
        if selected:
            return selected
    raise ValueError(f"Cartographer firmware version is not available: {version}")


def cartographer_flash_runner(*, katapult_path: str | None, klipper_path: str | None) -> dict[str, str]:
    klipper = Path(klipper_path or "~/klipper").expanduser()
    klippy_python = Path("~/klippy-env/bin/python").expanduser()
    python = str(klippy_python) if klippy_python.exists() else "python3"
    candidates = [
        klipper / "lib" / "canboot" / "flash_can.py",
        klipper / "lib" / "katapult" / "flashtool.py",
        Path(katapult_path or "~/katapult").expanduser() / "scripts" / "flashtool.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return {"python": python, "flashtool": str(candidate)}
    return {"python": python, "flashtool": str(candidates[-1])}


def normalize_can_uuid(uuid: str) -> str:
    return uuid.replace(":", "").replace("-", "").removeprefix("0x").removeprefix("0X").lower()


def command_from_step(plan: dict[str, Any], step_id: str) -> list[str]:
    for step in plan.get("steps", []):
        if step.get("id") == step_id:
            command = step["command"]
            if isinstance(command, list):
                return [str(item) for item in command]
            return str(command).split()
    raise ValueError(f"Cartographer flash step not found: {step_id}")


def run_logged(command: list[str], log_path: Path) -> str:
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(command)}\n")
        log.flush()
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, text=True,
                                   env={**os.environ, "PYTHONUNBUFFERED": "1"})
        try:
            returncode = process.wait(timeout=300)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise RuntimeError(f"Command timed out after 300s: {' '.join(command)}") from exc

    if returncode != 0:
        raise RuntimeError(f"Command failed ({returncode}): {' '.join(command)}")
    return ""


def reset_vendor_log(log_path: Path, result: dict[str, Any]) -> None:
    header = {
        "action": result.get("action"),
        "device": result.get("device"),
        "vendor_firmware": result.get("vendor_firmware"),
        "started_at": result.get("flash", {}).get("started_at"),
    }
    log_path.write_text("# MCU Update Manager Cartographer flash log\n" + json.dumps(header, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
