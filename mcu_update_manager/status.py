from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .cartographer import inspect_cartographer_device, read_usb_to_can_state
from .devices import devices_by_id, load_devices
from .discovery import DiscoverOptions, discover
from .dfu_flash import profile_catalog
from .profiles import automatic_build_ready, load_profiles


def collect_status(
    *,
    printer_cfg: str,
    serial_dir: str = "/dev/serial/by-id",
    profile_paths: list[str] | None = None,
    can_interface: str = "can0",
    can_query_output: str | None = None,
    katapult_path: str | None = "~/katapult",
    klipper_path: str = "~/klipper",
    kalico_path: str = "~/kalico",
    moonraker_url: str | None = "http://127.0.0.1:7125",
    devices_path: str = "devices.yaml",
    firmware_project: str = "kalico",
    firmware_ref: str = "current",
    can_bitrate: str = "1000000",
    cartographer_firmware_path: str | None = "~/cartographer_firmware",
    build_root: str = "builds",
    artifact_root: str = "artifacts",
    refresh_repositories: bool = False,
    printer_objects_json: str | None = None,
) -> dict[str, Any]:
    profiles = load_profiles(profile_paths or ["profiles"])
    discovery = discover(
        DiscoverOptions(
            printer_cfg=printer_cfg,
            serial_dir=serial_dir,
            can_interface=can_interface,
            can_query_output=can_query_output,
            katapult_path=katapult_path,
            klipper_path=klipper_path,
            kalico_path=kalico_path,
            moonraker_url=moonraker_url,
            refresh_repositories=refresh_repositories,
            printer_objects_json=printer_objects_json,
            devices_path=devices_path,
            firmware_project=firmware_project,
            firmware_ref=firmware_ref,
            can_bitrate=can_bitrate,
            cartographer_firmware_path=cartographer_firmware_path,
        ),
        profiles=profiles,
    )

    confirmed = devices_by_id(load_devices(devices_path))
    append_pending_usb_to_can_device(
        discovery,
        confirmed,
        cartographer_firmware_path=cartographer_firmware_path,
        can_bitrate=can_bitrate,
        build_root=build_root,
    )
    profiles_by_id = {profile.id: profile for profile in profiles}
    devices = []
    has_previous_artifact = False
    for device in discovery.get("devices", []):
        item = dict(device)
        item["artifact"] = latest_artifact_for_device(
            device_id=str(item.get("id")),
            firmware_ref=firmware_ref,
            build_root=build_root,
            artifact_root=artifact_root,
        )
        item["artifact_history"] = artifact_history_for_device(
            device_id=str(item.get("id")),
            build_root=build_root,
        )
        item["last_operation"] = latest_operation_for_device(
            device_id=str(item.get("id")),
            build_root=build_root,
        )
        item["firmware_state"] = firmware_state(item, item.get("artifact"))
        item["last_operation"] = reconcile_last_operation(item.get("last_operation"), item.get("firmware_state"))
        has_previous_artifact = has_previous_artifact or bool(
            item["artifact"] and item["artifact"].get("exists", True)
        )
        profile_id = item.get("confirmed_profile") or (confirmed.get(str(item.get("id"))) or {}).get("confirmed_profile")
        item["actions"] = device_actions(
            item,
            confirmed.get(str(item.get("id"))),
            profiles_by_id.get(str(profile_id)),
            item.get("firmware_state"),
        )
        item["printer_cfg_snippet"] = printer_cfg_snippet(item)
        item["recovery"] = recovery_state(item)
        devices.append(item)

    firmware_source = dict(discovery.get("firmware_source", {}))
    mark_previous_artifact_option(firmware_source, has_previous_artifact)

    return {
        "project": "MCU Update Manager",
        "schema_version": 1,
        "status": "ok",
        "firmware_source": firmware_source,
        "preflight": discovery.get("preflight", {}),
        "discovery": discovery.get("discovery", {}),
        "serial_devices": discovery.get("serial_devices", []),
        "can_nodes": discovery.get("can_nodes", []),
        "profile_catalog": profile_catalog(profile_paths or ["profiles"]),
        "summary": status_summary(devices),
        "devices": devices,
    }


