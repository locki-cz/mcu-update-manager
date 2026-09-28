from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re
import subprocess

from .cfg_parser import KlipperConfigParser, mcu_name_from_section, option_value
from .cartographer import inspect_cartographer_device
from .devices import apply_confirmed_device, devices_by_id, load_devices
from .firmware_source import inspect_firmware_sources
from .moonraker import query_printer_objects
from .preflight import check_katapult
from .profiles import HardwareProfile, best_profile_matches


UUID_RE = re.compile(r"(?:UUID|uuid)\s*[:=]\s*(?P<uuid>[0-9a-fA-F]+)")
APP_RE = re.compile(r"(?:Application|app)\s*[:=]\s*(?P<app>[A-Za-z0-9_.-]+)")
PIN_RE = re.compile(r"(?P<prefix>[A-Za-z0-9_ -]+):(?P<pin>[!^~]*[A-Za-z]+[0-9]+)")


@dataclass
class DiscoverOptions:
    printer_cfg: str
    serial_dir: str = "/dev/serial/by-id"
    can_interface: str = "can0"
    can_query_output: str | None = None
    katapult_path: str | None = None
    klipper_path: str | None = None
    kalico_path: str | None = None
    moonraker_url: str | None = "http://127.0.0.1:7125"
    refresh_repositories: bool = False
    printer_objects_json: str | None = None
    devices_path: str | None = "devices.yaml"
    firmware_project: str = "kalico"
    firmware_ref: str = "current"
    can_bitrate: str = "1000000"
    cartographer_firmware_path: str | None = "~/cartographer_firmware"


def discover(options: DiscoverOptions, profiles: list[HardwareProfile]) -> dict[str, Any]:
    parsed_config = KlipperConfigParser(options.printer_cfg).parse()
    mcu_sections = parsed_config.sections_by_prefix("mcu")
    printer_objects = load_printer_objects(options.printer_objects_json)
    warnings = list(parsed_config.warnings)
    runtime_source = "json_fixture" if printer_objects else None
    if not printer_objects and options.moonraker_url:
        printer_objects, runtime_warning = query_printer_objects(
            options.moonraker_url,
            [section.name for section in mcu_sections],
        )
        if printer_objects:
            runtime_source = "moonraker"
        if runtime_warning:
            warnings.append(runtime_warning)

    serial_devices = scan_serial_by_id(options.serial_dir)
    confirmed_devices = devices_by_id(load_devices(options.devices_path))
    katapult_status = check_katapult(options.katapult_path)
    firmware_sources = inspect_firmware_sources(
        klipper_path=options.klipper_path,
        kalico_path=options.kalico_path,
        refresh=options.refresh_repositories,
    )
    can_nodes = load_can_nodes(options.can_interface, options.can_query_output, options.katapult_path)
    can_scan_note = can_scan_status_note(mcu_sections, can_nodes)

    devices = []
    used_serials: set[str] = set()
    for section in mcu_sections:
        mcu_name = mcu_name_from_section(section.name)
        serial = option_value(section, "serial")
        canbus_uuid = option_value(section, "canbus_uuid")
        transport = "can" if canbus_uuid else "usb" if serial else "unknown"
        if serial:
            used_serials.add(serial)
            used_serials.add(Path(serial).name)
        runtime = printer_objects.get(section.name, printer_objects.get(mcu_name, {}))
        chip = runtime.get("mcu_constants", {}).get("MCU")
        firmware_version = runtime.get("mcu_version")
        referenced_pins = find_referenced_pins(parsed_config.sections, mcu_name)

        device = {
            "id": mcu_name.replace(" ", "_"),
            "mcu_section": section.name,
            "name": mcu_name,
            "transport": transport,
            "serial": serial,
            "canbus_uuid": canbus_uuid,
            "can_interface": options.can_interface if canbus_uuid else None,
            "detected_chip": chip,
            "firmware_version": firmware_version,
            "referenced_pins": referenced_pins,
            "runtime_app": runtime.get("app", "Klipper") if runtime else None,
            "config_source": {"file": section.source, "line": section.line},
            "matching_serial_devices": match_serial_devices(serial_devices, serial),
            "can_node": match_can_node(can_nodes, canbus_uuid),
            "likely_profiles": best_profile_matches(
                profiles,
                chip=chip,
                transport=transport,
                mcu_name=mcu_name,
                referenced_pins=referenced_pins,
            ),
        }
        apply_confirmed_device(device, confirmed_devices.get(device["id"]))
        cartographer = inspect_cartographer_device(
            device,
            cartographer_firmware_path=options.cartographer_firmware_path,
            can_bitrate=options.can_bitrate,
        )
        if cartographer:
            identity = cartographer.get("identity") or {}
            if not device.get("firmware_version") and identity.get("firmware_version"):
                device["firmware_version"] = identity["firmware_version"]
            device["vendor_firmware"] = cartographer
        devices.append(device)

    devices.extend(
        discover_usb_cartographers(
            serial_devices=serial_devices,
            used_serials=used_serials,
            profiles=profiles,
            confirmed_devices=confirmed_devices,
            cartographer_firmware_path=options.cartographer_firmware_path,
            can_bitrate=options.can_bitrate,
        )
    )
    devices.extend(
        discover_unconfigured_cartographer_can_candidates(
            can_nodes=can_nodes,
            configured_devices=devices,
            profiles=profiles,
            confirmed_devices=confirmed_devices,
            cartographer_firmware_path=options.cartographer_firmware_path,
            can_bitrate=options.can_bitrate,
            can_interface=options.can_interface,
        )
    )
    devices.extend(
        discover_dfu_candidates(
            profiles=profiles,
            confirmed_devices=confirmed_devices,
            cartographer_firmware_path=options.cartographer_firmware_path,
            can_bitrate=options.can_bitrate,
        )
    )

    return {
        "project": "MCU Update Manager",
        "schema_version": 1,
        "firmware_source": {
            "project": options.firmware_project,
            "ref": options.firmware_ref,
            "choices": ["current", "latest", "tag", "branch", "commit", "previous_artifact"],
            "repositories": firmware_sources,
        },
        "discovery": {
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "phases": [
                {"id": "printer_cfg", "status": "done"},
                {"id": "usb", "status": "done"},
                {"id": "dfu", "status": "done"},
                {"id": "can", "status": "done"},
                {"id": "runtime", "status": "done"},
            ],
            "printer_cfg": str(Path(options.printer_cfg).expanduser()),
            "can_interface": options.can_interface,
            "serial_dir": str(Path(options.serial_dir).expanduser()),
            "warnings": warnings,
            "runtime_source": runtime_source,
            "can_scan_note": can_scan_note,
            "devices_path": options.devices_path,
        },
        "preflight": {
            "katapult": katapult_status,
        },
        "serial_devices": serial_devices,
        "can_nodes": can_nodes,
        "devices": devices,
    }


