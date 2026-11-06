from __future__ import annotations

from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import json
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import quote
from urllib.request import urlopen

from .flash_plan import create_flash_plan, load_manifest
from .flash_plan import file_sha256
from .io_utils import atomic_write_json


def flash_firmware(
    device_id: str,
    devices_path: str | Path,
    discovery_path: str | Path,
    profile_paths: list[str | Path],
    katapult_path: str | None,
    artifact: str | Path | None = None,
    artifact_manifest: str | Path | None = None,
    execute: bool = False,
    moonraker_url: str = "http://127.0.0.1:7125",
) -> dict[str, Any]:
    try:
        plan = create_flash_plan(
            device_id=device_id,
            devices_path=devices_path,
            discovery_path=discovery_path,
            profile_paths=profile_paths,
            katapult_path=katapult_path,
            artifact=artifact,
            artifact_manifest=artifact_manifest,
        )
    except Exception as exc:
        result = failed_before_plan(device_id, artifact_manifest, execute, exc)
        if execute:
            write_preflight_failure(result)
        return result

    result = {
        **plan,
        "action": "flash_firmware",
        "execute": execute,
        "flash": {
            "started_at": None,
            "completed_at": None,
            "status": "planned",
        },
    }

    flash_dir = flash_output_dir(artifact_manifest, device_id)
    flash_log = flash_dir / "flash.log"
    flash_result = flash_dir / "flash-result.json"
    result["flash"]["flash_log"] = str(flash_log)
    result["flash"]["metadata"] = str(flash_result)

    if not execute:
        return result

    result["status"] = "running"
    result["flash"]["started_at"] = now_iso()
    flash_dir.mkdir(parents=True, exist_ok=True)
    reset_log(flash_log, result)
    initialise_steps(result, plan.get("steps", []), flash_result)

    stopped_klipper = False
    try:
        validate_preflight(plan)
        device = plan["device"]
        uuid = str(device.get("canbus_uuid") or "")
        existing_katapult = set(Path("/dev/serial/by-id").glob("usb-katapult*"))

        run_step(
            result,
            flash_result,
            "stop_klipper",
            ["sudo", "-n", "systemctl", "stop", "klipper"],
            flash_log,
            timeout=60,
        )
        require_service_state("klipper", "inactive", flash_log)
        stopped_klipper = True

        flash_method = str(plan.get("profile", {}).get("flash_method") or "")
        if flash_method == "can_katapult":
            run_step(result, flash_result, "enter_katapult", command_words(plan, "enter_katapult"), flash_log, timeout=90)
            run_verify_application_step(result, flash_result, "verify_katapult",
                                        command_words(plan, "verify_katapult"), flash_log, uuid, "katapult")
        elif flash_method == "usb_katapult_or_make_flash":
            run_step(result, flash_result, "enter_katapult", command_words(plan, "enter_katapult"), flash_log, timeout=90)
            katapult_serial = run_wait_usb_katapult_step(result, flash_result, flash_log,
                                                         timeout=45, existing=existing_katapult)
            updated_flash_command = replace_usb_katapult_placeholder(plan, katapult_serial)
            update_step_command(plan, "flash_firmware", updated_flash_command)
            update_step_command(result, "flash_firmware", updated_flash_command)
            write_result(flash_result, result)

        needs_runtime_verification = False
        if flash_method in {"klipper_make_flash_usb", "usb_make_flash"}:
            flash_command = command_words(plan, "flash_firmware")
            output = run_step(result, flash_result, "flash_firmware", flash_command, flash_log,
                     timeout=240, cwd=Path(str(plan["firmware_source"]["path"])))
            needs_runtime_verification = is_flash_usb_leave_error(flash_command, output)
            run_step(result, flash_result, "verify_usb", command_words(plan, "verify_usb"), flash_log, timeout=5, retries=22)
        else:
            run_step(result, flash_result, "flash_firmware", command_words(plan, "flash_firmware"), flash_log, timeout=240)
            run_verify_application_step(result, flash_result, "verify_klipper",
                                        command_words(plan, "verify_klipper"), flash_log, uuid, "klipper")

        run_step(
            result,
            flash_result,
            "start_klipper",
            ["sudo", "-n", "systemctl", "start", "klipper"],
            flash_log,
            timeout=60,
        )
        require_service_state("klipper", "active", flash_log)
        stopped_klipper = False
        if needs_runtime_verification:
            result["flash"]["steps"].append({"id": "verify_runtime", "label": "Verify MCU firmware in Klipper", "status": "pending"})
            write_result(flash_result, result)
            verify_usb_runtime(result, flash_result, flash_log, moonraker_url, load_manifest(artifact_manifest))
            result["flash"]["warning"] = "DFU leave returned an error after download; USB and running firmware version were verified, but flash contents were not read back."
        result["status"] = "ok"
        result["flash"]["status"] = "ok"
        result["flash"]["completed_at"] = now_iso()
        if not needs_runtime_verification:
            try:
                mark_last_good_artifact(flash_dir, result)
            except (OSError, RuntimeError) as backup_error:
                result["flash"]["backup_warning"] = str(backup_error)
                append_log(flash_log, f"\n# Last-good backup failed: {backup_error}\n")
        finish_steps(result, "ok")
    except Exception as exc:
        result["flash"]["status"] = "failed"
        result["flash"]["error"] = str(exc)
        result["status"] = "failed"
        finish_steps(result, "failed")
        if stopped_klipper:
            try:
                run_logged(["sudo", "-n", "systemctl", "start", "klipper"], flash_log, timeout=60)
                require_service_state("klipper", "active", flash_log)
                result["flash"]["recovery"] = "klipper_start_attempted"
            except Exception as recovery_exc:
                result["flash"]["recovery"] = f"klipper_start_failed: {recovery_exc}"
    finally:
        result["flash"]["completed_at"] = now_iso()
        atomic_write_json(flash_result, result)

    return result


