from __future__ import annotations

import argparse
import json

from .build_firmware import build_firmware
from .build_plan import create_build_plan
from .cartographer import flash_cartographer_dfu, flash_cartographer_firmware, switch_cartographer_usb_to_can
from .discovery import DiscoverOptions, discover
from .devices import confirm_device
from .dfu_flash import dfu_flash
from .firmware_source import switch_firmware_ref
from .flash_firmware import flash_firmware
from .flash_plan import create_flash_plan
from .installer import install_katapult, katapult_install_plan
from .prepare_build import prepare_build_workspace
from .profiles import load_profiles
from .status import collect_status
from .verify_device import verify_device


def main() -> None:
    parser = argparse.ArgumentParser(prog="mcu-update-manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover_parser = subparsers.add_parser("discover", help="Run read-only MCU discovery")
    discover_parser.add_argument("--printer-cfg", required=True)
    discover_parser.add_argument("--serial-dir", default="/dev/serial/by-id")
    discover_parser.add_argument("--profiles", nargs="+", default=["profiles"])
    discover_parser.add_argument("--can-interface", default="can0")
    discover_parser.add_argument("--can-query-output")
    discover_parser.add_argument("--katapult-path")
    discover_parser.add_argument("--klipper-path", default="~/klipper")
    discover_parser.add_argument("--kalico-path", default="~/kalico")
    discover_parser.add_argument("--moonraker-url", default="http://127.0.0.1:7125")
    discover_parser.add_argument("--refresh-repositories", action="store_true")
    discover_parser.add_argument("--printer-objects-json")
    discover_parser.add_argument("--devices-path", default="devices.yaml")
    discover_parser.add_argument("--firmware-project", default="kalico", choices=["klipper", "kalico"])
    discover_parser.add_argument("--firmware-ref", default="current")
    discover_parser.add_argument("--can-bitrate", default="1000000")
    discover_parser.add_argument("--cartographer-firmware-path", default="~/cartographer_firmware")

    install_parser = subparsers.add_parser("install-katapult", help="Plan or execute Katapult install")
    install_parser.add_argument("--path", default="~/katapult")
    install_parser.add_argument("--execute", action="store_true")

    switch_ref_parser = subparsers.add_parser("switch-firmware-ref", help="Switch Klipper/Kalico repository to a selected version")
    switch_ref_parser.add_argument("--firmware-ref", required=True)
    switch_ref_parser.add_argument("--klipper-path", default="~/klipper")
    switch_ref_parser.add_argument("--kalico-path", default="~/kalico")
    switch_ref_parser.add_argument("--allow-dirty", action="store_true")
    switch_ref_parser.add_argument("--execute", action="store_true")

    confirm_parser = subparsers.add_parser("confirm-device", help="Confirm a discovered device profile")
    confirm_parser.add_argument("--devices-path", default="devices.yaml")
    confirm_parser.add_argument("--discovery-json", default="discovery.json")
    confirm_parser.add_argument("--device", required=True)
    confirm_parser.add_argument("--profile", required=True)

    build_plan_parser = subparsers.add_parser("build-plan", help="Create a dry-run firmware build plan")
    build_plan_parser.add_argument("--device", required=True)
    build_plan_parser.add_argument("--firmware-ref", default="current")
    build_plan_parser.add_argument("--devices-path", default="devices.yaml")
    build_plan_parser.add_argument("--discovery-json", default="discovery.json")
    build_plan_parser.add_argument("--profiles", nargs="+", default=["profiles"])
    build_plan_parser.add_argument("--klipper-path", default="~/klipper")
    build_plan_parser.add_argument("--kalico-path", default="~/kalico")
    build_plan_parser.add_argument("--can-bitrate", default="1000000")

    prepare_build_parser = subparsers.add_parser("prepare-build", help="Prepare build workspace without compiling")
    prepare_build_parser.add_argument("--device", required=True)
    prepare_build_parser.add_argument("--firmware-ref", default="current")
    prepare_build_parser.add_argument("--devices-path", default="devices.yaml")
    prepare_build_parser.add_argument("--discovery-json", default="discovery.json")
    prepare_build_parser.add_argument("--profiles", nargs="+", default=["profiles"])
    prepare_build_parser.add_argument("--klipper-path", default="~/klipper")
    prepare_build_parser.add_argument("--kalico-path", default="~/kalico")
    prepare_build_parser.add_argument("--build-root", default="builds")
    prepare_build_parser.add_argument("--artifact-root", default="artifacts")
    prepare_build_parser.add_argument("--can-bitrate", default="1000000")

    build_firmware_parser = subparsers.add_parser("build-firmware", help="Build firmware artifact")
    build_firmware_parser.add_argument("--device", required=True)
    build_firmware_parser.add_argument("--firmware-ref", default="current")
    build_firmware_parser.add_argument("--devices-path", default="devices.yaml")
    build_firmware_parser.add_argument("--discovery-json", default="discovery.json")
    build_firmware_parser.add_argument("--profiles", nargs="+", default=["profiles"])
    build_firmware_parser.add_argument("--klipper-path", default="~/klipper")
    build_firmware_parser.add_argument("--kalico-path", default="~/kalico")
    build_firmware_parser.add_argument("--build-root", default="builds")
    build_firmware_parser.add_argument("--artifact-root", default="artifacts")
    build_firmware_parser.add_argument("--can-bitrate", default="1000000")
    build_firmware_parser.add_argument("--execute", action="store_true")

    flash_plan_parser = subparsers.add_parser("flash-plan", help="Create a dry-run firmware flash plan")
    flash_plan_parser.add_argument("--device", required=True)
    flash_plan_parser.add_argument("--devices-path", default="devices.yaml")
    flash_plan_parser.add_argument("--discovery-json", default="discovery.json")
    flash_plan_parser.add_argument("--profiles", nargs="+", default=["profiles"])
    flash_plan_parser.add_argument("--katapult-path", default="~/katapult")
    flash_plan_parser.add_argument("--artifact")
    flash_plan_parser.add_argument("--artifact-manifest")

    flash_firmware_parser = subparsers.add_parser("flash-firmware", help="Flash firmware artifact with Katapult")
    flash_firmware_parser.add_argument("--device", required=True)
    flash_firmware_parser.add_argument("--devices-path", default="devices.yaml")
    flash_firmware_parser.add_argument("--discovery-json", default="discovery.json")
    flash_firmware_parser.add_argument("--profiles", nargs="+", default=["profiles"])
    flash_firmware_parser.add_argument("--katapult-path", default="~/katapult")
    flash_firmware_parser.add_argument("--artifact")
    flash_firmware_parser.add_argument("--artifact-manifest")
    flash_firmware_parser.add_argument("--execute", action="store_true")

    cartographer_flash_parser = subparsers.add_parser("cartographer-flash", help="Flash Cartographer vendor firmware")
    cartographer_flash_parser.add_argument("--device", required=True)
    cartographer_flash_parser.add_argument("--firmware-version", required=True)
    cartographer_flash_parser.add_argument("--flavour", default="full", choices=["full", "lite"])
    cartographer_flash_parser.add_argument("--target-protocol", choices=["USB", "CAN", "usb", "can"])
    cartographer_flash_parser.add_argument("--devices-path", default="devices.yaml")
    cartographer_flash_parser.add_argument("--discovery-json", default="discovery.json")
    cartographer_flash_parser.add_argument("--cartographer-firmware-path", default="~/cartographer_firmware")
    cartographer_flash_parser.add_argument("--katapult-path", default="~/katapult")
    cartographer_flash_parser.add_argument("--klipper-path", default="~/klipper")
    cartographer_flash_parser.add_argument("--can-bitrate", default="1000000")
    cartographer_flash_parser.add_argument("--execute", action="store_true")

    dfu_flash_parser = subparsers.add_parser("dfu-flash", help="Build and flash firmware to a board in DFU/BOOTSEL mode")
    dfu_flash_parser.add_argument("--profile", required=True)
    dfu_flash_parser.add_argument("--firmware-kind", required=True, choices=["katapult", "klipper"])
    dfu_flash_parser.add_argument("--communication", required=True, choices=["usb", "canbus", "usb_to_canbus_bridge"])
    dfu_flash_parser.add_argument("--profiles", nargs="+", default=["profiles"])
    dfu_flash_parser.add_argument("--klipper-path", default="~/klipper")
    dfu_flash_parser.add_argument("--kalico-path", default="~/kalico")
    dfu_flash_parser.add_argument("--katapult-path", default="~/katapult")
    dfu_flash_parser.add_argument("--build-root", default="builds")
    dfu_flash_parser.add_argument("--firmware-ref", default="current")
    dfu_flash_parser.add_argument("--dfu-device-id")
    dfu_flash_parser.add_argument("--moonraker-url", default="http://127.0.0.1:7125")
    dfu_flash_parser.add_argument("--can-bitrate", default="1000000")
    dfu_flash_parser.add_argument("--execute", action="store_true")

    cartographer_switch_parser = subparsers.add_parser("cartographer-usb-to-can", help="Run Cartographer V4 USB to CAN switch workflow")
    cartographer_switch_parser.add_argument("--cartographer-firmware-path", default="~/cartographer_firmware")
    cartographer_switch_parser.add_argument("--katapult-path", default="~/katapult")
    cartographer_switch_parser.add_argument("--klipper-path", default="~/klipper")
    cartographer_switch_parser.add_argument("--can-interface", default="can0")
    cartographer_switch_parser.add_argument("--firmware-version")
    cartographer_switch_parser.add_argument("--flavour", default="full", choices=["full", "lite"])
    cartographer_switch_parser.add_argument("--phase", default="deploy_katapult", choices=["deploy_katapult", "flash_can"])
    cartographer_switch_parser.add_argument("--device-serial")
    cartographer_switch_parser.add_argument("--device-id", default="cartographer_usb")
    cartographer_switch_parser.add_argument("--canbus-uuid")
    cartographer_switch_parser.add_argument("--can-bitrate", default="1000000")
    cartographer_switch_parser.add_argument("--execute", action="store_true")

    cartographer_dfu_parser = subparsers.add_parser("cartographer-dfu-flash", help="Flash Cartographer full firmware in STM32 DFU mode")
    cartographer_dfu_parser.add_argument("--cartographer-firmware-path", default="~/cartographer_firmware")
    cartographer_dfu_parser.add_argument("--firmware-version", required=True)
    cartographer_dfu_parser.add_argument("--target-protocol", required=True, choices=["USB", "CAN", "usb", "can"])
    cartographer_dfu_parser.add_argument("--probe-version", default="v4", choices=["v3", "v4"])
    cartographer_dfu_parser.add_argument("--flavour", default="full", choices=["full", "lite"])
    cartographer_dfu_parser.add_argument("--can-bitrate", default="1000000")
    cartographer_dfu_parser.add_argument("--dfu-vid-pid", default="0483:df11")
    cartographer_dfu_parser.add_argument("--dfu-device-id")
    cartographer_dfu_parser.add_argument("--moonraker-url", default="http://127.0.0.1:7125")
    cartographer_dfu_parser.add_argument("--execute", action="store_true")

    verify_parser = subparsers.add_parser("verify-device", help="Verify a device after flashing")
    verify_parser.add_argument("--device", required=True)
    verify_parser.add_argument("--printer-cfg", required=True)
    verify_parser.add_argument("--serial-dir", default="/dev/serial/by-id")
    verify_parser.add_argument("--profiles", nargs="+", default=["profiles"])
    verify_parser.add_argument("--can-interface", default="can0")
    verify_parser.add_argument("--katapult-path", default="~/katapult")
    verify_parser.add_argument("--klipper-path", default="~/klipper")
    verify_parser.add_argument("--kalico-path", default="~/kalico")
    verify_parser.add_argument("--moonraker-url", default="http://127.0.0.1:7125")
    verify_parser.add_argument("--devices-path", default="devices.yaml")
    verify_parser.add_argument("--artifact-manifest")
    verify_parser.add_argument("--printer-objects-json")
    verify_parser.add_argument("--can-query-output")

    status_parser = subparsers.add_parser("status", help="Collect GUI-friendly MCU Update Manager status")
    status_parser.add_argument("--printer-cfg", required=True)
    status_parser.add_argument("--serial-dir", default="/dev/serial/by-id")
    status_parser.add_argument("--profiles", nargs="+", default=["profiles"])
    status_parser.add_argument("--can-interface", default="can0")
    status_parser.add_argument("--can-query-output")
    status_parser.add_argument("--katapult-path", default="~/katapult")
    status_parser.add_argument("--klipper-path", default="~/klipper")
    status_parser.add_argument("--kalico-path", default="~/kalico")
    status_parser.add_argument("--moonraker-url", default="http://127.0.0.1:7125")
    status_parser.add_argument("--devices-path", default="devices.yaml")
    status_parser.add_argument("--firmware-project", default="kalico", choices=["klipper", "kalico"])
    status_parser.add_argument("--firmware-ref", default="current")
    status_parser.add_argument("--can-bitrate", default="1000000")
    status_parser.add_argument("--cartographer-firmware-path", default="~/cartographer_firmware")
    status_parser.add_argument("--build-root", default="builds")
    status_parser.add_argument("--artifact-root", default="artifacts")
    status_parser.add_argument("--refresh-repositories", action="store_true")
    status_parser.add_argument("--printer-objects-json")

    args = parser.parse_args()
    if args.command == "discover":
        profiles = load_profiles(args.profiles)
        result = discover(
            DiscoverOptions(
                printer_cfg=args.printer_cfg,
                serial_dir=args.serial_dir,
                can_interface=args.can_interface,
                can_query_output=args.can_query_output,
                katapult_path=args.katapult_path,
                klipper_path=args.klipper_path,
                kalico_path=args.kalico_path,
                moonraker_url=args.moonraker_url,
                refresh_repositories=args.refresh_repositories,
                printer_objects_json=args.printer_objects_json,
                devices_path=args.devices_path,
                firmware_project=args.firmware_project,
                firmware_ref=args.firmware_ref,
                can_bitrate=args.can_bitrate,
                cartographer_firmware_path=args.cartographer_firmware_path,
            ),
            profiles=profiles,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "install-katapult":
        result = install_katapult(args.path) if args.execute else katapult_install_plan(args.path)
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "switch-firmware-ref":
        result = switch_firmware_ref(
            firmware_ref=args.firmware_ref,
            klipper_path=args.klipper_path,
            kalico_path=args.kalico_path,
            allow_dirty=args.allow_dirty,
            execute=args.execute,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "confirm-device":
        result = confirm_device(args.devices_path, args.discovery_json, args.device, args.profile)
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "build-plan":
        result = create_build_plan(
            device_id=args.device,
            firmware_ref=args.firmware_ref,
            devices_path=args.devices_path,
            discovery_path=args.discovery_json,
            profile_paths=args.profiles,
            klipper_path=args.klipper_path,
            kalico_path=args.kalico_path,
            can_bitrate=args.can_bitrate,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "prepare-build":
        result = prepare_build_workspace(
            device_id=args.device,
            firmware_ref=args.firmware_ref,
            devices_path=args.devices_path,
            discovery_path=args.discovery_json,
            profile_paths=args.profiles,
            klipper_path=args.klipper_path,
            kalico_path=args.kalico_path,
            build_root=args.build_root,
            artifact_root=args.artifact_root,
            can_bitrate=args.can_bitrate,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "build-firmware":
        result = build_firmware(
            device_id=args.device,
            firmware_ref=args.firmware_ref,
            devices_path=args.devices_path,
            discovery_path=args.discovery_json,
            profile_paths=args.profiles,
            klipper_path=args.klipper_path,
            kalico_path=args.kalico_path,
            build_root=args.build_root,
            artifact_root=args.artifact_root,
            can_bitrate=args.can_bitrate,
            execute=args.execute,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "flash-plan":
        result = create_flash_plan(
            device_id=args.device,
            devices_path=args.devices_path,
            discovery_path=args.discovery_json,
            profile_paths=args.profiles,
            katapult_path=args.katapult_path,
            artifact=args.artifact,
            artifact_manifest=args.artifact_manifest,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "flash-firmware":
        result = flash_firmware(
            device_id=args.device,
            devices_path=args.devices_path,
            discovery_path=args.discovery_json,
            profile_paths=args.profiles,
            katapult_path=args.katapult_path,
            artifact=args.artifact,
            artifact_manifest=args.artifact_manifest,
            execute=args.execute,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "cartographer-flash":
        result = flash_cartographer_firmware(
            device_id=args.device,
            discovery_path=args.discovery_json,
            devices_path=args.devices_path,
            cartographer_firmware_path=args.cartographer_firmware_path,
            katapult_path=args.katapult_path,
            klipper_path=args.klipper_path,
            can_bitrate=args.can_bitrate,
            firmware_version=args.firmware_version,
            flavour=args.flavour,
            target_protocol=args.target_protocol,
            execute=args.execute,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "dfu-flash":
        result = dfu_flash(
            profile_id=args.profile,
            firmware_kind=args.firmware_kind,
            communication=args.communication,
            profile_paths=args.profiles,
            klipper_path=args.klipper_path,
            kalico_path=args.kalico_path,
            katapult_path=args.katapult_path,
            build_root=args.build_root,
            firmware_ref=args.firmware_ref,
            can_bitrate=args.can_bitrate,
            execute=args.execute,
            dfu_device_id=args.dfu_device_id,
            moonraker_url=args.moonraker_url,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "cartographer-usb-to-can":
        result = switch_cartographer_usb_to_can(
            cartographer_firmware_path=args.cartographer_firmware_path,
            katapult_path=args.katapult_path,
            klipper_path=args.klipper_path,
            can_interface=args.can_interface,
            firmware_version=args.firmware_version,
            flavour=args.flavour,
            phase=args.phase,
            device_serial=args.device_serial,
            device_id=args.device_id,
            canbus_uuid=args.canbus_uuid,
            can_bitrate=args.can_bitrate,
            execute=args.execute,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "cartographer-dfu-flash":
        result = flash_cartographer_dfu(
            cartographer_firmware_path=args.cartographer_firmware_path,
            firmware_version=args.firmware_version,
            target_protocol=args.target_protocol,
            probe_version=args.probe_version,
            flavour=args.flavour,
            can_bitrate=args.can_bitrate,
            dfu_vid_pid=args.dfu_vid_pid,
            execute=args.execute,
            dfu_device_id=args.dfu_device_id,
            moonraker_url=args.moonraker_url,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "verify-device":
        result = verify_device(
            device_id=args.device,
            printer_cfg=args.printer_cfg,
            serial_dir=args.serial_dir,
            profile_paths=args.profiles,
            can_interface=args.can_interface,
            katapult_path=args.katapult_path,
            klipper_path=args.klipper_path,
            kalico_path=args.kalico_path,
            moonraker_url=args.moonraker_url,
            devices_path=args.devices_path,
            artifact_manifest=args.artifact_manifest,
            printer_objects_json=args.printer_objects_json,
            can_query_output=args.can_query_output,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.command == "status":
        result = collect_status(
            printer_cfg=args.printer_cfg,
            serial_dir=args.serial_dir,
            profile_paths=args.profiles,
            can_interface=args.can_interface,
            can_query_output=args.can_query_output,
            katapult_path=args.katapult_path,
            klipper_path=args.klipper_path,
            kalico_path=args.kalico_path,
            moonraker_url=args.moonraker_url,
            devices_path=args.devices_path,
            firmware_project=args.firmware_project,
            firmware_ref=args.firmware_ref,
            can_bitrate=args.can_bitrate,
            cartographer_firmware_path=args.cartographer_firmware_path,
            build_root=args.build_root,
            artifact_root=args.artifact_root,
            refresh_repositories=args.refresh_repositories,
            printer_objects_json=args.printer_objects_json,
        )
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
