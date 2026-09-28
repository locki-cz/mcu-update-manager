from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .devices import devices_by_id, load_devices
from .firmware_source import inspect_firmware_sources
from .profiles import HardwareProfile, automatic_build_ready, load_profiles


def create_build_plan(
    device_id: str,
    firmware_ref: str,
    devices_path: str | Path,
    discovery_path: str | Path,
    profile_paths: list[str | Path],
    klipper_path: str | None,
    kalico_path: str | None,
    can_bitrate: str = "1000000",
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

    firmware_sources = inspect_firmware_sources(klipper_path=klipper_path, kalico_path=kalico_path)
    active_source = first_available_source(firmware_sources)
    if not active_source:
        raise ValueError("No firmware source repository is available.")

    profile = profiles[profile_id]
    if not automatic_build_ready(profile.build):
        raise ValueError(f"Profile lacks verified settings for automatic build: {profile_id}")
    if profile.build.get("requires_manual_menuconfig"):
        raise ValueError(f"Profile requires manual menuconfig before automatic build: {profile_id}")
    if profile.build.get("requires_esoterical_image_extraction"):
        raise ValueError(f"Profile requires verified Esoterical screenshot extraction before automatic build: {profile_id}")
    if profile.build.get("import_config"):
        raise ValueError(
            f"Profile requires importing or selecting an exact existing Klipper .config before automatic build: {profile_id}"
        )
    if profile.build.get("requires_exact_profile"):
        raise ValueError(
            f"Profile is only a fallback template. Select the exact hardware profile before automatic build: {profile_id}"
        )

    device = discovered[device_id]
    rendered_build = render_template_values(profile.build, {"can_bitrate": can_bitrate})

    build_config_name = (
        confirmed.get("build_config_name")
        or rendered_build.get("build_config_name")
        or device_id
    )
    artifact_name = f"{device_id}-{profile_id}-{firmware_ref}.bin".replace("/", "_")
    return {
        "action": "build_plan",
        "device": {
            "id": device_id,
            "mcu_section": device.get("mcu_section"),
            "transport": device.get("transport"),
            "can_interface": device.get("can_interface"),
            "canbus_uuid": device.get("canbus_uuid"),
            "serial": device.get("serial"),
            "detected_chip": device.get("detected_chip"),
        },
        "firmware_source": {
            "project": active_source.get("project"),
            "path": active_source.get("path"),
            "current_version": active_source.get("version"),
            "selected_ref": firmware_ref,
        },
        "profile": {
            "id": profile.id,
            "name": profile.name,
            "build": rendered_build,
            "flash": profile.flash,
        },
        "build_config": {
            "name": sanitize_path_part(str(build_config_name)),
            "source": "device" if confirmed.get("build_config_name") else "profile" if rendered_build.get("build_config_name") else "device_id",
        },
        "klipper_config_preview": klipper_config_preview(rendered_build),
        "outputs": {
            "build_dir": f"builds/{device_id}/{firmware_ref}",
            "artifact": f"artifacts/{artifact_name}",
        },
        "commands_preview": build_commands_preview(active_source.get("path"), firmware_ref),
        "execute": False,
    }


def first_available_source(firmware_sources: dict[str, Any]) -> dict[str, Any] | None:
    for source in firmware_sources.get("sources", []):
        if source.get("available"):
            return source

    return None


def render_template_values(data: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            rendered[key] = render_string(value, values)
        else:
            rendered[key] = value

    return rendered


def render_string(value: str, values: dict[str, str]) -> str:
    rendered = value
    for key, replacement in values.items():
        rendered = rendered.replace("{{ " + key + " }}", replacement)
        rendered = rendered.replace("{{" + key + "}}", replacement)

    return rendered


def klipper_config_preview(build: dict[str, Any]) -> dict[str, Any]:
    communication = build.get("communication")
    preview = {
        "architecture": build.get("architecture"),
        "processor": build.get("processor"),
        "clock_reference": build.get("clock_reference"),
        "bootloader_offset": build.get("bootloader_offset"),
        "communication": communication,
    }

    if communication == "canbus":
        preview.update(
            {
                "can_rx_pin": build.get("can_rx_pin"),
                "can_tx_pin": build.get("can_tx_pin"),
                "can_bitrate": build.get("can_bitrate"),
            }
        )

    if "gpio_pins_on_startup" in build:
        preview["gpio_pins_on_startup"] = build.get("gpio_pins_on_startup")

    return preview


def build_commands_preview(repo_path: str | None, firmware_ref: str) -> list[str]:
    commands = []
    if firmware_ref == "current":
        commands.append(f"git -C {repo_path} status --short")
    elif firmware_ref == "latest":
        commands.append(f"git -C {repo_path} fetch --tags --prune")
        commands.append(f"git -C {repo_path} worktree add --detach <temporary-source> @{{upstream}}")
    else:
        commands.append(f"git -C {repo_path} worktree add --detach <temporary-source> {firmware_ref}")

    commands.extend(["make olddefconfig", "make"])
    return commands


def sanitize_path_part(value: str) -> str:
    return value.replace("/", "_").replace("\\", "_").replace(":", "_")