def failed_before_plan(device_id: str, artifact_manifest: str | Path | None, execute: bool, exc: Exception) -> dict[str, Any]:
    flash_dir = flash_output_dir(artifact_manifest, device_id)
    flash_log = flash_dir / "flash.log"
    flash_result = flash_dir / "flash-result.json"
    return {
        "action": "flash_firmware",
        "device": {"id": device_id},
        "execute": execute,
        "status": "failed",
        "flash": {
            "started_at": now_iso() if execute else None,
            "completed_at": now_iso() if execute else None,
            "status": "failed",
            "error": str(exc),
            "flash_log": str(flash_log),
            "metadata": str(flash_result),
            "steps": [],
        },
    }


def write_preflight_failure(result: dict[str, Any]) -> None:
    flash = result.get("flash", {})
    flash_log = Path(str(flash.get("flash_log"))).expanduser()
    flash_result = Path(str(flash.get("metadata"))).expanduser()
    flash_log.parent.mkdir(parents=True, exist_ok=True)
    reset_log(flash_log, result)
    append_log(flash_log, "\nPreflight failed before flash plan was created:\n")
    append_log(flash_log, str(flash.get("error") or "unknown error") + "\n")
    write_result(flash_result, result)


def validate_preflight(plan: dict[str, Any]) -> None:
    preflight = plan.get("preflight", {})
    katapult = preflight.get("katapult", {})
    artifact = preflight.get("artifact", {})
    manifest = preflight.get("manifest_checks", {})

    errors = []
    flash_method = str(plan.get("profile", {}).get("flash_method") or "")
    if flash_method not in {"klipper_make_flash_usb", "usb_make_flash"} and katapult.get("status") != "ok":
        errors.append(f"katapult={katapult.get('status')}")
    if artifact.get("status") != "ok":
        errors.append(f"artifact={artifact.get('status')}")
    if manifest.get("status") != "ok":
        errors.append(f"manifest={manifest.get('status')}")

    if errors:
        raise RuntimeError("Flash preflight failed: " + ", ".join(errors))


def command_words(plan: dict[str, Any], step_id: str) -> list[str]:
    for step in plan.get("steps", []):
        if step.get("id") == step_id:
            return split_command(str(step["command"]))
    raise ValueError(f"Flash step not found: {step_id}")


def replace_usb_katapult_placeholder(plan: dict[str, Any], katapult_serial: str) -> str:
    for step in plan.get("steps", []):
        if step.get("id") == "flash_firmware":
            return str(step.get("command", "")).replace("<usb-katapult-serial>", katapult_serial)
    raise ValueError("Flash step not found: flash_firmware")


