"""User-owned hardware profiles kept outside the application checkout."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import json
import hashlib
import re
import unicodedata

from .profiles import HardwareProfile, load_profiles, load_simple_yaml


SUPPORTED_PROCESSORS = {
    "STM32F072", "STM32F103", "STM32F405", "STM32F407", "STM32F429",
    "STM32F446", "STM32G0B1", "STM32H723", "STM32H743", "RP2040",
}
FLASH_METHODS = {"can_katapult", "usb_katapult_or_make_flash", "klipper_make_flash_usb", "usb_make_flash"}
FIELDS = {
    "name", "vendor", "family", "chip", "transport", "architecture", "processor",
    "clock_reference", "bootloader_offset", "communication", "can_rx_pin", "can_tx_pin",
    "usb_pins", "flash_method", "use_katapult", "application_start_offset",
    "bootloader_communication", "bootloader_clock_reference", "bootloader_can_rx_pin",
    "bootloader_can_tx_pin", "bootloader_usb_pins", "initial_flash_method", "dfu_vid_pid",
}


def profile_fields(profile: HardwareProfile) -> dict[str, Any]:
    build = profile.build
    boot = profile.bootloader
    initial = profile.initial_flash
    return {
        "name": profile.name,
        "vendor": profile.vendor,
        "family": profile.family,
        "chip": next(iter(profile.match.get("chips", [])), ""),
        "transport": next(iter(profile.match.get("transports", [])), ""),
        "architecture": build.get("architecture", ""),
        "processor": build.get("processor", ""),
        "clock_reference": build.get("clock_reference", ""),
        "bootloader_offset": build.get("bootloader_offset", ""),
        "communication": build.get("communication", ""),
        "can_rx_pin": build.get("can_rx_pin", ""),
        "can_tx_pin": build.get("can_tx_pin", ""),
        "usb_pins": build.get("usb_pins", ""),
        "flash_method": profile.flash.get("method", ""),
        "use_katapult": bool(boot),
        "application_start_offset": boot.get("application_start_offset", ""),
        "bootloader_communication": boot.get("communication", ""),
        "bootloader_clock_reference": boot.get("clock_reference", ""),
        "bootloader_can_rx_pin": boot.get("can_rx_pin", ""),
        "bootloader_can_tx_pin": boot.get("can_tx_pin", ""),
        "bootloader_usb_pins": boot.get("usb_pins", ""),
        "initial_flash_method": initial.get("method", ""),
        "dfu_vid_pid": initial.get("dfu_vid_pid", ""),
    }


def get_profile(profiles: list[HardwareProfile], profile_id: str) -> HardwareProfile:
    for profile in profiles:
        if profile.id == profile_id:
            return profile
    raise ValueError(f"Profile not found: {profile_id}")


def is_custom_profile(profile: HardwareProfile, custom_dir: str | Path) -> bool:
    return Path(profile.path).resolve().is_relative_to(Path(custom_dir).expanduser().resolve())


def save_custom_profile(
    profile_paths: list[str | Path],
    custom_dir: str | Path,
    fields: dict[str, Any],
    *,
    template_id: str | None = None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(fields, dict) or set(fields) - FIELDS:
        raise ValueError("Profile contains unsupported fields.")
    if any(not isinstance(value, (str, bool)) for value in fields.values()):
        raise ValueError("Profile fields must be text or a Katapult checkbox.")

    profiles = load_profiles(profile_paths)
    own_dir = Path(custom_dir).expanduser().resolve()
    if profile_id:
        base = get_profile(profiles, profile_id)
        if not is_custom_profile(base, own_dir):
            raise ValueError("Catalog profiles cannot be edited. Copy the profile first.")
        identifier = base.id
    else:
        base = get_profile(profiles, template_id) if template_id else None
        name = str(fields.get("name", "")).strip()
        ascii_name = "".join(char for char in unicodedata.normalize("NFKD", name) if not unicodedata.combining(char))
        slug = re.sub(r"[^a-z0-9]+", "_", ascii_name.casefold()).strip("_")
        identifier = "user_" + (slug[:70] or hashlib.sha256(name.encode()).hexdigest()[:12])
        if not re.fullmatch(r"user_[a-z0-9_]{2,80}", identifier):
            raise ValueError("Enter a name using Latin letters or numbers (at least two characters).")
        if any(item.id.casefold() == identifier.casefold() for item in profiles):
            raise ValueError(f"Profile ID already exists: {identifier}")

    merged = profile_fields(base) if base else {}
    merged.update({key: value.strip() if isinstance(value, str) else value for key, value in fields.items()})
    name = str(merged.get("name", "")).strip()
    if len(name) < 2 or len(name) > 100 or any(ord(char) < 32 for char in name):
        raise ValueError("Profile name must contain 2-100 printable characters.")
    if len(str(merged.get("vendor", ""))) > 80 or merged.get("family") not in {"mainboard", "toolhead", "expansion", "cartographer", "beacon"}:
        raise ValueError("Choose a valid category and keep the vendor name under 80 characters.")
    normalized = unicodedata.normalize("NFKC", name).casefold().strip()
    if any(unicodedata.normalize("NFKC", item.name).casefold().strip() == normalized and item.id != identifier for item in profiles):
        raise ValueError(f"Profile name already exists: {name}")

    chip = str(merged.get("chip", "")).lower()
    transport = str(merged.get("transport", ""))
    architecture = str(merged.get("architecture", ""))
    processor = str(merged.get("processor", ""))
    communication = str(merged.get("communication", ""))
    method = str(merged.get("flash_method", ""))
    offset = str(merged.get("bootloader_offset", ""))
    if not re.fullmatch(r"[a-z0-9_]{3,40}", chip):
        raise ValueError("Enter a valid MCU chip name, for example stm32g0b1xx.")
    if transport not in {"can", "usb"} or architecture not in {"stm32", "rp2040"}:
        raise ValueError("Only STM32/RP2040 USB and CAN profiles are supported by this editor.")
    if processor not in SUPPORTED_PROCESSORS or (architecture == "rp2040") != (processor == "RP2040"):
        raise ValueError(f"Processor is not supported by the automatic config generator: {processor}")
    if communication not in {"usb", "canbus", "usb_to_canbus_bridge"} or method not in FLASH_METHODS:
        raise ValueError("Select a supported communication and flash method.")
    if transport == "usb" and communication != "usb":
        raise ValueError("A USB device must use USB communication. USB-CAN bridges appear as CAN devices.")
    if transport == "can" and communication == "usb":
        raise ValueError("A CAN device must use CAN or USB-CAN bridge communication.")
    if transport == "usb" and method not in {"klipper_make_flash_usb", "usb_make_flash"}:
        raise ValueError("Select a direct USB flash method for a USB device.")
    if transport == "can" and method not in {"can_katapult", "usb_katapult_or_make_flash"}:
        raise ValueError("Select a CAN Katapult flash method for a CAN device.")
    if architecture == "stm32" and offset not in {"No bootloader", "8KiB", "16KiB", "32KiB", "64KiB", "128KiB"}:
        raise ValueError("Choose a supported STM32 bootloader offset.")
    if architecture == "rp2040" and offset not in {"No bootloader", "16KiB"}:
        raise ValueError("Choose a supported RP2040 bootloader offset.")
    if communication != "usb" and not (merged.get("can_rx_pin") and merged.get("can_tx_pin")):
        raise ValueError("CAN communication requires RX and TX pins.")
    if architecture == "stm32" and communication != "canbus" and merged.get("usb_pins") != "PA11/PA12":
        raise ValueError("The automatic STM32 generator currently supports USB only on PA11/PA12.")
    if merged.get("use_katapult") and offset == "No bootloader":
        raise ValueError("Katapult requires a nonzero Klipper bootloader offset.")
    if merged.get("use_katapult") and str(merged.get("application_start_offset", "")) != offset:
        raise ValueError("Katapult application start offset must match the Klipper bootloader offset.")
    boot_comm = str(merged.get("bootloader_communication") or communication)
    if merged.get("use_katapult") and boot_comm not in {"usb", "canbus"}:
        raise ValueError("Katapult must communicate over USB or CAN.")
    if merged.get("use_katapult") and boot_comm == "canbus" and not (merged.get("bootloader_can_rx_pin") and merged.get("bootloader_can_tx_pin")):
        raise ValueError("Katapult CAN communication requires RX and TX pins.")
    if merged.get("use_katapult") and architecture == "stm32" and not merged.get("bootloader_clock_reference"):
        raise ValueError("Katapult requires a clock reference for STM32.")
    if merged.get("use_katapult") and architecture == "stm32" and boot_comm == "usb" and merged.get("bootloader_usb_pins") != "PA11/PA12":
        raise ValueError("The automatic STM32 generator currently supports Katapult USB only on PA11/PA12.")
    initial_method = str(merged.get("initial_flash_method", ""))
    if initial_method not in {"dfu_util", "klipper_make_flash_dfu", "rp2040_bootsel_make_flash"}:
        raise ValueError("Select a supported initial flash method.")
    if architecture == "stm32" and initial_method == "rp2040_bootsel_make_flash":
        raise ValueError("RP2040 BOOTSEL cannot be used for STM32.")
    if architecture == "rp2040" and initial_method != "rp2040_bootsel_make_flash":
        raise ValueError("RP2040 profiles require BOOTSEL initial flashing.")
    vid_pid = str(merged.get("dfu_vid_pid", ""))
    if architecture == "stm32" and not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}", vid_pid):
        raise ValueError("Enter a DFU VID:PID such as 0483:df11.")

    original = load_simple_yaml(base.path) if base else {}
    if base and "profiles" in original:
        original = next(item for item in original["profiles"] if item["id"] == base.id)
    data = deepcopy(original)
    old_build = dict(data.get("build", {}))
    build = dict(old_build)
    for key in ("architecture", "processor", "clock_reference", "bootloader_offset", "communication", "can_rx_pin", "can_tx_pin", "usb_pins"):
        if merged.get(key):
            build[key] = merged[key]
        else:
            build.pop(key, None)
    build["firmware"] = "klipper"
    for flag in ("requires_manual_menuconfig", "requires_esoterical_image_extraction", "import_config", "requires_exact_profile"):
        build.pop(flag, None)
    if communication != "usb":
        build["can_bitrate"] = "{{ can_bitrate }}"
    else:
        build.pop("can_bitrate", None)
    hardware_changed = any(old_build.get(key) != build.get(key) for key in ("architecture", "processor", "clock_reference", "bootloader_offset", "communication", "can_rx_pin", "can_tx_pin", "usb_pins"))
    if hardware_changed:
        build.pop("kconfig_overrides", None)
        build.pop("gpio_pins_on_startup", None)
        build.pop("mcu_name", None)
    from .prepare_build import generate_klipper_dot_config
    generate_klipper_dot_config(build)
    data.update({"id": identifier, "name": name, "vendor": str(merged.get("vendor", "Custom")), "family": str(merged.get("family", "toolhead"))})
    data["match"] = {"chips": [chip], "transports": [transport]}
    data.pop("fingerprint", None)
    data.pop("source_images", None)
    data["source"] = ["custom"]
    if template_id and not profile_id:
        data["template_id"] = template_id
    data["build"] = build
    if merged.get("use_katapult"):
        boot = dict(data.get("bootloader", {})) if not hardware_changed else {}
        boot.update({"type": "katapult", "firmware": "katapult", "architecture": architecture, "processor": processor,
                     "application_start_offset": offset,
                     "communication": str(merged.get("bootloader_communication") or communication)})
        for flag in ("requires_manual_menuconfig", "requires_esoterical_image_extraction", "import_config", "requires_exact_profile"):
            boot.pop(flag, None)
        if boot_comm == "canbus":
            boot["can_bitrate"] = "{{ can_bitrate }}"
        else:
            boot.pop("can_bitrate", None)
        for key, field in (("clock_reference", "bootloader_clock_reference"), ("can_rx_pin", "bootloader_can_rx_pin"),
                           ("can_tx_pin", "bootloader_can_tx_pin"), ("usb_pins", "bootloader_usb_pins")):
            if merged.get(field):
                boot[key] = merged[field]
            else:
                boot.pop(key, None)
        data["bootloader"] = boot
        generate_klipper_dot_config(boot)
    else:
        data.pop("bootloader", None)
    initial = {"method": initial_method}
    if architecture == "stm32":
        initial["dfu_vid_pid"] = vid_pid.lower()
    data["initial_flash"] = initial
    data.pop("update", None)
    data["flash"] = {"method": method}
    data.pop("notes", None)

    own_dir.mkdir(parents=True, exist_ok=True)
    target = own_dir / f"{identifier}.yaml"
    if target.exists() and not profile_id:
        raise ValueError(f"Profile file already exists: {target}")
    temp = own_dir / f".{identifier}.tmp"
    try:
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    return {"id": identifier, "name": name, "path": str(target), "fields": profile_fields(get_profile(load_profiles(profile_paths), identifier))}