def load_printer_objects(path: str | None) -> dict[str, Any]:
    if not path:
        return {}

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "status" in data:
        return data["status"]

    return data


def can_scan_status_note(mcu_sections: list[Any], can_nodes: list[dict[str, str]]) -> str | None:
    configured_can_uuids = [option_value(section, "canbus_uuid") for section in mcu_sections]
    configured_can_uuids = [uuid for uuid in configured_can_uuids if uuid]

    if configured_can_uuids and not can_nodes:
        return (
            "CAN query returned no nodes. This can be normal when all CAN UUIDs are already assigned "
            "in printer.cfg and Klipper is running."
        )

    return None


def find_referenced_pins(sections: list[Any], mcu_name: str) -> list[str]:
    pins: set[str] = set()
    for section in sections:
        for option in section.options.values():
            for match in PIN_RE.finditer(option.value):
                if match.group("prefix").strip() == mcu_name:
                    pins.add(match.group("pin").lstrip("!^~").upper())

    return sorted(pins)


def scan_serial_by_id(serial_dir: str) -> list[dict[str, str]]:
    root = Path(serial_dir).expanduser()
    if not root.exists():
        return []

    devices = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        devices.append(
            {
                "name": entry.name,
                "path": str(entry),
                "target": safe_readlink(entry),
                "detected_chip": chip_from_serial_name(entry.name),
            }
        )

    return devices