def update_step_command(result: dict[str, Any], step_id: str, command: str) -> None:
    for step in result.get("steps", []):
        if step.get("id") == step_id:
            step["command"] = command
    for step in result.get("flash", {}).get("steps", []):
        if step.get("id") == step_id:
            step["command"] = command


def run_wait_usb_katapult_step(result: dict[str, Any], metadata_path: Path, log_path: Path, *, timeout: int,
                               existing: set[Path] | None = None) -> str:
    step_id = "wait_usb_katapult"
    mark_step(result, step_id, "running", attempt=1, attempts=1)
    write_result(metadata_path, result)
    try:
        serial = wait_for_usb_katapult(log_path, timeout=timeout, existing=existing)
    except Exception:
        mark_step(result, step_id, "failed", attempt=1, attempts=1)
        write_result(metadata_path, result)
        raise

    result.setdefault("flash", {})["usb_katapult_serial"] = serial
    mark_step(result, step_id, "done", attempt=1, attempts=1)
    write_result(metadata_path, result)
    return serial


def wait_for_usb_katapult(log_path: Path, *, timeout: int, existing: set[Path] | None = None) -> str:
    serial_dir = Path("/dev/serial/by-id")
    deadline = time.monotonic() + timeout
    append_log(log_path, "\n# Waiting for /dev/serial/by-id/usb-katapult* after CAN bootloader reset.\n")
    while time.monotonic() < deadline:
        matches = sorted(set(serial_dir.glob("usb-katapult*")) - (existing or set())) if serial_dir.exists() else []
        if len(matches) > 1:
            raise RuntimeError("Multiple new USB Katapult devices appeared; cannot identify the selected mainboard.")
        if matches:
            serial = str(matches[0])
            append_log(log_path, f"Found USB Katapult device: {serial}\n")
            return serial
        time.sleep(1)

    raise RuntimeError(
        "USB Katapult device did not appear after CAN bootloader reset. "
        "Check the mainboard USB cable, then use the board double-reset fallback and retry."
    )


def split_command(command: str) -> list[str]:
    return command.split()


def initialise_steps(result: dict[str, Any], planned_steps: list[dict[str, Any]], metadata_path: Path) -> None:
    result["flash"]["current_step"] = None
    result["flash"]["steps"] = [
        {
            "id": str(step.get("id")),
            "label": step.get("label"),
            "status": "pending",
        }
        for step in planned_steps
    ]
    write_result(metadata_path, result)


def run_step(
    result: dict[str, Any],
    metadata_path: Path,
    step_id: str,
    command: list[str],
    log_path: Path,
    *,
    timeout: int,
    retries: int = 0,
    allow_nonzero: bool = False,
    cwd: Path | None = None,
) -> str:
    for attempt in range(retries + 1):
        mark_step(result, step_id, "running", attempt=attempt + 1, attempts=retries + 1)
        write_result(metadata_path, result)
        try:
            output = run_logged(command, log_path, timeout=timeout, allow_nonzero=allow_nonzero, cwd=cwd)
        except Exception:
            if attempt < retries:
                append_log(log_path, f"\n# Step {step_id} failed on attempt {attempt + 1}; retrying once.\n")
                time.sleep(2)
                continue

            mark_step(result, step_id, "failed", attempt=attempt + 1, attempts=retries + 1)
            write_result(metadata_path, result)
            raise
        break

    mark_step(result, step_id, "done", attempt=attempt + 1, attempts=retries + 1)
    write_result(metadata_path, result)
    return output


def run_verify_application_step(result: dict[str, Any], metadata_path: Path, step_id: str,
                                command: list[str], log_path: Path, uuid: str, application: str,
                                timeout: int = 45) -> None:
    mark_step(result, step_id, "running")
    write_result(metadata_path, result)
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            output = run_logged(command, log_path, timeout=10, allow_nonzero=True)
            if "ModuleNotFoundError:" in output or "ImportError:" in output:
                mark_step(result, step_id, "failed")
                write_result(metadata_path, result)
                raise ValueError(f"CAN verification could not start due to a Python dependency error: {output.strip()}")
            require_query_application(output, uuid, application)
            mark_step(result, step_id, "done")
            write_result(metadata_path, result)
            return
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            time.sleep(2)
    mark_step(result, step_id, "failed")
    write_result(metadata_path, result)
    raise RuntimeError(f"Could not verify UUID {uuid} as {application} within {timeout}s: {last_error}")