def status_summary(devices: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(devices),
        "ready": sum(1 for item in devices if item.get("confirmation_status") == "confirmed" and not item.get("recovery", {}).get("active")),
        "needs_confirmation": sum(1 for item in devices if item.get("confirmation_status") != "confirmed"),
        "dfu": sum(1 for item in devices if str(item.get("transport") or "").lower() == "dfu"),
        "issues": sum(1 for item in devices if item.get("recovery", {}).get("active")),
    }


def printer_cfg_snippet(device: dict[str, Any]) -> str | None:
    name = str(device.get("name") or device.get("id") or "mcu").strip().replace(" ", "_")
    section = "mcu" if name == "mcu" else f"mcu {name}"
    if device.get("canbus_uuid"):
        interface = device.get("can_interface") or "can0"
        return f"[{section}]\ncanbus_uuid: {device['canbus_uuid']}\ncanbus_interface: {interface}"
    if device.get("serial"):
        return f"[{section}]\nserial: {device['serial']}"
    return None


def recovery_state(device: dict[str, Any]) -> dict[str, Any]:
    operation = device.get("last_operation") or {}
    failed = operation.get("status") == "failed"
    transport = str(device.get("transport") or "").lower()
    return {
        "active": failed,
        "mode": "dfu" if transport == "dfu" else "usb" if transport == "usb" else "can" if transport == "can" else "unknown",
        "can_rescan": True,
        "can_retry": bool(device.get("confirmed_profile")),
        "has_last_good": any(item.get("kind") == "last_good" for item in device.get("artifact_history", [])),
    }


def artifact_history_for_device(*, device_id: str, build_root: str) -> list[dict[str, Any]]:
    root = Path(build_root).expanduser() / device_id
    entries: list[dict[str, Any]] = []
    if root.exists():
        manifests = sorted(
            [*root.glob("*/artifact.json"), *root.glob("history/*/artifact.json")],
            key=lambda path: path.stat().st_mtime, reverse=True,
        )
        for path in manifests:
            artifact = read_artifact_manifest(path)
            entries.append(
                {
                    **artifact,
                    "kind": "built",
                    "ref": f"history:{path.parent.name}" if path.parent.parent.name == "history" else path.parent.name,
                    "label": (artifact.get("firmware_source") or {}).get("current_version") or path.parent.name,
                    "created_at": (artifact.get("build") or {}).get("completed_at"),
                }
            )
        last_good = root / "last-good-artifact.json"
        if last_good.exists():
            try:
                data = json.loads(last_good.read_text(encoding="utf-8"))
                artifact = data.get("artifact") or {}
                manifest_path = Path(str(data.get("manifest") or "")).expanduser()
                restore_ref = manifest_path.parent.name if manifest_path.name == "artifact.json" else None
                if restore_ref == "last-good":
                    restore_ref = "last_good"
                if not restore_ref:
                    matching_entry = next(
                        (entry for entry in entries if entry.get("path") == artifact.get("path")),
                        None,
                    )
                    restore_ref = (matching_entry or {}).get("ref")
                entries.insert(
                    0,
                    {
                        "kind": "last_good",
                        "ref": restore_ref or "last_good",
                        "label": "Last successfully flashed firmware",
                        "path": artifact.get("path"),
                        "size": artifact.get("size"),
                        "sha256": artifact.get("sha256"),
                        "created_at": data.get("flashed_at"),
                        "exists": bool(artifact.get("path") and Path(str(artifact.get("path"))).expanduser().exists()),
                    },
                )
            except (OSError, json.JSONDecodeError):
                pass
    return entries


def mark_previous_artifact_option(firmware_source: dict[str, Any], available: bool) -> None:
    repositories = firmware_source.get("repositories")
    if not isinstance(repositories, dict):
        return

    option_lists = [repositories.get("version_options")]
    for source in repositories.get("sources", []):
        if isinstance(source, dict):
            option_lists.append(source.get("version_options"))

    for options in option_lists:
        if not isinstance(options, list):
            continue

        for option in options:
            if not isinstance(option, dict) or option.get("kind") != "previous_artifact":
                continue

            option["available"] = available
            if available:
                option["label"] = "Previous built firmware artifact"


