from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re


PROCESSOR_CHIPS = {
    "STM32F072": "stm32f072xx",
    "STM32F407": "stm32f407xx",
    "STM32F429": "stm32f429xx",
    "STM32F446": "stm32f446xx",
    "STM32G0B1": "stm32g0b1xx",
    "STM32H723": "stm32h723xx",
    "STM32H743": "stm32h743xx",
    "RP2040": "rp2040",
}


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    source_path = repo_root / "esoterical-extracted-config.json"
    target_path = repo_root / "profiles" / "mainboard" / "esoterical_extracted_mainboards.yaml"
    data = json.loads(source_path.read_text(encoding="utf-8-sig"))
    lines = [
        "# Auto-generated from reviewed Esoterical menuconfig screenshots.",
        "# Regenerate with tools/generate_profiles_from_extracted.py after editing esoterical-extracted-config.json.",
        "profiles:",
    ]
    for record in data.get("records", []):
        lines.extend(profile_yaml(record))
    target_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def profile_yaml(record: dict[str, Any]) -> list[str]:
    title = str(record["title"])
    profile_id = "esoterical_" + slug(title) + "_usb_can_bridge"
    family = str(record.get("family") or "mainboard")
    vendor = vendor_name(title)
    katapult = dict(record.get("katapult") or {})
    klipper = dict(record.get("klipper") or {})
    processor = str(klipper.get("processor") or katapult.get("processor") or "")
    chip = PROCESSOR_CHIPS.get(processor)
    source_images = record.get("source_images") or []

    lines = [
        f"  - id: {profile_id}",
        f"    name: {title} USB-CAN bridge",
        f"    vendor: {vendor}",
        f"    family: {family}",
        "    source:",
        "      - https://canbus.esoterical.online/mainboard_flashing/common_hardware.html",
        "    source_images:",
    ]
    for image in source_images:
        lines.append(f"      - esoterical-images/{int(image):03d}.png")

    lines.extend(
        [
            "    match:",
            "      chips:",
        ]
    )
    if chip:
        lines.append(f"        - {chip}")
    lines.extend(
        [
            "      mcu_name_hints:",
        ]
    )
    for hint in hints(title):
        lines.append(f"        - {hint}")
    lines.extend(
        [
            "      transports:",
            "        - usb",
            "        - can",
            "    build:",
            "      firmware: klipper",
        ]
    )
    lines.extend(nested_config_yaml(klipper, indent=6))
    lines.extend(
        [
            "    bootloader:",
            "      firmware: katapult",
            "      type: katapult",
        ]
    )
    lines.extend(nested_config_yaml(katapult, indent=6))
    lines.extend(initial_flash_yaml(katapult))
    lines.extend(update_yaml())
    lines.extend(
        [
            "    flash:",
            "      method: usb_katapult_or_make_flash",
        ]
    )
    return lines


def nested_config_yaml(config: dict[str, Any], indent: int) -> list[str]:
    prefix = " " * indent
    lines = []
    for key, value in config.items():
        if key == "can_bitrate":
            value = "{{ can_bitrate }}"
        lines.append(f"{prefix}{key}: {scalar(value)}")
    return lines


def initial_flash_yaml(katapult: dict[str, Any]) -> list[str]:
    architecture = str(katapult.get("architecture") or "")
    if architecture == "rp2040":
        return [
            "    initial_flash:",
            "      method: rp2040_bootsel_make_flash",
            "      bootloader: katapult",
            "      boot_vid_pid: \"2e8a:0003\"",
            "      physical_steps:",
            "        - Hold BOOTSEL.",
            "        - Connect USB while holding BOOTSEL.",
            "        - Release BOOTSEL after the RP2040 boot device appears.",
        ]
    return [
        "    initial_flash:",
        "      method: dfu_util",
        "      bootloader: katapult",
        "      dfu_vid_pid: \"0483:df11\"",
        "      artifact: \"~/katapult/out/katapult.bin\"",
        "      command: \"dfu-util -R -a 0 -s 0x08000000:mass-erase:force:leave -D ~/katapult/out/katapult.bin -d 0483:df11\"",
        "      physical_steps:",
        "        - Put the board into STM32 DFU mode.",
        "        - Connect USB to the board.",
        "        - Verify that 0483:df11 is visible before flashing.",
    ]


def update_yaml() -> list[str]:
    return [
        "    update:",
        "      method: usb_katapult_or_make_flash",
        "      stop_klipper: true",
        "      enter_bootloader: \"make flash FLASH_DEVICE={{ serial }}\"",
        "      flash: \"make flash FLASH_DEVICE={{ serial }}\"",
        "      start_klipper: true",
    ]


def scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if not text:
        return "\"\""
    if any(ch in text for ch in [":", "{", "}", "#"]):
        return json.dumps(text)
    return text


def vendor_name(title: str) -> str:
    lower = title.lower()
    if lower.startswith("bigtreetech"):
        return "BigTreeTech"
    if lower.startswith("fysetc"):
        return "FYSETC"
    if lower.startswith("mellow"):
        return "Mellow"
    if lower.startswith("mks"):
        return "MKS"
    if lower.startswith("ldo"):
        return "LDO"
    return "Esoterical"


def hints(title: str) -> list[str]:
    raw = re.split(r"[^a-z0-9]+", title.lower())
    ignored = {"bigtreetech", "fysetc", "stm32", "v", "usb", "can", "bridge"}
    result = []
    for item in raw:
        if len(item) < 2 or item in ignored:
            continue
        if item not in result:
            result.append(item)
    return result[:5] or [slug(title)]


def slug(title: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return value.replace("v1_0", "v10").replace("v2_0", "v20").replace("v3_0", "v30")


if __name__ == "__main__":
    main()
