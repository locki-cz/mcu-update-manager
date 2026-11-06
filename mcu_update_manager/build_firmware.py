from __future__ import annotations

from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import hashlib
import json
import shutil
import subprocess
from uuid import uuid4

from .prepare_build import prepare_build_workspace, sanitize_path_part
from .prepare_build import stm32_can_pin_symbol
from .io_utils import atomic_write_json


def build_firmware(
    device_id: str,
    firmware_ref: str,
    devices_path: str | Path,
    discovery_path: str | Path,
    profile_paths: list[str | Path],
    klipper_path: str | None,
    kalico_path: str | None,
    build_root: str | Path = "builds",
    artifact_root: str | Path = "artifacts",
    can_bitrate: str = "1000000",
    execute: bool = False,
) -> dict[str, Any]:
    try:
        prepared = prepare_build_workspace(
            device_id=device_id,
            firmware_ref=firmware_ref,
            devices_path=devices_path,
            discovery_path=discovery_path,
            profile_paths=profile_paths,
            klipper_path=klipper_path,
            kalico_path=kalico_path,
            build_root=build_root,
            artifact_root=artifact_root,
            can_bitrate=can_bitrate,
        )
    except Exception as exc:
        build_dir = Path(build_root).expanduser() / device_id / sanitize_path_part(firmware_ref)
        build_dir.mkdir(parents=True, exist_ok=True)
        build_log = build_dir / "build.log"
        metadata_path = build_dir / "build-result.json"
        result = {
            "action": "build_firmware",
            "device": {"id": device_id},
            "execute": execute,
            "firmware_ref": firmware_ref,
            "build": {
                "build_log": str(build_log),
                "completed_at": now_iso(),
                "error": str(exc),
                "metadata": str(metadata_path),
                "started_at": None,
                "status": "failed",
            },
        }
        build_log.write_text(
            "# MCU Update Manager build log\n"
            + json.dumps({"device": device_id, "firmware_ref": firmware_ref}, indent=2, sort_keys=True)
            + f"\n\nPrepare build failed: {exc}\n",
            encoding="utf-8",
        )
        atomic_write_json(metadata_path, result)
        return result

    repo_path = Path(prepared["firmware_source"]["path"]).expanduser().resolve()
    workspace = prepared["workspace"]
    build_dir = Path(workspace["build_dir"]).expanduser().resolve()
    generated_config = Path(workspace["config"]).expanduser().resolve()
    build_log = build_dir / "build.log"
    resolved_config = build_dir / ".config.resolved"
    artifact_manifest = build_dir / "artifact.json"
    artifact = Path(prepared["outputs"]["artifact"]).expanduser().resolve()

    result = {
        **prepared,
        "action": "build_firmware",
        "execute": execute,
        "build": {
            "kconfig_config": str(generated_config),
            "build_log": str(build_log),
            "resolved_config": str(resolved_config),
            "artifact": str(artifact),
            "artifact_manifest": str(artifact_manifest),
            "started_at": None,
            "completed_at": None,
            "status": "planned",
        },
    }

    if not execute:
        result["build"]["commands"] = build_command_list(repo_path, firmware_ref, generated_config, artifact)
        return result

    result["build"]["started_at"] = now_iso()
    build_log.parent.mkdir(parents=True, exist_ok=True)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    remove_stale_outputs(artifact_manifest, resolved_config)
    reset_log(build_log, result)
    metadata_path = build_dir / "build-result.json"
    initialise_steps(result, metadata_path)

    build_repo_path = repo_path
    temporary_worktree: Path | None = None
    try:
        if firmware_ref != "current":
            temporary_worktree = build_dir / f"source-{uuid4().hex}"
            run_build_step(result, metadata_path, "checkout",
                           lambda: prepare_selected_source(repo_path, firmware_ref, temporary_worktree, build_log))
            build_repo_path = temporary_worktree
        else:
            run_build_step(result, metadata_path, "checkout", lambda: checkout_ref(repo_path, firmware_ref, build_log))
        run_build_step(
            result,
            metadata_path,
            "clean",
            lambda: run_logged(["make", "clean", f"KCONFIG_CONFIG={generated_config}"], build_repo_path, build_log),
        )
        run_build_step(
            result,
            metadata_path,
            "config",
            lambda: run_logged(["make", "olddefconfig", f"KCONFIG_CONFIG={generated_config}"], build_repo_path, build_log),
        )
        shutil.copy2(generated_config, resolved_config)
        validate_resolved_config(resolved_config, prepared["profile"]["build"])
        run_build_step(
            result,
            metadata_path,
            "compile",
            lambda: run_logged(["make", f"KCONFIG_CONFIG={generated_config}"], build_repo_path, build_log),
        )

        output_bin = run_build_step(result, metadata_path, "artifact", lambda: resolve_firmware_output(build_repo_path, prepared))

        digest = file_sha256(output_bin)
        artifact = artifact.with_name(f"{artifact.stem}-{digest[:16]}{artifact.suffix}")
        if not artifact.exists():
            shutil.copy2(output_bin, artifact)
        result["build"]["artifact"] = str(artifact)
        result["build"]["status"] = "ok"
        result["build"]["artifact_size"] = artifact.stat().st_size
        result["build"]["artifact_sha256"] = digest
        result["build"]["source_head"] = git_output(build_repo_path, ["rev-parse", "HEAD"])
        result["firmware_source"]["current_version"] = git_output(build_repo_path, ["describe", "--tags", "--always"])
        finish_steps(result, "ok")
    except Exception as exc:
        result["build"]["status"] = "failed"
        result["build"]["error"] = str(exc)
        finish_steps(result, "failed")
    finally:
        if temporary_worktree and temporary_worktree.exists():
            cleanup = subprocess.run(
                ["git", "worktree", "remove", "--force", str(temporary_worktree)],
                cwd=repo_path, capture_output=True, text=True,
            )
            if cleanup.returncode != 0:
                result["build"]["cleanup_warning"] = cleanup.stderr.strip() or "Could not remove temporary source worktree."
        result["build"]["completed_at"] = now_iso()
        if result["build"]["status"] == "ok":
            manifest = create_artifact_manifest(result)
            atomic_write_json(artifact_manifest, manifest)
            history_manifest = build_dir.parent / "history" / result["build"]["artifact_sha256"] / "artifact.json"
            history_manifest.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(history_manifest, manifest)
        result["build"]["metadata"] = str(metadata_path)
        atomic_write_json(metadata_path, result)

    return result