def device_actions(
    device: dict[str, Any],
    confirmed: dict[str, Any] | None,
    profile: Any | None = None,
    firmware_state_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    has_profile = bool(device.get("confirmed_profile") or (confirmed or {}).get("confirmed_profile"))
    has_uuid_or_serial = bool(device.get("canbus_uuid") or device.get("serial"))
    katapult_can = device.get("transport") == "can"
    direct_usb_flash = bool(
        profile
        and profile.flash.get("method") in {"klipper_make_flash_usb", "usb_make_flash"}
        and device.get("transport") == "usb"
        and device.get("serial")
    )
    vendor_firmware = device.get("vendor_firmware") or {}
    is_vendor_managed = vendor_firmware.get("manager") == "cartographer"
    is_cartographer_usb = is_vendor_managed and device.get("transport") == "usb"
    is_cartographer_can = is_vendor_managed and device.get("transport") == "can"
    is_cartographer_pending_can = is_vendor_managed and device.get("transport") == "pending_can"
    is_cartographer_dfu = is_vendor_managed and device.get("transport") == "dfu"
    is_dfu = device.get("transport") == "dfu"
    requires_manual_menuconfig = bool(profile and profile.build.get("requires_manual_menuconfig"))
    requires_esoterical_image_extraction = bool(profile and profile.build.get("requires_esoterical_image_extraction"))
    requires_import_config = bool(profile and profile.build.get("import_config"))
    requires_exact_profile = bool(profile and profile.build.get("requires_exact_profile"))
    blocked_build = requires_manual_menuconfig or requires_esoterical_image_extraction or requires_import_config or requires_exact_profile

    return {
        "needs_confirmation": not has_profile,
        "can_build": has_profile and not is_vendor_managed and not is_dfu and not blocked_build and bool(profile and automatic_build_ready(profile.build)),
        "can_flash": has_profile and has_uuid_or_serial and (katapult_can or direct_usb_flash) and not is_vendor_managed,
        "can_verify": has_profile and has_uuid_or_serial,
        "already_matching": bool((firmware_state_data or {}).get("already_matching")),
        "needs_build": bool((firmware_state_data or {}).get("needs_build")),
        "needs_flash": bool((firmware_state_data or {}).get("needs_flash")),
        "vendor_managed": is_vendor_managed,
        "requires_manual_menuconfig": requires_manual_menuconfig,
        "requires_esoterical_image_extraction": requires_esoterical_image_extraction,
        "requires_import_config": requires_import_config,
        "requires_exact_profile": requires_exact_profile,
        "vendor_flash_ready": bool(
            is_vendor_managed
            and (is_cartographer_can or is_cartographer_pending_can)
            and has_uuid_or_serial
            and vendor_firmware.get("status") == "ok"
            and vendor_firmware.get("selected")
        ),
        "cartographer_can_to_usb_ready": bool(
            is_cartographer_can
            and device.get("canbus_uuid")
            and vendor_firmware.get("can_to_usb", {}).get("available")
        ),
        "cartographer_usb_to_can_ready": bool(
            is_cartographer_usb
            and has_uuid_or_serial
            and vendor_firmware.get("usb_to_can", {}).get("available")
        ),
        "cartographer_usb_update_ready": bool(
            is_cartographer_usb
            and has_profile
            and has_uuid_or_serial
            and vendor_firmware.get("status") == "ok"
            and vendor_firmware.get("selected")
        ),
        "cartographer_dfu_usb_ready": bool(
            is_cartographer_dfu
            and vendor_firmware.get("dfu_available_versions", {}).get("USB")
        ),
        "cartographer_dfu_can_ready": bool(
            is_cartographer_dfu
            and vendor_firmware.get("dfu_available_versions", {}).get("CAN")
        ),
        "cartographer_usb_to_can_flash_ready": bool(
            is_cartographer_pending_can
            and device.get("canbus_uuid")
            and vendor_firmware.get("status") == "ok"
            and vendor_firmware.get("selected")
        ),
    }


def firmware_state(device: dict[str, Any], artifact: dict[str, Any] | None) -> dict[str, Any]:
    artifact = artifact or {}
    runtime_version = device.get("firmware_version")
    artifact_version = (artifact.get("firmware_source") or {}).get("current_version")
    source_head = (artifact.get("firmware_source") or {}).get("source_head")
    artifact_ready = bool(artifact.get("exists") and artifact.get("status") == "ok")
    comparable = bool(runtime_version and artifact_version)
    already_matching = bool(comparable and str(runtime_version) == str(artifact_version))
    needs_build = not artifact_ready
    needs_flash = bool(artifact_ready and comparable and not already_matching)
    status = "unknown"
    if needs_build:
        status = "needs_build"
    elif already_matching:
        status = "already_matching"
    elif needs_flash:
        status = "needs_flash"
    elif artifact_ready:
        status = "artifact_ready"

    return {
        "status": status,
        "runtime_version": runtime_version,
        "artifact_version": artifact_version,
        "source_head": source_head,
        "artifact_ready": artifact_ready,
        "already_matching": already_matching,
        "needs_build": needs_build,
        "needs_flash": needs_flash,
    }


def reconcile_last_operation(operation: dict[str, Any] | None, state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not operation or operation.get("status") != "failed" or not (state or {}).get("already_matching"):
        return operation

    error = str(operation.get("error") or "").lower()
    failed_step_ids = {
        str(step.get("id"))
        for step in operation.get("steps", [])
        if isinstance(step, dict) and step.get("status") == "failed"
    }
    if "canbus_query.py" not in error and "verify_klipper" not in failed_step_ids:
        return operation

    reconciled = dict(operation)
    reconciled["status"] = "ok"
    reconciled["error"] = None
    reconciled["reconciled"] = "runtime_version_matches_artifact_after_verify_failure"
    return reconciled


def append_pending_usb_to_can_device(
    discovery: dict[str, Any],
    confirmed: dict[str, dict[str, Any]],
    *,
    cartographer_firmware_path: str | None,
    can_bitrate: str,
    build_root: str,
) -> None:
    state = read_usb_to_can_state(build_root)
    if not state:
        return

    devices = discovery.setdefault("devices", [])
    if any(str(device.get("id")) == "cartographer_usb_to_can_pending" for device in devices):
        return

    uuid = first_unassigned_katapult_uuid(discovery)
    device = {
        "id": "cartographer_usb_to_can_pending",
        "mcu_section": None,
        "name": "cartographer usb -> can",
        "transport": "pending_can",
        "serial": state.get("usb_serial"),
        "canbus_uuid": uuid,
        "can_interface": state.get("can_interface") or discovery.get("discovery", {}).get("can_interface") or "can0",
        "detected_chip": "stm32g431xx",
        "firmware_version": None,
        "referenced_pins": [],
        "runtime_app": "Katapult" if uuid else "Awaiting CAN reconnect",
        "config_source": None,
        "matching_serial_devices": [],
        "can_node": {"uuid": uuid, "application": "Katapult", "interface": state.get("can_interface") or "can0"} if uuid else None,
        "discovery_source": "workflow_state",
        "workflow": {
            "type": "cartographer_usb_to_can",
            "status": "can_uuid_detected" if uuid else "awaiting_can_reconnect",
            "message": state.get("message"),
            "updated_at": state.get("updated_at"),
        },
        "likely_profiles": [],
        "confirmation_status": "confirmed",
        "confirmed_profile": "cartographer_can",
    }
    vendor = inspect_cartographer_device(
        device,
        cartographer_firmware_path=cartographer_firmware_path,
        can_bitrate=str(state.get("can_bitrate") or can_bitrate),
    )
    if vendor:
        device["vendor_firmware"] = vendor
    devices.append(device)


def first_unassigned_katapult_uuid(discovery: dict[str, Any]) -> str | None:
    configured = {
        str(device.get("canbus_uuid") or "").lower()
        for device in discovery.get("devices", [])
        if device.get("canbus_uuid") and device.get("discovery_source") != "can_query_unconfigured"
    }
    for node in discovery.get("can_nodes", []):
        uuid = str(node.get("uuid") or "").lower()
        app = str(node.get("application") or "").lower()
        if uuid and uuid not in configured and app in {"katapult", "canboot"}:
            return uuid
    return None


def latest_artifact_for_device(
    *,
    device_id: str,
    firmware_ref: str,
    build_root: str,
    artifact_root: str,
) -> dict[str, Any] | None:
    candidates = [
        Path(build_root).expanduser() / device_id / sanitize_path_part(firmware_ref) / "artifact.json",
        Path(build_root).expanduser() / device_id / "current" / "artifact.json",
    ]

    manifests = [path for path in candidates if path.exists()]
    if not manifests:
        manifests = sorted(
            (Path(build_root).expanduser() / device_id).glob("*/artifact.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    if manifests:
        return read_artifact_manifest(manifests[0])

    artifact_dir = Path(artifact_root).expanduser()
    artifacts = sorted(
        artifact_dir.glob(f"{device_id}-*.bin"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if artifacts:
        artifact = artifacts[0]
        return {
            "status": "artifact_without_manifest",
            "path": str(artifact.resolve()),
            "size": artifact.stat().st_size,
        }

    return None


def latest_operation_for_device(*, device_id: str, build_root: str) -> dict[str, Any] | None:
    root = Path(build_root).expanduser() / device_id
    candidates = [
        root / "current" / "build-result.json",
        root / "current" / "flash-result.json",
        root / "cartographer" / "flash-result.json",
        root / "dfu-flash-result.json",
    ]
    if root.exists():
        candidates.extend(root.glob("*/build-result.json"))
        candidates.extend(root.glob("*/flash-result.json"))
        candidates.extend(root.glob("**/dfu-flash-result.json"))
        candidates.extend(root.glob("**/switch-result.json"))

    existing = sorted(
        {path.resolve() for path in candidates if path.exists()},
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not existing:
        return None

    path = existing[0]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid_result",
            "metadata": str(path),
            "error": str(exc),
        }

    operation = data.get("flash") if isinstance(data.get("flash"), dict) else None
    operation = operation or (data.get("build") if isinstance(data.get("build"), dict) else None)
    operation = operation or data
    log_path = (
        operation.get("flash_log")
        or operation.get("build_log")
        or data.get("log")
        or (data.get("workspace") or {}).get("log")
    )
    status = operation.get("status") or data.get("status")
    error = operation.get("error") or data.get("error")
    result = {
        "action": data.get("action"),
        "status": status,
        "error": error,
        "metadata": str(path),
        "log": log_path,
        "current_step": operation.get("current_step") or data.get("current_step"),
        "steps": operation.get("steps") or data.get("steps") or [],
        "started_at": operation.get("started_at") or data.get("started_at"),
        "completed_at": operation.get("completed_at") or data.get("completed_at"),
        "detected_serial": operation.get("detected_serial") or data.get("detected_serial"),
        "printer_cfg_snippet": operation.get("printer_cfg_snippet") or data.get("printer_cfg_snippet"),
    }
    if log_path:
        result["log_tail"] = read_log_tail(Path(str(log_path)).expanduser())
    return result


def read_log_tail(path: Path, max_lines: int = 24) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"Could not read log {path}: {exc}"

    return "\n".join(lines[-max_lines:])


def read_artifact_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid_manifest",
            "manifest": str(path.resolve()),
            "error": str(exc),
        }

    artifact = data.get("artifact") or {}
    artifact_path = artifact.get("path")
    return {
        "status": "ok",
        "manifest": str(path.resolve()),
        "path": artifact_path,
        "size": artifact.get("size"),
        "sha256": artifact.get("sha256"),
        "source": data.get("source"),
        "firmware_source": data.get("firmware_source"),
        "profile": data.get("profile"),
        "build": data.get("build"),
        "exists": bool(artifact_path and Path(str(artifact_path)).expanduser().exists()),
    }


def sanitize_path_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in value)