def discover_usb_cartographers(
    *,
    serial_devices: list[dict[str, str]],
    used_serials: set[str],
    profiles: list[HardwareProfile],
    confirmed_devices: dict[str, dict[str, Any]],
    cartographer_firmware_path: str | None,
    can_bitrate: str,
) -> list[dict[str, Any]]:
    devices = []
    index = 1
    for serial_device in serial_devices:
        if not is_cartographer_serial_device(serial_device):
            continue
        if serial_device["path"] in used_serials or serial_device["name"] in used_serials:
            continue

        device_id = "cartographer_usb" if index == 1 else f"cartographer_usb_{index}"
        index += 1
        device = {
            "id": device_id,
            "mcu_section": None,
            "name": "cartographer",
            "transport": "usb",
            "serial": serial_device["path"],
            "canbus_uuid": None,
            "can_interface": None,
            "detected_chip": serial_device.get("detected_chip") or "stm32g431xx",
            "firmware_version": None,
            "referenced_pins": [],
            "runtime_app": "Cartographer",
            "config_source": None,
            "matching_serial_devices": [serial_device],
            "can_node": None,
            "discovery_source": "serial_by_id",
            "likely_profiles": best_profile_matches(
                profiles,
                chip=serial_device.get("detected_chip") or "stm32g431xx",
                transport="usb",
                mcu_name="cartographer",
                referenced_pins=[],
            ),
        }
        apply_confirmed_device(device, confirmed_devices.get(device_id))
        cartographer = inspect_cartographer_device(
            device,
            cartographer_firmware_path=cartographer_firmware_path,
            can_bitrate=can_bitrate,
        )
        if cartographer:
            identity = cartographer.get("identity") or {}
            if not device.get("firmware_version") and identity.get("firmware_version"):
                device["firmware_version"] = identity["firmware_version"]
            device["vendor_firmware"] = cartographer
        devices.append(device)

    return devices


def discover_unconfigured_cartographer_can_candidates(
    *,
    can_nodes: list[dict[str, str]],
    configured_devices: list[dict[str, Any]],
    profiles: list[HardwareProfile],
    confirmed_devices: dict[str, dict[str, Any]],
    cartographer_firmware_path: str | None,
    can_bitrate: str,
    can_interface: str,
) -> list[dict[str, Any]]:
    configured_uuids = {
        normalize_uuid(str(device.get("canbus_uuid")))
        for device in configured_devices
        if device.get("canbus_uuid")
    }
    devices = []
    for node in can_nodes:
        uuid = normalize_uuid(node.get("uuid", ""))
        if not uuid or uuid in configured_uuids:
            continue
        if str(node.get("application", "")).lower() not in {"katapult", "canboot"}:
            continue

        device_id = f"cartographer_can_{uuid}"
        device = {
            "id": device_id,
            "mcu_section": None,
            "name": "cartographer",
            "transport": "can",
            "serial": None,
            "canbus_uuid": uuid,
            "can_interface": can_interface,
            "detected_chip": "stm32g431xx",
            "firmware_version": None,
            "referenced_pins": [],
            "runtime_app": node.get("application") or "Katapult",
            "config_source": None,
            "matching_serial_devices": [],
            "can_node": node,
            "discovery_source": "can_query_unconfigured",
            "likely_profiles": best_profile_matches(
                profiles,
                chip="stm32g431xx",
                transport="can",
                mcu_name="cartographer",
                referenced_pins=[],
            ),
        }
        apply_confirmed_device(device, confirmed_devices.get(device_id))
        cartographer = inspect_cartographer_device(
            device,
            cartographer_firmware_path=cartographer_firmware_path,
            can_bitrate=can_bitrate,
        )
        if cartographer:
            identity = cartographer.get("identity") or {}
            if not device.get("firmware_version") and identity.get("firmware_version"):
                device["firmware_version"] = identity["firmware_version"]
            device["vendor_firmware"] = cartographer
        devices.append(device)

    return devices


def discover_dfu_candidates(
    *,
    profiles: list[HardwareProfile],
    confirmed_devices: dict[str, dict[str, Any]],
    cartographer_firmware_path: str | None = None,
    can_bitrate: str = "1000000",
) -> list[dict[str, Any]]:
    dfu_devices = scan_usb_dfu_devices()
    devices = []
    for index, dfu_device in enumerate(dfu_devices, start=1):
        bus = str(dfu_device.get("bus") or "unknown")
        address = str(dfu_device.get("device") or index)
        vid_pid = str(dfu_device.get("vid_pid") or "0483:df11")
        device_id = f"dfu_{bus}_{address}_{vid_pid.replace(':', '_')}"
        device = {
            "id": device_id,
            "mcu_section": None,
            "name": "STM32 DFU",
            "transport": "dfu",
            "serial": None,
            "canbus_uuid": None,
            "can_interface": None,
            "detected_chip": None,
            "firmware_version": None,
            "referenced_pins": [],
            "runtime_app": "STM32 DFU",
            "config_source": None,
            "matching_serial_devices": [],
            "usb_device": dfu_device,
            "display_id": f"Bus {bus} Device {address}: ID {vid_pid}",
            "can_node": None,
            "discovery_source": "lsusb_dfu",
            "likely_profiles": dfu_profile_choices(profiles, vid_pid),
        }
        apply_confirmed_device(device, confirmed_devices.get(device_id))
        if str(device.get("confirmed_profile") or "").startswith("cartographer_"):
            cartographer = inspect_cartographer_device(
                device,
                cartographer_firmware_path=cartographer_firmware_path,
                can_bitrate=can_bitrate,
            )
            if cartographer:
                device["vendor_firmware"] = cartographer
        devices.append(device)

    return devices