def build_command_list(repo_path: Path, firmware_ref: str, generated_config: Path, artifact: Path) -> list[str]:
    commands = []
    if firmware_ref == "current":
        commands.append(f"git -C {repo_path} status --short")
    elif firmware_ref == "latest":
        commands.append(f"git -C {repo_path} fetch --tags --prune")
        commands.append(f"git -C {repo_path} worktree add --detach <temporary-source> @{{upstream}}")
    else:
        commands.append(f"git -C {repo_path} worktree add --detach <temporary-source> {firmware_ref}")

    commands.extend(
        [
            f"make -C {repo_path} clean KCONFIG_CONFIG={generated_config}",
            f"make -C {repo_path} olddefconfig KCONFIG_CONFIG={generated_config}",
            f"make -C {repo_path} KCONFIG_CONFIG={generated_config}",
            f"cp {repo_path / 'out' / 'klipper.bin'} {artifact}",
        ]
    )
    return commands


def resolve_firmware_output(repo_path: Path, prepared: dict[str, Any]) -> Path:
    expected = repo_path / "out" / "klipper.bin"
    if expected.exists():
        return expected

    out_dir = repo_path / "out"
    candidates = [
        out_dir / "klipper.uf2",
        out_dir / "klipper.elf.hex",
        out_dir / "klipper.hex",
    ]
    existing = [path for path in candidates if path.exists()]
    produced = ", ".join(str(path) for path in existing) if existing else "no known firmware outputs"
    profile = prepared.get("profile", {})
    flash = profile.get("flash", {})
    build = profile.get("build", {})
    if flash.get("method") == "can_katapult":
        raise RuntimeError(
            "Expected firmware output was not created: "
            f"{expected}. Produced {produced}. CAN Katapult flashing requires klipper.bin; "
            f"profile {profile.get('id')} build architecture={build.get('architecture')} processor={build.get('processor')}."
        )
    if existing:
        return existing[0]

    raise RuntimeError(f"Expected firmware output was not created: {expected}. Produced {produced}.")


