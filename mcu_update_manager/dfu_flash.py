from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen
import json
import subprocess
import time

from .firmware_source import inspect_firmware_sources
from .dfu_target import select_dfu_port
from .io_utils import atomic_write_json
from .prepare_build import generate_klipper_dot_config, sanitize_path_part
from .profiles import HardwareProfile, automatic_build_ready, load_profiles
from .custom_profiles import is_custom_profile


def profile_catalog(profile_paths: list[str | Path], custom_profile_dir: str | Path | None = None) -> list[dict[str, Any]]:
    profiles = load_profiles(profile_paths)
    items = []
    for profile in profiles:
        initial = profile.initial_flash
        build_ready = automatic_build_ready(profile.build)
        bootloader_ready = automatic_build_ready(profile.bootloader)
        items.append(
            {
                "id": profile.id,
                "name": profile.name,
                "vendor": profile.vendor,
                "family": profile.family,
                "path": profile.path,
                "custom": bool(custom_profile_dir and is_custom_profile(profile, custom_profile_dir)),
                "chips": profile.match.get("chips", []),
                "transports": profile.match.get("transports", []),
                "supports_dfu": initial.get("method") in {"dfu_util", "klipper_make_flash_dfu", "rp2040_bootsel_make_flash"},
                "supports_katapult": bootloader_ready,
                "supports_klipper": build_ready,
                "verification": {
                    "ready": build_ready and (not profile.bootloader or bootloader_ready),
                    "reasons": profile_verification_reasons(profile),
                },
                "settings": {
                    "klipper": profile_settings(profile.build),
                    "katapult": profile_settings(profile.bootloader),
                    "initial_flash_method": initial.get("method"),
                    "update_method": profile.flash.get("method") or profile.update.get("method"),
                    "sources": profile.source,
                },
                "custom_template": profile.build.get("firmware") == "klipper" and bool(profile.build.get("architecture") and profile.build.get("processor")),
                "initial_flash": {
                    "method": initial.get("method"),
                    "dfu_vid_pid": initial.get("dfu_vid_pid"),
                    "boot_vid_pid": initial.get("boot_vid_pid"),
                },
                "targets": firmware_targets(profile),
            }
        )

    return sorted(items, key=lambda item: (str(item["family"]), str(item["name"])))


def profile_settings(config: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "architecture", "processor", "clock_reference", "bootloader_offset",
        "application_start_offset", "communication", "can_rx_pin", "can_tx_pin",
        "can_rx_gpio", "can_tx_gpio", "usb_pins", "can_bitrate",
        "gpio_pins_on_startup", "status_led_pin", "support_double_click_reset",
    )
    return {key: config[key] for key in fields if config.get(key) is not None}


def profile_verification_reasons(profile: HardwareProfile) -> list[str]:
    reasons = []
    configs = (("Klipper/Kalico", profile.build), ("Katapult", profile.bootloader))
    for label, config in configs:
        if not config:
            continue
        if config.get("requires_esoterical_image_extraction"):
            reasons.append(f"{label}: menuconfig values have not been extracted from the source images")
        if config.get("requires_manual_menuconfig"):
            reasons.append(f"{label}: manual menuconfig verification is required")
        if config.get("requires_exact_profile"):
            reasons.append(f"{label}: exact board revision must be selected")
        if config.get("import_config"):
            reasons.append(f"{label}: a verified configuration must be imported")
        if not automatic_build_ready(config) and not any(config.get(key) for key in (
            "requires_esoterical_image_extraction", "requires_manual_menuconfig",
            "requires_exact_profile", "import_config",
        )):
            reasons.append(f"{label}: required build settings are incomplete")
    if not profile.build:
        reasons.append("Klipper/Kalico: build settings are not documented")
    return reasons