def verify_usb_runtime(result: dict[str, Any], metadata_path: Path, log_path: Path,
                       moonraker_url: str, manifest: dict[str, Any] | None, timeout: int = 45) -> None:
    device = result.get("device") or {}
    section = device.get("mcu_section")
    expected_version = ((manifest or {}).get("firmware_source") or {}).get("current_version")
    expected_chip = device.get("detected_chip")
    if not section or not expected_version or not expected_chip:
        raise RuntimeError("Cannot verify USB firmware: MCU section, expected version or chip is missing.")
    mark_step(result, "verify_runtime", "running")
    write_result(metadata_path, result)
    url = moonraker_url.rstrip("/") + "/printer/objects/query?" + quote(str(section), safe="")
    deadline = time.monotonic() + timeout
    detail = "MCU not yet connected"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=3) as response:
                state = json.load(response).get("result", {}).get("status", {}).get(section, {})
            version = state.get("mcu_version")
            chip = (state.get("mcu_constants") or {}).get("MCU")
            if version == expected_version and chip == expected_chip:
                append_log(log_path, f"\n# Verified {section}: version {version}, MCU {chip}. Flash readback was not performed.\n")
                mark_step(result, "verify_runtime", "done")
                write_result(metadata_path, result)
                return
            detail = f"reported version={version!r}, MCU={chip!r}"
        except Exception as exc:
            detail = str(exc)
        time.sleep(2)
    mark_step(result, "verify_runtime", "failed")
    write_result(metadata_path, result)
    raise RuntimeError(f"DFU download finished but running MCU could not be verified: {detail}")


def mark_step(result: dict[str, Any], step_id: str, status: str, *, attempt: int | None = None, attempts: int | None = None) -> None:
    result["flash"]["current_step"] = step_id if status == "running" else result["flash"].get("current_step")
    for step in result["flash"].get("steps", []):
        if step.get("id") == step_id:
            step["status"] = status
            if attempt is not None:
                step["attempt"] = attempt
            if attempts is not None:
                step["attempts"] = attempts
            if status == "running":
                step["started_at"] = now_iso()
            if status in {"done", "failed"}:
                step["completed_at"] = now_iso()
            break


def finish_steps(result: dict[str, Any], status: str) -> None:
    if status == "ok":
        for step in result["flash"].get("steps", []):
            if step.get("status") == "pending":
                step["status"] = "done"
    result["flash"]["current_step"] = None


def require_query_application(output: str, uuid: str, application: str) -> None:
    accepted_applications = {application.lower()}
    if application.lower() == "klipper":
        accepted_applications.add("kalico")
    for line in output.splitlines():
        found_uuid = re.search(r"(?:canbus_uuid=|UUID:\s*)([0-9a-f]{12})", line, re.IGNORECASE)
        found_app = re.search(r"Application:\s*([A-Za-z]+)", line, re.IGNORECASE)
        if found_uuid and found_app and found_uuid.group(1).lower() == uuid.lower() and found_app.group(1).lower() in accepted_applications:
            return
    raise RuntimeError(f"Expected UUID {uuid} to report application {application}; query output was: {output.strip()}")


def require_service_state(service: str, expected: str, log_path: Path) -> None:
    command = ["systemctl", "is-active", service]
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(command)}\n")
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        output = (result.stdout or result.stderr or "").strip()
        log.write((output or f"exit={result.returncode}") + "\n")

    if output != expected:
        raise RuntimeError(f"Expected {service} service to be {expected}; systemctl reported {output or result.returncode}")


def run_logged(command: list[str], log_path: Path, *, timeout: int, allow_nonzero: bool = False, cwd: Path | None = None) -> str:
    output_lines: list[str] = []
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(command)}\n")
        log.flush()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=cwd,
        )

        def pump_output() -> None:
            if process.stdout is None:
                return
            for line in process.stdout:
                output_lines.append(line)
                log.write(line)
                log.flush()

        reader = threading.Thread(target=pump_output, daemon=True)
        reader.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            reader.join(timeout=2)
            raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(command)}") from exc
        reader.join(timeout=2)

    output = "".join(output_lines)
    if returncode != 0 and not allow_nonzero:
        if benign_stm32_leave_error(command, output):
            append_log(
                log_path,
                "# STM32 disconnected during DFU leave after a successful download; verifying USB re-enumeration.\n",
            )
            return output
        hint = extract_failure_hint(output)
        detail = f": {hint}" if hint else ""
        raise RuntimeError(f"Command failed ({returncode}): {' '.join(command)}{detail}")
    if returncode != 0 and allow_nonzero:
        append_log(log_path, f"# Command returned {returncode}; continuing because output will be validated.\n")
    return output