def checkout_ref(repo_path: Path, firmware_ref: str, log_path: Path) -> None:
    if firmware_ref == "current":
        run_logged(["git", "status", "--short"], repo_path, log_path)
    elif firmware_ref == "latest":
        run_logged(["git", "checkout", "@{upstream}"], repo_path, log_path)
    else:
        run_logged(["git", "checkout", firmware_ref], repo_path, log_path)


def prepare_selected_source(repo_path: Path, firmware_ref: str, worktree: Path, log_path: Path) -> None:
    if firmware_ref == "current":
        raise ValueError("A temporary worktree is only needed for a selected source version.")
    if firmware_ref == "latest":
        run_logged(["git", "fetch", "--tags", "--prune"], repo_path, log_path)
    run_logged(["git", "worktree", "add", "--detach", str(worktree),
                "@{upstream}" if firmware_ref == "latest" else firmware_ref], repo_path, log_path)


def run_logged(command: list[str], cwd: Path, log_path: Path) -> None:
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(command)}\n")
        result = subprocess.run(command, cwd=str(cwd), check=False, stdout=log, stderr=subprocess.STDOUT, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}")


def initialise_steps(result: dict[str, Any], metadata_path: Path) -> None:
    result["build"]["current_step"] = None
    result["build"]["steps"] = [
        {"id": "checkout", "label": "Check firmware source", "status": "pending"},
        {"id": "clean", "label": "Clean previous build", "status": "pending"},
        {"id": "config", "label": "Resolve Klipper config", "status": "pending"},
        {"id": "compile", "label": "Compile firmware", "status": "pending"},
        {"id": "artifact", "label": "Copy firmware artifact", "status": "pending"},
    ]
    atomic_write_json(metadata_path, result)


def run_build_step(result: dict[str, Any], metadata_path: Path, step_id: str, callback: Any) -> Any:
    mark_step(result, step_id, "running")
    atomic_write_json(metadata_path, result)
    try:
        value = callback()
    except Exception:
        mark_step(result, step_id, "failed")
        atomic_write_json(metadata_path, result)
        raise

    mark_step(result, step_id, "done")
    atomic_write_json(metadata_path, result)
    return value


def mark_step(result: dict[str, Any], step_id: str, status: str) -> None:
    result["build"]["current_step"] = step_id if status == "running" else result["build"].get("current_step")
    for step in result["build"].get("steps", []):
        if step.get("id") == step_id:
            step["status"] = status
            if status == "running":
                step["started_at"] = now_iso()
            if status in {"done", "failed"}:
                step["completed_at"] = now_iso()
            break


def finish_steps(result: dict[str, Any], status: str) -> None:
    if status == "ok":
        for step in result["build"].get("steps", []):
            if step.get("status") == "pending":
                step["status"] = "done"
    result["build"]["current_step"] = None