def firmware_targets(profile: HardwareProfile) -> list[dict[str, str]]:
    if profile.initial_flash.get("method") not in {"dfu_util", "klipper_make_flash_dfu", "rp2040_bootsel_make_flash"}:
        return []
    targets: list[dict[str, str]] = []
    if automatic_build_ready(profile.bootloader):
        boot_comm = str(profile.bootloader.get("communication") or "usb")
        targets.append({"kind": "katapult", "communication": boot_comm, "label": f"Katapult ({boot_comm})"})
        if boot_comm != "canbus" and profile.build.get("can_rx_pin") and profile.build.get("can_tx_pin"):
            targets.append({"kind": "katapult", "communication": "canbus", "label": "Katapult (CAN)"})

    if automatic_build_ready(profile.build) and not automatic_build_ready(profile.bootloader):
        build_comm = str(profile.build.get("communication") or "usb")
        targets.append({"kind": "klipper", "communication": build_comm, "label": f"Klipper ({build_comm})"})
        if profile.family == "mainboard" and profile.build.get("can_rx_pin") and profile.build.get("can_tx_pin"):
            targets.append({"kind": "klipper", "communication": "usb_to_canbus_bridge", "label": "Klipper USB-CAN bridge"})

    seen = set()
    unique = []
    for target in targets:
        key = (target["kind"], target["communication"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(target)
    return unique


def dfu_flash(
    *,
    profile_id: str,
    firmware_kind: str,
    communication: str,
    profile_paths: list[str | Path],
    klipper_path: str | None,
    kalico_path: str | None,
    katapult_path: str | None,
    build_root: str | Path = "builds",
    firmware_ref: str = "current",
    can_bitrate: str = "1000000",
    execute: bool = False,
    dfu_device_id: str | None = None,
    moonraker_url: str = "http://127.0.0.1:7125",
) -> dict[str, Any]:
    profiles = {profile.id: profile for profile in load_profiles(profile_paths)}
    if profile_id not in profiles:
        raise ValueError(f"Profile not found: {profile_id}")

    profile = profiles[profile_id]
    if profile.initial_flash.get("method") not in {"dfu_util", "klipper_make_flash_dfu", "rp2040_bootsel_make_flash"}:
        raise ValueError(f"Profile does not support automatic initial flashing: {profile_id}")
    build_config = target_build_config(profile, firmware_kind, communication, can_bitrate)
    source = source_for_target(firmware_kind, klipper_path, kalico_path, katapult_path)
    build_dir = (Path(build_root).expanduser() / sanitize_path_part(dfu_device_id or "dfu") / "dfu" / sanitize_path_part(profile_id) / firmware_kind / sanitize_path_part(communication)).resolve()
    config_path = build_dir / ".config.mcu-update-manager"
    log_path = build_dir / "dfu-flash.log"
    result_path = build_dir / "dfu-flash-result.json"
    artifact = artifact_path(source["path"], firmware_kind)
    commands = command_plan(source["path"], config_path, profile, firmware_kind, communication, artifact)

    result: dict[str, Any] = {
        "action": "dfu_flash",
        "execute": execute,
        "status": "planned",
        "profile": {
            "id": profile.id,
            "name": profile.name,
            "family": profile.family,
            "vendor": profile.vendor,
        },
        "target": {
            "firmware_kind": firmware_kind,
            "communication": communication,
            "firmware_ref": firmware_ref,
        },
        "source": source,
        "workspace": {
            "build_dir": str(build_dir),
            "config": str(config_path),
            "log": str(log_path),
            "metadata": str(result_path),
        },
        "artifact": str(artifact),
        "commands_preview": printable_commands(commands),
        "initial_flash": profile.initial_flash,
    }

    if not execute:
        return result

    result["dfu_device_id"] = dfu_device_id

    build_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_for_target(firmware_kind, build_config), encoding="utf-8")
    reset_log(log_path, result)
    result["status"] = "running"
    result["started_at"] = now_iso()
    result["current_step"] = None
    result["steps"] = [
        {"id": step_id, "label": step_label, "status": "pending"}
        for step_id, step_label in dfu_step_descriptions(firmware_kind, communication)
    ]
    atomic_write_json(result_path, result)
    existing_usb_serials = usb_firmware_serials(build_config)

    try:
        for index, command in enumerate(commands):
            if index == 3:
                require_idle_printer(moonraker_url)
                if "dfu-util" in command:
                    vid_pid = str(profile.initial_flash.get("dfu_vid_pid") or "0483:df11")
                    command = [*command, "-p", select_dfu_port(dfu_device_id, vid_pid)]
                    result["commands_preview"][index] = " ".join(command)
            step = result["steps"][index]
            step["status"] = "running"
            step["started_at"] = now_iso()
            result["current_step"] = step["id"]
            atomic_write_json(result_path, result)
            run_logged(command, log_path)
            step["status"] = "done"
            step["completed_at"] = now_iso()
            atomic_write_json(result_path, result)
        if firmware_kind == "klipper" and communication == "usb":
            step = result["steps"][len(commands)]
            step["status"] = "running"
            step["started_at"] = now_iso()
            result["current_step"] = step["id"]
            atomic_write_json(result_path, result)
            detected_serial = wait_for_new_usb_firmware_serial(build_config, existing_usb_serials, log_path)
            result["detected_serial"] = detected_serial
            mcu_name = str(build_config.get("mcu_name") or sanitize_path_part(profile.id))
            result["printer_cfg_snippet"] = f"[mcu {mcu_name}]\nserial: {detected_serial}"
            with log_path.open("a", encoding="utf-8") as log:
                log.write("\nAdd this MCU connection to printer.cfg:\n")
                log.write(result["printer_cfg_snippet"] + "\n")
            step["status"] = "done"
            step["completed_at"] = now_iso()
            atomic_write_json(result_path, result)
        result["status"] = "ok"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        if result.get("current_step"):
            for step in result["steps"]:
                if step["id"] == result["current_step"]:
                    step["status"] = "failed"
                    step["completed_at"] = now_iso()
                    break
    finally:
        result["current_step"] = None
        result["completed_at"] = now_iso()
        atomic_write_json(result_path, result)

    return result


def require_idle_printer(moonraker_url: str) -> None:
    url = moonraker_url.rstrip("/") + "/printer/objects/query?print_stats&idle_timeout"
    try:
        with urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        status = payload.get("result", {}).get("status", {})
        print_state = str(status.get("print_stats", {}).get("state") or "").lower()
        idle_state = str(status.get("idle_timeout", {}).get("state") or "").lower()
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        raise RuntimeError("Could not verify printer state immediately before DFU flash.") from exc
    if print_state in {"printing", "paused"} or idle_state == "printing":
        raise RuntimeError("DFU flash blocked because the printer is printing or paused.")
    if not print_state and idle_state not in {"ready", "idle"}:
        raise RuntimeError("Could not verify idle printer state immediately before DFU flash.")


def target_build_config(profile: HardwareProfile, firmware_kind: str, communication: str, can_bitrate: str) -> dict[str, Any]:
    if firmware_kind == "katapult":
        if not automatic_build_ready(profile.bootloader):
            raise ValueError(f"Profile has no Katapult bootloader config: {profile.id}")
        config = dict(profile.bootloader)
    elif firmware_kind == "klipper":
        if not automatic_build_ready(profile.build):
            raise ValueError(f"Profile has no Klipper build config: {profile.id}")
        config = dict(profile.build)
    else:
        raise ValueError(f"Unsupported firmware_kind: {firmware_kind}")

    config["communication"] = communication
    config["can_bitrate"] = can_bitrate
    if communication in {"canbus", "usb_to_canbus_bridge"}:
        has_stm32_can = config.get("can_rx_pin") and config.get("can_tx_pin")
        has_rp2040_can = config.get("can_rx_gpio") and config.get("can_tx_gpio")
        if not has_stm32_can and not has_rp2040_can:
            raise ValueError(f"Profile does not define CAN pins for {communication}: {profile.id}")

    return config


def source_for_target(
    firmware_kind: str,
    klipper_path: str | None,
    kalico_path: str | None,
    katapult_path: str | None,
) -> dict[str, str]:
    if firmware_kind == "katapult":
        path = Path(katapult_path or "~/katapult").expanduser()
        return {"project": "katapult", "path": str(path)}

    sources = inspect_firmware_sources(klipper_path=klipper_path, kalico_path=kalico_path)
    for source in sources.get("sources", []):
        if source.get("available"):
            return {"project": str(source.get("project")), "path": str(source.get("path"))}
    raise ValueError("No Klipper/Kalico source repository is available.")


def config_for_target(firmware_kind: str, build: dict[str, Any]) -> str:
    if firmware_kind == "klipper":
        return generate_klipper_dot_config(build)
    return generate_katapult_dot_config(build)


def generate_katapult_dot_config(build: dict[str, Any]) -> str:
    klipper_like = generate_klipper_dot_config(build)
    lines = [
        "# Auto-generated by MCU Update Manager",
        "# Katapult firmware config.",
    ]
    lines.extend(line for line in klipper_like.splitlines() if line and not line.startswith("#"))
    app_offsets = {
        "8KiB": "CONFIG_STM32_APP_START_2000=y",
        "16KiB": "CONFIG_STM32_APP_START_4000=y",
        "32KiB": "CONFIG_STM32_APP_START_8000=y",
        "128KiB": "CONFIG_STM32_APP_START_20000=y",
    }
    app_offset = build.get("application_start_offset")
    if build.get("architecture") == "stm32" and app_offset in app_offsets:
        lines.append(app_offsets[app_offset])
    if build.get("architecture") == "stm32" and app_offset not in app_offsets:
        raise ValueError(f"Unsupported Katapult application offset: {app_offset}")
    if build.get("support_bootloader_entry") or build.get("support_double_click_reset"):
        lines.append("CONFIG_DOUBLE_RESET=y")
    if build.get("status_led") or build.get("status_led_pin"):
        lines.append("CONFIG_STATUS_LED=y")
    if build.get("status_led_pin"):
        lines.append(f"CONFIG_STATUS_LED_PIN=\"{build.get('status_led_pin')}\"")
    return "\n".join(lines) + "\n"


def artifact_path(source_path: str, firmware_kind: str) -> Path:
    root = Path(source_path).expanduser()
    if firmware_kind == "katapult":
        return root / "out" / "katapult.bin"
    return root / "out" / "klipper.bin"


def command_plan(
    source_path: str,
    config_path: Path,
    profile: HardwareProfile,
    firmware_kind: str,
    communication: str,
    artifact: Path,
) -> list[list[str]]:
    source = str(Path(source_path).expanduser())
    commands = [
        ["make", "-C", source, "clean", f"KCONFIG_CONFIG={config_path}"],
        ["make", "-C", source, "olddefconfig", f"KCONFIG_CONFIG={config_path}"],
        ["make", "-C", source, f"KCONFIG_CONFIG={config_path}"],
    ]

    initial = profile.initial_flash
    method = str(initial.get("method") or "")
    if method in {"dfu_util", "klipper_make_flash_dfu"} or initial.get("dfu_vid_pid"):
        commands.append(
            [
                "sudo", "dfu-util", "-R", "-a", "0",
                "-s", "0x08000000:mass-erase:force:leave",
                "-D", str(artifact),
                "-d", str(initial.get("dfu_vid_pid") or "0483:df11"),
            ]
        )
    elif method == "rp2040_bootsel_make_flash" or initial.get("boot_vid_pid"):
        commands.append(["make", "-C", source, "flash", f"FLASH_DEVICE={initial.get('boot_vid_pid', '2e8a:0003')}", f"KCONFIG_CONFIG={config_path}"])
    else:
        raise ValueError(f"Unsupported initial flash method for {profile.id}: {method}")

    return commands


def printable_commands(commands: list[list[str]]) -> list[str]:
    return [" ".join(command) for command in commands]


def dfu_step_descriptions(firmware_kind: str, communication: str) -> list[tuple[str, str]]:
    firmware_name = "Katapult" if firmware_kind == "katapult" else "Klipper/Kalico"
    steps = [
        ("clean", "Clean previous build"),
        ("config", f"Resolve {firmware_name} configuration"),
        ("compile", f"Compile {firmware_name} firmware"),
        ("flash_dfu", "Flash firmware through STM32 DFU"),
    ]
    if firmware_kind == "klipper" and communication == "usb":
        steps.append(("verify_usb", "Verify USB MCU returned"))
    return steps


def run_logged(command: list[str], log_path: Path) -> str:
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(command)}\n")
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        output = result.stdout + result.stderr
        log.write(output)

    if result.returncode != 0:
        if benign_stm32_leave_error(command, output):
            with log_path.open("a", encoding="utf-8") as log:
                log.write("\n# STM32 disconnected during DFU leave after a successful download; verifying USB re-enumeration.\n")
            return output
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}")
    return output


