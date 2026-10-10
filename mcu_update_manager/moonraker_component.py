from __future__ import annotations

from pathlib import Path
from typing import Any
import asyncio
import json
import re
import sys
from contextlib import suppress
from urllib.parse import quote
from urllib.request import urlopen

from .io_utils import atomic_write_json
from .custom_profiles import get_profile, is_custom_profile, profile_fields, save_custom_profile
from .hardware_options import processor_options
from .operation_cache import finish_operation, is_active, mark_interrupted, new_operation, read_operation, write_operation
from .profiles import load_profiles
from .status import latest_operation_for_device


def load_component(config: Any) -> "MCUUpdateManagerComponent":
    return MCUUpdateManagerComponent(config)


class MCUUpdateManagerComponent:
    def __init__(self, config: Any) -> None:
        self.server = config.get_server()
        self.enabled = config.getboolean("enabled", True)
        self.repo_path = Path(config.get("repo_path", "~/mcu-update-manager")).expanduser()
        self.printer_cfg = config.get("printer_cfg", "~/printer_data/config/printer.cfg")
        self.serial_dir = config.get("serial_dir", "/dev/serial/by-id")
        self.devices_path = config.get("devices_path", "devices.yaml")
        self.can_interface = config.get("can_interface", "can0")
        self.can_bitrate = config.get("can_bitrate", "1000000")
        self.katapult_path = config.get("katapult_path", "~/katapult")
        self.klipper_path = config.get("klipper_path", "~/klipper")
        self.kalico_path = config.get("kalico_path", "~/kalico")
        self.cartographer_firmware_path = config.get("cartographer_firmware_path", "~/cartographer_firmware")
        self.profile_dirs = config.get("profile_dirs", "profiles")
        self.custom_profile_dir = Path(config.get("custom_profile_dir", "~/printer_data/config/mcu_update_manager/profiles")).expanduser()
        self.build_root = config.get("build_root", "builds")
        self.artifact_root = config.get("artifact_root", "artifacts")
        self.cache_path = self.repo_path / config.get("cache_path", "cache/operation.json")
        self.moonraker_url = config.get("moonraker_url", "http://127.0.0.1:7125")
        self.operation_lock = asyncio.Lock()
        self.active_task: asyncio.Task[None] | None = None
        mark_interrupted(self.cache_path)

        if not self.enabled:
            return

        self.server.register_endpoint(
            "/machine/mcu_update_manager/status",
            ["GET"],
            self._handle_status,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/operation",
            ["GET"],
            self._handle_operation,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/log",
            ["GET"],
            self._handle_log,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/confirm",
            ["POST"],
            self._handle_confirm,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/profile",
            ["GET"],
            self._handle_get_profile,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/profile/options",
            ["GET"],
            self._handle_profile_options,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/profile/save",
            ["POST"],
            self._handle_save_profile,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/switch_firmware_ref",
            ["POST"],
            self._handle_switch_firmware_ref,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/build",
            ["POST"],
            self._handle_build,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/flash",
            ["POST"],
            self._handle_flash,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/cartographer_flash",
            ["POST"],
            self._handle_cartographer_flash,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/cartographer_usb_to_can",
            ["POST"],
            self._handle_cartographer_usb_to_can,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/cartographer_dfu_flash",
            ["POST"],
            self._handle_cartographer_dfu_flash,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/dfu_flash",
            ["POST"],
            self._handle_dfu_flash,
        )
        self.server.register_endpoint(
            "/machine/mcu_update_manager/verify",
            ["POST"],
            self._handle_verify,
        )

    async def _handle_status(self, web_request: Any) -> dict[str, Any]:
        refresh = web_request.get_str("refresh_repositories", "false").lower() == "true"
        command = self._status_command()
        if refresh:
            command.append("--refresh-repositories")
        return await self._run_json(command)

    async def _handle_operation(self, _web_request: Any) -> dict[str, Any]:
        cached = read_operation(self.cache_path)
        if not cached:
            return {"status": "idle", "job": None, "operation": None}

        device_id = cached.get("device_id")
        operation = None
        if device_id:
            operation = latest_operation_for_device(
                device_id=str(device_id),
                build_root=str(self.repo_path / self.build_root),
            )
        return {"status": cached.get("status", "unknown"), "job": cached, "operation": operation}

    async def _handle_log(self, web_request: Any) -> dict[str, Any]:
        device_id = web_request.get_str("device_id")
        operation = latest_operation_for_device(
            device_id=device_id,
            build_root=str(self.repo_path / self.build_root),
        )
        if not operation or not operation.get("log"):
            raise self.server.error(f"No operation log is available for device: {device_id}")

        log_path = Path(str(operation["log"])).expanduser().resolve()
        build_root = (self.repo_path / self.build_root).resolve()
        if not log_path.is_relative_to(build_root):
            raise self.server.error("Operation log is outside the configured build directory.")
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise self.server.error(f"Could not read operation log: {exc}") from exc
        return {
            "device_id": device_id,
            "filename": log_path.name,
            "content": content[-2_000_000:],
            "truncated": len(content) > 2_000_000,
        }

    async def _handle_confirm(self, web_request: Any) -> dict[str, Any]:
        device_id = web_request.get_str("device_id")
        profile_id = web_request.get_str("profile_id")
        profile = get_profile(load_profiles(self._profile_paths()), profile_id)
        discovery_path = self.repo_path / "discovery.json"
        discovery = await self._run_json(self._discover_command())
        selected = next((item for item in discovery.get("devices", []) if item.get("id") == device_id), None)
        if selected is None:
            raise self.server.error(f"Device not found in discovery: {device_id}")
        chip = str(selected.get("detected_chip") or "").lower()
        transport = str(selected.get("transport") or "").lower()
        chips = [str(item).lower() for item in profile.match.get("chips", [])]
        transports = [str(item).lower() for item in profile.match.get("transports", [])]
        if chip and chips and chip not in chips:
            raise self.server.error(f"Profile {profile_id} is for {', '.join(chips)}, not {chip}.")
        if transport != "dfu" and transport and transports and transport not in transports:
            raise self.server.error(f"Profile {profile_id} does not support {transport}.")
        atomic_write_json(discovery_path, discovery)

        result = await self._run_json(
            [
                *self._base_command(),
                "confirm-device",
                "--devices-path",
                self.devices_path,
                "--discovery-json",
                str(discovery_path),
                "--device",
                device_id,
                "--profile",
                profile_id,
            ]
        )
        return {"status": "ok", "result": result}

    async def _handle_get_profile(self, web_request: Any) -> dict[str, Any]:
        profile = get_profile(load_profiles(self._profile_paths()), web_request.get_str("profile_id"))
        return {
            "id": profile.id,
            "custom": is_custom_profile(profile, self.custom_profile_dir),
            "fields": profile_fields(profile),
        }

    async def _handle_profile_options(self, web_request: Any) -> dict[str, Any]:
        return {"processors": processor_options()}

    async def _handle_save_profile(self, web_request: Any) -> dict[str, Any]:
        if (self.active_task and not self.active_task.done()) or is_active(read_operation(self.cache_path)):
            raise self.server.error("A firmware operation is running. Save the profile after it finishes.")
        try:
            fields = json.loads(web_request.get_str("fields_json"))
            result = save_custom_profile(
                self._profile_paths(),
                self.custom_profile_dir,
                fields,
                template_id=web_request.get_str("template_id", "") or None,
                profile_id=web_request.get_str("profile_id", "") or None,
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            raise self.server.error(str(exc)) from exc
        return {"status": "ok", "profile": result}

    async def _handle_switch_firmware_ref(self, web_request: Any) -> dict[str, Any]:
        await self._ensure_not_printing()
        firmware_ref = web_request.get_str("firmware_ref")
        return await self._start_operation("switch_firmware_ref", None,
            [
                *self._base_command(),
                "switch-firmware-ref",
                "--firmware-ref",
                firmware_ref,
                "--klipper-path",
                self.klipper_path,
                "--kalico-path",
                self.kalico_path,
                "--execute",
            ]
        )

    async def _handle_cartographer_usb_to_can(self, web_request: Any) -> dict[str, Any]:
        await self._ensure_not_printing()
        device_id = web_request.get_str("device_id", "cartographer")
        phase = web_request.get_str("phase", "deploy_katapult")
        firmware_version = web_request.get_str("firmware_version", "")
        flavour = web_request.get_str("flavour", "full")
        device_serial = web_request.get_str("device_serial", "")
        canbus_uuid = web_request.get_str("canbus_uuid", "")
        command = [
            *self._base_command(),
            "cartographer-usb-to-can",
            "--cartographer-firmware-path",
            self.cartographer_firmware_path,
            "--katapult-path",
            self.katapult_path,
            "--klipper-path",
            self.klipper_path,
            "--can-interface",
            self.can_interface,
            "--can-bitrate",
            self.can_bitrate,
            "--phase",
            phase,
            "--device-id",
            device_id,
            "--flavour",
            flavour,
            "--execute",
        ]
        if firmware_version:
            command.extend(["--firmware-version", firmware_version])
        if device_serial:
            command.extend(["--device-serial", device_serial])
        if canbus_uuid:
            command.extend(["--canbus-uuid", canbus_uuid])
        return await self._start_operation("cartographer_usb_to_can", device_id, command)

    async def _handle_cartographer_dfu_flash(self, web_request: Any) -> dict[str, Any]:
        await self._ensure_not_printing()
        device_id = web_request.get_str("device_id", "cartographer_dfu")
        firmware_version = web_request.get_str("firmware_version")
        target_protocol = web_request.get_str("target_protocol")
        flavour = web_request.get_str("flavour", "full")
        probe_version = web_request.get_str("probe_version", "v4")
        return await self._start_operation("cartographer_dfu_flash", device_id,
            [
                *self._base_command(),
                "cartographer-dfu-flash",
                "--cartographer-firmware-path",
                self.cartographer_firmware_path,
                "--firmware-version",
                firmware_version,
                "--target-protocol",
                target_protocol,
                "--probe-version",
                probe_version,
                "--flavour",
                flavour,
                "--can-bitrate",
                self.can_bitrate,
                "--dfu-device-id",
                device_id,
                "--moonraker-url",
                self.moonraker_url,
                "--execute",
            ]
        )

    async def _handle_dfu_flash(self, web_request: Any) -> dict[str, Any]:
        await self._ensure_not_printing()
        device_id = web_request.get_str("device_id")
        profile_id = web_request.get_str("profile_id")
        firmware_kind = web_request.get_str("firmware_kind")
        communication = web_request.get_str("communication")
        return await self._start_operation("dfu_flash", device_id,
            [
                *self._base_command(),
                "dfu-flash",
                "--profile",
                profile_id,
                "--firmware-kind",
                firmware_kind,
                "--communication",
                communication,
                *self._profile_args(),
                "--klipper-path",
                self.klipper_path,
                "--kalico-path",
                self.kalico_path,
                "--katapult-path",
                self.katapult_path,
                "--build-root",
                self.build_root,
                "--can-bitrate",
                self.can_bitrate,
                "--dfu-device-id",
                device_id,
                "--moonraker-url",
                self.moonraker_url,
                "--execute",
            ]
        )

    async def _handle_build(self, web_request: Any) -> dict[str, Any]:
        device_id = web_request.get_str("device_id")
        firmware_ref = web_request.get_str("firmware_ref", "current")
        return await self._start_operation("build", device_id,
            [
                *self._base_command(),
                "build-firmware",
                "--device",
                device_id,
                "--firmware-ref",
                firmware_ref,
                "--devices-path",
                self.devices_path,
                "--discovery-json",
                str(await self._ensure_discovery()),
                *self._profile_args(),
                "--klipper-path",
                self.klipper_path,
                "--kalico-path",
                self.kalico_path,
                "--build-root",
                self.build_root,
                "--artifact-root",
                self.artifact_root,
                "--execute",
            ]
        )

    async def _handle_flash(self, web_request: Any) -> dict[str, Any]:
        await self._ensure_not_printing()
        device_id = web_request.get_str("device_id")
        firmware_ref = web_request.get_str("firmware_ref", "current")
        artifact_manifest = (
            self.repo_path
            / self.build_root
            / sanitize_path_part(device_id)
            / sanitize_path_part(firmware_ref)
            / "artifact.json"
        )
        if firmware_ref == "last_good":
            artifact_manifest = self.repo_path / self.build_root / sanitize_path_part(device_id) / "last-good" / "artifact.json"
        elif firmware_ref.startswith("history:"):
            digest = firmware_ref.removeprefix("history:")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise self.server.error("Invalid firmware history reference.")
            artifact_manifest = self.repo_path / self.build_root / sanitize_path_part(device_id) / "history" / digest / "artifact.json"
        return await self._start_operation("flash", device_id,
            [
                *self._base_command(),
                "flash-firmware",
                "--device",
                device_id,
                "--devices-path",
                self.devices_path,
                "--discovery-json",
                str(await self._ensure_discovery()),
                *self._profile_args(),
                "--katapult-path",
                self.katapult_path,
                "--artifact-manifest",
                str(artifact_manifest),
                "--execute",
            ]
        )

    async def _handle_cartographer_flash(self, web_request: Any) -> dict[str, Any]:
        await self._ensure_not_printing()
        device_id = web_request.get_str("device_id")
        firmware_version = web_request.get_str("firmware_version")
        flavour = web_request.get_str("flavour", "full")
        target_protocol = web_request.get_str("target_protocol", "")
        command = [
            *self._base_command(),
            "cartographer-flash",
            "--device",
            device_id,
            "--firmware-version",
            firmware_version,
            "--flavour",
            flavour,
            "--devices-path",
            self.devices_path,
            "--discovery-json",
            str(await self._ensure_discovery()),
            "--cartographer-firmware-path",
            self.cartographer_firmware_path,
            "--katapult-path",
            self.katapult_path,
            "--klipper-path",
            self.klipper_path,
            "--can-bitrate",
            self.can_bitrate,
            "--execute",
        ]
        if target_protocol:
            command.extend(["--target-protocol", target_protocol])
        return await self._start_operation("cartographer_flash", device_id, command)

    async def _handle_verify(self, web_request: Any) -> dict[str, Any]:
        device_id = web_request.get_str("device_id")
        return await self._run_json(
            [
                *self._base_command(),
                "verify-device",
                "--device",
                device_id,
                "--printer-cfg",
                self.printer_cfg,
                "--serial-dir",
                self.serial_dir,
                *self._profile_args(),
                "--can-interface",
                self.can_interface,
                "--katapult-path",
                self.katapult_path,
                "--klipper-path",
                self.klipper_path,
                "--kalico-path",
                self.kalico_path,
                "--devices-path",
                self.devices_path,
            ]
        )

    async def _ensure_discovery(self) -> Path:
        discovery_path = self.repo_path / "discovery.json"
        discovery = await self._run_json(self._discover_command())
        atomic_write_json(discovery_path, discovery)
        return discovery_path

    async def _ensure_not_printing(self) -> None:
        state = await asyncio.to_thread(self._query_print_state)
        if state in {"printing", "paused"}:
            raise self.server.error("MCU firmware changes are blocked while the printer is printing or paused.")
        if state == "unknown":
            raise self.server.error(
                "Cannot verify printer state. Firmware changes are blocked until Moonraker reports that the printer is idle."
            )

    def _query_print_state(self) -> str:
        objects = ["print_stats", "idle_timeout"]
        query = "&".join(quote(item, safe="") for item in objects)
        url = self.moonraker_url.rstrip("/") + "/printer/objects/query?" + query
        try:
            with urlopen(url, timeout=3) as response:
                payload = json.loads(response.read().decode())
        except Exception:
            return "unknown"

        result = payload.get("result", payload)
        status = result.get("status", {}) if isinstance(result, dict) else {}
        print_state = str(status.get("print_stats", {}).get("state") or "").lower()
        idle_state = str(status.get("idle_timeout", {}).get("state") or "").lower()
        if print_state in {"printing", "paused"} or idle_state in {"printing", "paused"}:
            return "printing"
        return print_state or idle_state or "unknown"

    def _status_command(self) -> list[str]:
        return [
            *self._base_command(),
            "status",
            "--printer-cfg",
            self.printer_cfg,
            "--serial-dir",
            self.serial_dir,
            *self._profile_args(),
            "--custom-profile-dir",
            str(self.custom_profile_dir),
            "--can-interface",
            self.can_interface,
            "--can-bitrate",
            self.can_bitrate,
            "--katapult-path",
            self.katapult_path,
            "--klipper-path",
            self.klipper_path,
            "--kalico-path",
            self.kalico_path,
            "--devices-path",
            self.devices_path,
            "--build-root",
            self.build_root,
            "--artifact-root",
            self.artifact_root,
            "--cartographer-firmware-path",
            self.cartographer_firmware_path,
        ]

    def _discover_command(self) -> list[str]:
        return [
            *self._base_command(),
            "discover",
            "--printer-cfg",
            self.printer_cfg,
            "--serial-dir",
            self.serial_dir,
            *self._profile_args(),
            "--can-interface",
            self.can_interface,
            "--can-bitrate",
            self.can_bitrate,
            "--katapult-path",
            self.katapult_path,
            "--klipper-path",
            self.klipper_path,
            "--kalico-path",
            self.kalico_path,
            "--devices-path",
            self.devices_path,
            "--cartographer-firmware-path",
            self.cartographer_firmware_path,
        ]

    def _base_command(self) -> list[str]:
        python = sys.executable or "python3"
        return [python, "-m", "mcu_update_manager.cli"]

    def _profile_args(self) -> list[str]:
        return ["--profiles", *self._profile_paths()]

    def _profile_paths(self) -> list[str]:
        paths = []
        for item in self.profile_dirs.split(","):
            if not item.strip():
                continue
            path = Path(item.strip()).expanduser()
            paths.append(str(path if path.is_absolute() else self.repo_path / path))
        custom = str(self.custom_profile_dir)
        if custom not in paths:
            paths.append(custom)
        return paths

    async def _run_json(self, command: list[str]) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            process.terminate()
            await process.wait()
            raise
        if process.returncode != 0:
            stdout_text = stdout.decode(errors="replace").strip()
            stderr_text = stderr.decode(errors="replace").strip()
            detail = stderr_text or stdout_text or "no output"
            raise self.server.error(
                "\n".join(
                    [
                        f"MCU Update Manager command failed with exit code {process.returncode}.",
                        f"Command: {' '.join(command)}",
                        f"Output: {detail}",
                    ]
                )
            )

        try:
            return json.loads(stdout.decode())
        except json.JSONDecodeError as exc:
            raise self.server.error(f"Invalid MCU Update Manager JSON output: {exc}") from exc

    async def _start_operation(self, action: str, device_id: str | None, command: list[str]) -> dict[str, Any]:
        cached = read_operation(self.cache_path)
        if (self.active_task and not self.active_task.done()) or is_active(cached):
            raise self.server.error("Another MCU Update Manager operation is already running.")

        operation = new_operation(action, device_id)
        write_operation(self.cache_path, operation)
        self.active_task = asyncio.create_task(self._run_cached_operation(operation, command))
        return {"status": "queued", "job_id": operation["job_id"], "job": operation}

    async def _run_cached_operation(self, operation: dict[str, Any], command: list[str]) -> None:
        operation["status"] = "running"
        write_operation(self.cache_path, operation)
        try:
            async with self.operation_lock:
                if operation["action"] in {
                    "switch_firmware_ref", "flash", "cartographer_flash",
                    "cartographer_usb_to_can", "cartographer_dfu_flash", "dfu_flash",
                }:
                    await self._ensure_not_printing()
                result = await self._run_json(command)
            status = operation_result_status(result)
            finish_operation(
                self.cache_path,
                operation,
                status=status,
                result=result,
                error=operation_result_error(result) if status in {"failed", "blocked"} else None,
            )
        except asyncio.CancelledError:
            finish_operation(self.cache_path, operation, status="interrupted", error="Operation was interrupted.")
            raise
        except Exception as exc:
            finish_operation(self.cache_path, operation, status="failed", error=str(exc))

    async def close(self) -> None:
        if not self.active_task or self.active_task.done():
            return
        self.active_task.cancel()
        with suppress(asyncio.CancelledError):
            await self.active_task


def sanitize_path_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in value)


def operation_result_status(result: dict[str, Any]) -> str:
    nested = result.get("flash") if isinstance(result.get("flash"), dict) else None
    nested = nested or (result.get("build") if isinstance(result.get("build"), dict) else None)
    status = str((nested or {}).get("status") or result.get("status") or "ok")
    return status if status in {"ok", "failed", "blocked", "interrupted", "cancelled"} else "ok"


def operation_result_error(result: dict[str, Any]) -> str | None:
    nested = result.get("flash") if isinstance(result.get("flash"), dict) else None
    nested = nested or (result.get("build") if isinstance(result.get("build"), dict) else None)
    error = (nested or {}).get("error") or result.get("error") or result.get("message")
    return str(error) if error else None