def reset_log(log_path: Path, result: dict[str, Any]) -> None:
    header = {
        "action": result.get("action"),
        "device": result.get("device"),
        "firmware_source": result.get("firmware_source"),
        "profile": result.get("profile", {}).get("id"),
        "started_at": result.get("build", {}).get("started_at"),
    }
    log_path.write_text("# MCU Update Manager build log\n" + json.dumps(header, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def remove_stale_outputs(*paths: Path) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_output(repo_path: Path, args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo_path),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def create_artifact_manifest(result: dict[str, Any]) -> dict[str, Any]:
    build = result.get("build", {})
    device = result.get("device", {})
    profile = result.get("profile", {})
    firmware_source = result.get("firmware_source", {})
    return {
        "schema_version": 1,
        "project": "MCU Update Manager",
        "device": {
            "id": device.get("id"),
            "mcu_section": device.get("mcu_section"),
            "transport": device.get("transport"),
            "can_interface": device.get("can_interface"),
            "canbus_uuid": device.get("canbus_uuid"),
            "serial": device.get("serial"),
            "detected_chip": device.get("detected_chip"),
        },
        "profile": {
            "id": profile.get("id"),
            "name": profile.get("name"),
            "digest": profile.get("digest"),
        },
        "firmware_source": {
            "project": firmware_source.get("project"),
            "path": firmware_source.get("path"),
            "selected_ref": firmware_source.get("selected_ref"),
            "current_version": firmware_source.get("current_version"),
            "source_head": build.get("source_head"),
        },
        "artifact": {
            "path": build.get("artifact"),
            "size": build.get("artifact_size"),
            "sha256": build.get("artifact_sha256"),
        },
        "build": {
            "started_at": build.get("started_at"),
            "completed_at": build.get("completed_at"),
            "build_config_name": result.get("workspace", {}).get("build_config_name"),
            "generated_config": result.get("workspace", {}).get("config"),
            "generated_config_sha256": file_sha256_if_exists(result.get("workspace", {}).get("config")),
            "resolved_config": build.get("resolved_config"),
            "resolved_config_sha256": file_sha256_if_exists(build.get("resolved_config")),
            "build_log": build.get("build_log"),
        },
    }


def file_sha256_if_exists(path: Any) -> str | None:
    if not path:
        return None
    config_path = Path(str(path)).expanduser()
    if not config_path.exists():
        return None
    return file_sha256(config_path)


def validate_resolved_config(config_path: Path, build: dict[str, Any]) -> None:
    config_text = config_path.read_text(encoding="utf-8")
    expected = []
    expected_any: list[tuple[str, list[str]]] = []

    processor_symbols = {
        "STM32F072": "CONFIG_MACH_STM32F072=y",
        "STM32F103": "CONFIG_MACH_STM32F103=y",
        "STM32F405": "CONFIG_MACH_STM32F405=y",
        "STM32F407": "CONFIG_MACH_STM32F407=y",
        "STM32F429": "CONFIG_MACH_STM32F429=y",
        "STM32F446": "CONFIG_MACH_STM32F446=y",
        "STM32G0B1": "CONFIG_MACH_STM32G0B1=y",
        "STM32H723": "CONFIG_MACH_STM32H723=y",
        "STM32H743": "CONFIG_MACH_STM32H743=y",
        "RP2040": "CONFIG_MACH_RP2040=y",
    }
    processor = build.get("processor")
    if processor in processor_symbols:
        expected.append(processor_symbols[processor])

    offset_symbols = {
        "8KiB": "CONFIG_STM32_FLASH_START_2000=y",
        "16KiB": "CONFIG_STM32_FLASH_START_4000=y",
        "32KiB": "CONFIG_STM32_FLASH_START_8000=y",
        "64KiB": "CONFIG_STM32_FLASH_START_10000=y",
        "128KiB": "CONFIG_STM32_FLASH_START_20000=y",
        "No bootloader": "CONFIG_STM32_FLASH_START_0000=y",
    }
    bootloader_offset = build.get("bootloader_offset")
    if build.get("processor") == "RP2040" and bootloader_offset == "16KiB":
        expected.append("CONFIG_RPXXXX_FLASH_START_4000=y")
    elif build.get("processor") == "RP2040" and bootloader_offset == "No bootloader":
        expected.append("CONFIG_RPXXXX_FLASH_START_0100=y")
    elif bootloader_offset in offset_symbols:
        expected.append(offset_symbols[bootloader_offset])

    if build.get("clock_reference") == "8MHz crystal":
        expected_any.append(("8MHz clock reference", ["CONFIG_CLOCK_REF_FREQ=8000000", "CONFIG_STM32_CLOCK_REF_8M=y"]))
    if build.get("clock_reference") == "12MHz crystal":
        expected_any.append(("12MHz clock reference", ["CONFIG_CLOCK_REF_FREQ=12000000", "CONFIG_STM32_CLOCK_REF_12M=y"]))
    if build.get("clock_reference") == "25MHz crystal":
        expected_any.append(("25MHz clock reference", ["CONFIG_CLOCK_REF_FREQ=25000000", "CONFIG_STM32_CLOCK_REF_25M=y"]))

    if build.get("communication") in {"canbus", "usb_to_canbus_bridge"} and build.get("processor") != "RP2040":
        pin_symbol = stm32_can_pin_symbol(
            build.get("can_rx_pin"),
            build.get("can_tx_pin"),
            usb_to_canbus_bridge=build.get("communication") == "usb_to_canbus_bridge",
        )
        expected.extend(
            [
                "CONFIG_CANBUS=y",
                "CONFIG_CAN_UUID_USE_CHIPID=y",
                "CONFIG_CANBUS_FREQUENCY=" + str(build.get("can_bitrate", "1000000")),
            ]
        )
        if build.get("communication") == "canbus":
            expected.append(pin_symbol + "=y")
            expected.append("CONFIG_CANSERIAL=y")
            forbidden = ["CONFIG_USB=y", "CONFIG_USBSERIAL=y", "CONFIG_USBCANBUS=y"]
        else:
            expected.extend(
                [
                    "CONFIG_STM32_USBCANBUS_PA11_PA12=y",
                    pin_symbol + "=y",
                    "CONFIG_USBCANBUS=y",
                ]
            )
            forbidden = []
    elif build.get("communication") in {"canbus", "usb_to_canbus_bridge"} and build.get("processor") == "RP2040":
        from .prepare_build import rp2040_can_lines
        expected.extend(rp2040_can_lines(build))
        expected.extend(
            [
                "CONFIG_CANBUS=y",
                "CONFIG_CANBUS_FREQUENCY=" + str(build.get("can_bitrate", "1000000")),
            ]
        )
        if build.get("communication") == "canbus":
            expected.extend(["CONFIG_RPXXXX_CANBUS=y", "CONFIG_CANSERIAL=y"])
            forbidden = ["CONFIG_USBCANBUS=y", "CONFIG_USB=y"]
        else:
            expected.extend(["CONFIG_RPXXXX_USBCANBUS=y", "CONFIG_USBCANBUS=y", "CONFIG_USB=y"])
            forbidden = ["CONFIG_USBSERIAL=y"]
    elif build.get("communication") == "usb" and build.get("processor") == "RP2040":
        expected.extend(["CONFIG_RPXXXX_USB=y", "CONFIG_USBSERIAL=y", "CONFIG_USB=y"])
        forbidden = ["CONFIG_USBCANBUS=y", "CONFIG_CANSERIAL=y"]
    else:
        forbidden = []

    missing = [line for line in expected if line not in config_text]
    for label, options in expected_any:
        if not any(option in config_text for option in options):
            missing.append(f"{label} ({' or '.join(options)})")
    present_forbidden = [line for line in forbidden if line in config_text]
    if missing or present_forbidden:
        message_parts = []
        if missing:
            message_parts.append("missing expected config: " + ", ".join(missing))
        if present_forbidden:
            message_parts.append("unexpected config present: " + ", ".join(present_forbidden))
        raise RuntimeError(f"Resolved Klipper config does not match selected profile ({'; '.join(message_parts)})")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