def benign_stm32_leave_error(command: list[str], output: str) -> bool:
    make_flash = "flash" in command and any("FLASH_DEVICE=" in part for part in command)
    return (make_flash or is_flash_usb_leave_error(command, output)) and has_dfu_leave_error(output)


def has_dfu_leave_error(output: str) -> bool:
    download = output.find("File downloaded successfully")
    leave = output.find("Submitting leave request", download + 1)
    error = output.find("dfu-util: Error during download get_status", leave + 1)
    return download >= 0 and leave > download and error > leave


def is_flash_usb_leave_error(command: list[str], output: str) -> bool:
    return (len(command) > 2 and Path(command[1]).name == "flash_usb.py"
            and "-t" in command and any(part.startswith("stm32") for part in command)
            and "-d" in command and "-s" in command and has_dfu_leave_error(output))


def write_result(path: Path, result: dict[str, Any]) -> None:
    atomic_write_json(path, result)


def append_log(path: Path, message: str) -> None:
    with path.open("a", encoding="utf-8") as log:
        log.write(message)


def extract_failure_hint(output: str) -> str:
    interesting = []
    for line in output.splitlines():
        if "FlashError" in line or "Flash write failed" in line or "Error sending command" in line:
            interesting.append(line.strip())
    return " | ".join(interesting[-3:])


def decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def reset_log(log_path: Path, result: dict[str, Any]) -> None:
    header = {
        "action": result.get("action"),
        "device": result.get("device"),
        "profile": result.get("profile"),
        "artifact": result.get("preflight", {}).get("artifact"),
        "started_at": result.get("flash", {}).get("started_at"),
    }
    log_path.write_text("# MCU Update Manager flash log\n" + json.dumps(header, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def mark_last_good_artifact(flash_dir: Path, result: dict[str, Any]) -> None:
    artifact = result.get("preflight", {}).get("artifact") or {}
    manifest = result.get("preflight", {}).get("manifest_checks") or {}
    device_id = result.get("device", {}).get("id")
    if not device_id:
        return

    source = Path(str(artifact.get("path") or "")).expanduser()
    digest = str(artifact.get("sha256") or "")
    if not source.is_file() or not digest:
        return
    backup_dir = flash_dir.parent / "last-good"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{digest}.bin"
    if not backup.exists():
        shutil.copy2(source, backup)
    if file_sha256(backup) != digest:
        raise RuntimeError(f"Last-good firmware backup is corrupt: {backup}")
    original_manifest = Path(str(manifest.get("manifest") or "")).expanduser()
    backup_manifest = None
    if original_manifest.is_file():
        manifest_data = json.loads(original_manifest.read_text(encoding="utf-8"))
        manifest_data["artifact"] = {**manifest_data.get("artifact", {}), "path": str(backup), "sha256": digest}
        backup_manifest = backup_dir / "artifact.json"
        atomic_write_json(backup_manifest, manifest_data)

    record = {
        "schema_version": 1,
        "project": "MCU Update Manager",
        "device": result.get("device"),
        "profile": result.get("profile"),
        "artifact": {
            "path": str(backup),
            "size": artifact.get("size"),
            "sha256": artifact.get("sha256"),
        },
        "manifest": str(backup_manifest) if backup_manifest else manifest.get("manifest"),
        "flashed_at": result.get("flash", {}).get("completed_at") or now_iso(),
        "flash_log": result.get("flash", {}).get("flash_log"),
    }
    atomic_write_json(flash_dir.parent / "last-good-artifact.json", record)


def flash_output_dir(artifact_manifest: str | Path | None, device_id: str) -> Path:
    if artifact_manifest:
        manifest_dir = Path(artifact_manifest).expanduser().resolve().parent
        if manifest_dir.parent.name == "history":
            return manifest_dir.parent.parent / f"restore-{manifest_dir.name[:16]}"
        if manifest_dir.name == "last-good":
            return manifest_dir.parent / "restore-last-good"
        return manifest_dir
    return (Path("builds").expanduser() / device_id / "current").resolve()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