def benign_stm32_leave_error(command: list[str], output: str) -> bool:
    return (
        "flash" in command
        and any("FLASH_DEVICE=0483:df11" in part for part in command)
        and "File downloaded successfully" in output
        and "Submitting leave request" in output
        and "Error during download get_status" in output
    )


def usb_firmware_serials(build: dict[str, Any]) -> set[str]:
    serial_dir = Path("/dev/serial/by-id")
    if not serial_dir.exists():
        return set()
    processor = str(build.get("processor") or "").lower()
    return {
        str(path)
        for path in serial_dir.iterdir()
        if processor in path.name.lower() and ("usb-klipper_" in path.name.lower() or "usb-kalico_" in path.name.lower())
    }


def wait_for_new_usb_firmware_serial(
    build: dict[str, Any],
    existing: set[str],
    log_path: Path,
    timeout: int = 45,
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = usb_firmware_serials(build)
        new_matches = sorted(matches - existing)
        if new_matches:
            serial = new_matches[0]
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"Detected flashed USB MCU: {serial}\n")
            return serial
        time.sleep(1)
    raise RuntimeError("Firmware download completed, but the flashed USB MCU did not appear in /dev/serial/by-id.")


def reset_log(log_path: Path, result: dict[str, Any]) -> None:
    log_path.write_text("# MCU Update Manager DFU flash log\n" + json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