def scan_usb_dfu_devices() -> list[dict[str, str]]:
    try:
        result = subprocess.run(["lsusb"], check=False, capture_output=True, text=True)
    except OSError:
        return []
    devices = []
    for line in result.stdout.splitlines():
        if "0483:df11" not in line.lower():
            continue
        match = re.match(
            r"Bus\s+(?P<bus>\d+)\s+Device\s+(?P<device>\d+):\s+ID\s+(?P<vid_pid>[0-9a-fA-F]{4}:[0-9a-fA-F]{4})",
            line,
        )
        devices.append(
            {
                "bus": match.group("bus") if match else "unknown",
                "device": match.group("device") if match else str(len(devices) + 1),
                "vid_pid": match.group("vid_pid").lower() if match else "0483:df11",
                "description": line.strip(),
            }
        )

    return devices


def dfu_profile_choices(profiles: list[HardwareProfile], vid_pid: str) -> list[dict[str, Any]]:
    choices = []
    for profile in profiles:
        profile_vid_pid = str(profile.initial_flash.get("dfu_vid_pid") or "").lower()
        vendor_dfu = str(profile.initial_flash.get("method") or "") == "cartographer_vendor_script"
        if profile_vid_pid != vid_pid.lower() and not vendor_dfu:
            continue
        family = profile.family.replace("_", " ").title() if profile.family else "Hardware"
        choices.append(
            {
                "id": profile.id,
                "name": f"{family} · {profile.name}",
                "score": None,
                "reasons": ["user_selection_required"],
            }
        )
    return sorted(choices, key=lambda item: str(item["name"]).lower())


def is_cartographer_serial_device(serial_device: dict[str, str]) -> bool:
    haystack = " ".join(
        str(serial_device.get(key) or "")
        for key in ("name", "path", "target")
    ).lower()
    return "cartographer" in haystack or "scanner" in haystack


def chip_from_serial_name(name: str) -> str | None:
    match = re.search(r"(stm32[a-z0-9]+xx|rp2040)", name.lower())
    return match.group(1) if match else None


def normalize_uuid(value: str) -> str:
    return value.replace(":", "").replace("-", "").removeprefix("0x").removeprefix("0X").lower()


def safe_readlink(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return ""


def load_can_nodes(can_interface: str, can_query_output: str | None, katapult_path: str | None) -> list[dict[str, str]]:
    if can_query_output:
        return parse_can_query_output(Path(can_query_output).read_text(encoding="utf-8"), can_interface)

    if katapult_path:
        return run_can_query(katapult_path, can_interface)

    return []


def parse_can_query_output(output: str, can_interface: str) -> list[dict[str, str]]:
    nodes: list[dict[str, str]] = []
    pending_uuid: str | None = None

    for line in output.splitlines():
        uuid_match = UUID_RE.search(line)
        app_match = APP_RE.search(line)

        if uuid_match:
            pending_uuid = uuid_match.group("uuid").lower()
            nodes.append({"uuid": pending_uuid, "application": "", "interface": can_interface})

        if app_match:
            application = app_match.group("app")
            if nodes and pending_uuid:
                nodes[-1]["application"] = application

    return nodes


def run_can_query(katapult_path: str, can_interface: str) -> list[dict[str, str]]:
    flashtool = Path(katapult_path).expanduser() / "scripts" / "flashtool.py"
    result = subprocess.run(
        ["python3", str(flashtool), "-i", can_interface, "-q"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return parse_can_query_output(result.stdout + "\n" + result.stderr, can_interface)


def match_serial_devices(serial_devices: list[dict[str, str]], serial: str | None) -> list[dict[str, str]]:
    if not serial:
        return []

    serial_name = Path(serial).name
    return [device for device in serial_devices if device["name"] == serial_name or device["path"] == serial]


def match_can_node(can_nodes: list[dict[str, str]], canbus_uuid: str | None) -> dict[str, str] | None:
    if not canbus_uuid:
        return None

    canbus_uuid = canbus_uuid.lower()
    for node in can_nodes:
        if node.get("uuid", "").lower() == canbus_uuid:
            return node

    return None
