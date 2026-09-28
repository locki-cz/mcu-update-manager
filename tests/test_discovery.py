from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest
from unittest import mock

from mcu_update_manager.build_plan import create_build_plan
from mcu_update_manager.build_firmware import build_firmware, create_artifact_manifest, file_sha256, validate_resolved_config
from mcu_update_manager.devices import confirm_device
from mcu_update_manager.discovery import DiscoverOptions, discover, discover_dfu_candidates, scan_usb_dfu_devices
from mcu_update_manager.dfu_flash import generate_katapult_dot_config
from mcu_update_manager.flash_firmware import flash_firmware, require_query_application, validate_preflight
from mcu_update_manager.flash_plan import create_flash_plan
from mcu_update_manager.firmware_source import dedupe_version_options, detect_project
from mcu_update_manager.prepare_build import generate_klipper_dot_config, prepare_build_workspace
from mcu_update_manager.profiles import HardwareProfile, load_profiles
from mcu_update_manager.status import (
    artifact_history_for_device,
    device_actions,
    firmware_state,
    printer_cfg_snippet,
    status_summary,
)
from mcu_update_manager.verify_device import runtime_checks, verify_device


ROOT = Path(__file__).resolve().parents[1]


class DiscoveryTest(unittest.TestCase):
    def test_discovery_maps_cfg_runtime_and_profiles(self):
        profiles = load_profiles([ROOT / "profiles"])
        result = discover(
            DiscoverOptions(
                printer_cfg=str(ROOT / "tests" / "fixtures" / "printer.cfg"),
                serial_dir=str(ROOT / "tests" / "fixtures" / "serial-by-id"),
                can_query_output=str(ROOT / "tests" / "fixtures" / "can-query.txt"),
                katapult_path=str(ROOT / "tests" / "fixtures" / "katapult"),
                klipper_path=str(ROOT / "tests" / "fixtures" / "missing-klipper"),
                kalico_path=str(ROOT / "tests" / "fixtures" / "missing-kalico"),
                printer_objects_json=str(ROOT / "tests" / "fixtures" / "printer-objects.json"),
            ),
            profiles=profiles,
        )

        devices = {device["name"]: device for device in result["devices"]}

        self.assertEqual(result["firmware_source"]["project"], "kalico")
        self.assertEqual(
            result["firmware_source"]["repositories"]["sources"][0]["status"],
            "missing_path",
        )
        self.assertEqual(result["preflight"]["katapult"]["status"], "ok")
        self.assertEqual(devices["ebb"]["transport"], "can")
        self.assertEqual(devices["ebb"]["detected_chip"], "stm32g0b1xx")
        self.assertEqual(devices["ebb"]["can_node"]["application"], "Klipper")
        self.assertIn("PB14", devices["ebb"]["referenced_pins"])
        self.assertEqual(devices["ebb"]["likely_profiles"][0]["id"], "btt_ebb36_gen2_can")
        self.assertIn("printer_cfg_pins", devices["ebb"]["likely_profiles"][0]["reasons"])
        self.assertEqual(
            devices["mcu"]["matching_serial_devices"][0]["name"],
            "usb-Klipper_stm32h723xx_35003D001851303235383730-if00",
        )
        self.assertEqual(devices["cartographer"]["likely_profiles"][0]["id"], "cartographer_can")
        self.assertEqual(
            result["firmware_source"]["repositories"]["default_project"],
            None,
        )

    def test_discovery_reports_missing_katapult(self):
        profiles = load_profiles([ROOT / "profiles"])
        result = discover(
            DiscoverOptions(
                printer_cfg=str(ROOT / "tests" / "fixtures" / "printer.cfg"),
                serial_dir=str(ROOT / "tests" / "fixtures" / "serial-by-id"),
                can_query_output=str(ROOT / "tests" / "fixtures" / "can-query.txt"),
                printer_objects_json=str(ROOT / "tests" / "fixtures" / "printer-objects.json"),
            ),
            profiles=profiles,
        )

        self.assertEqual(result["preflight"]["katapult"]["status"], "not_configured")
        self.assertEqual(result["preflight"]["katapult"]["install_hint"]["action"], "install_katapult")

    @mock.patch("mcu_update_manager.discovery.subprocess.run")
    def test_scan_usb_dfu_devices_keeps_bus_device_and_vid_pid(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["lsusb"],
            0,
            stdout="Bus 001 Device 006: ID 0483:df11 STMicroelectronics STM Device in DFU Mode\n",
            stderr="",
        )

        devices = scan_usb_dfu_devices()

        self.assertEqual(devices[0]["bus"], "001")
        self.assertEqual(devices[0]["device"], "006")
        self.assertEqual(devices[0]["vid_pid"], "0483:df11")

    @mock.patch("mcu_update_manager.discovery.scan_usb_dfu_devices")
    def test_dfu_candidate_is_neutral_and_requires_manual_profile_selection(self, scan_mock):
        scan_mock.return_value = [
            {
                "bus": "001",
                "device": "006",
                "vid_pid": "0483:df11",
                "description": "Bus 001 Device 006: ID 0483:df11 STMicroelectronics STM Device in DFU Mode",
            }
        ]
        profiles = [
            HardwareProfile(
                id="stm32_board",
                name="Example STM32 Board",
                family="mainboard",
                initial_flash={"method": "dfu_util", "dfu_vid_pid": "0483:df11"},
            ),
            HardwareProfile(id="rp2040_board", name="Example RP2040 Board", family="toolhead"),
        ]

        devices = discover_dfu_candidates(profiles=profiles, confirmed_devices={})

        self.assertEqual(devices[0]["id"], "dfu_001_006_0483_df11")
        self.assertEqual(devices[0]["name"], "STM32 DFU")
        self.assertIsNone(devices[0]["detected_chip"])
        self.assertNotIn("vendor_firmware", devices[0])
        self.assertEqual(devices[0]["display_id"], "Bus 001 Device 006: ID 0483:df11")
        self.assertEqual([profile["id"] for profile in devices[0]["likely_profiles"]], ["stm32_board"])

    def test_discovery_adds_cartographer_usb_from_serial_by_id(self):
        profiles = load_profiles([ROOT / "profiles"])
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            printer_cfg = tmpdir_path / "printer.cfg"
            printer_cfg.write_text("[printer]\nkinematics: corexy\n", encoding="utf-8")
            serial_dir = tmpdir_path / "serial-by-id"
            serial_dir.mkdir()
            (serial_dir / "usb-Cartographer3D_Cartographer_123456-if00").write_text("", encoding="utf-8")
            cartographer_root = tmpdir_path / "cartographer_firmware"
            cartographer_root.mkdir()
            (cartographer_root / "v4_switch.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (cartographer_root / "firmware_list.csv").write_text(
                "\n".join(
                    [
                        "filepath,filename,firmware_type,protocol,process,probe_version,firmware_version,can_speed,is_lite,min_plugin_version",
                        "v4/firmware/6.2.0/CartographerV4_6.2.0_USB_full_8kib_offset.bin,CartographerV4_6.2.0_USB_full_8kib_offset.bin,Cartographer,USB,Update,v4,6.2.0,,No,1.6.0",
                    ]
                ),
                encoding="utf-8",
            )

            result = discover(
                DiscoverOptions(
                    printer_cfg=str(printer_cfg),
                    serial_dir=str(serial_dir),
                    moonraker_url=None,
                    cartographer_firmware_path=str(cartographer_root),
                ),
                profiles=profiles,
            )

        devices = {device["id"]: device for device in result["devices"]}
        self.assertIn("cartographer_usb", devices)
        self.assertEqual(devices["cartographer_usb"]["transport"], "usb")
        self.assertEqual(devices["cartographer_usb"]["likely_profiles"][0]["id"], "cartographer_usb")
        self.assertEqual(devices["cartographer_usb"]["vendor_firmware"]["protocol"], "USB")
        self.assertTrue(devices["cartographer_usb"]["vendor_firmware"]["usb_to_can"]["available"])

    def test_cartographer_usb_actions_do_not_enable_vendor_flash(self):
        actions = device_actions(
            {
                "id": "cartographer_usb",
                "transport": "usb",
                "serial": "/dev/serial/by-id/usb-Cartographer_stm32g431xx_test",
                "vendor_firmware": {
                    "manager": "cartographer",
                    "status": "ok",
                    "selected": {"version": "6.2.0"},
                    "usb_to_can": {"available": True},
                },
            },
            {"confirmed_profile": "cartographer_usb"},
            None,
        )

        self.assertFalse(actions["vendor_flash_ready"])
        self.assertTrue(actions["cartographer_usb_to_can_ready"])

    def test_discovery_adds_unconfigured_cartographer_can_candidate_from_katapult_node(self):
        profiles = load_profiles([ROOT / "profiles"])
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            printer_cfg = tmpdir_path / "printer.cfg"
            printer_cfg.write_text("[printer]\nkinematics: corexy\n", encoding="utf-8")
            can_query = tmpdir_path / "can-query.txt"
            can_query.write_text("Detected UUID: abc123abc123, Application: Katapult\n", encoding="utf-8")
            cartographer_root = tmpdir_path / "cartographer_firmware"
            firmware_dir = cartographer_root / "firmware" / "v4" / "firmware" / "6.2.0"
            firmware_dir.mkdir(parents=True)
            firmware_file = firmware_dir / "CartographerV4_6.2.0_CAN_1M_full_8kib_offset.bin"
            firmware_file.write_bytes(b"firmware")
            (cartographer_root / "firmware_list.csv").write_text(
                "\n".join(
                    [
                        "filepath,filename,firmware_type,protocol,process,probe_version,firmware_version,can_speed,is_lite,min_plugin_version",
                        "v4/firmware/6.2.0/CartographerV4_6.2.0_CAN_1M_full_8kib_offset.bin,CartographerV4_6.2.0_CAN_1M_full_8kib_offset.bin,Cartographer,CAN,Update,v4,6.2.0,1000000,No,1.6.0",
                    ]
                ),
                encoding="utf-8",
            )

            result = discover(
                DiscoverOptions(
                    printer_cfg=str(printer_cfg),
                    serial_dir=str(tmpdir_path / "missing-serial"),
                    can_query_output=str(can_query),
                    moonraker_url=None,
                    cartographer_firmware_path=str(cartographer_root),
                ),
                profiles=profiles,
            )

        devices = {device["id"]: device for device in result["devices"]}
        self.assertIn("cartographer_can_abc123abc123", devices)
        device = devices["cartographer_can_abc123abc123"]
        self.assertEqual(device["transport"], "can")
        self.assertEqual(device["likely_profiles"][0]["id"], "cartographer_can")
        self.assertEqual(device["vendor_firmware"]["protocol"], "CAN")
        self.assertEqual(device["vendor_firmware"]["selected"]["version"], "6.2.0")

    def test_detect_project_from_remote(self):
        self.assertEqual(detect_project("source", "https://github.com/KalicoCrew/kalico.git", ""), "kalico")
        self.assertEqual(detect_project("source", "https://github.com/Klipper3d/klipper.git", ""), "klipper")

    def test_dedupe_version_options_preserves_order(self):
        options = dedupe_version_options(
            [
                {"kind": "current", "value": "current"},
                {"kind": "tag", "value": "v1"},
                {"kind": "tag", "value": "v1"},
            ]
        )

        self.assertEqual([option["value"] for option in options], ["current", "v1"])

    def test_confirm_device_writes_devices_yaml_and_discovery_reads_it(self):
        profiles = load_profiles([ROOT / "profiles"])
        discovery_path = ROOT / "tests" / "fixtures" / "discovery-confirm.json"

        result = discover(
            DiscoverOptions(
                printer_cfg=str(ROOT / "tests" / "fixtures" / "printer.cfg"),
                serial_dir=str(ROOT / "tests" / "fixtures" / "serial-by-id"),
                can_query_output=str(ROOT / "tests" / "fixtures" / "can-query.txt"),
                katapult_path=str(ROOT / "tests" / "fixtures" / "katapult"),
                klipper_path=str(ROOT / "tests" / "fixtures" / "missing-klipper"),
                kalico_path=str(ROOT / "tests" / "fixtures" / "missing-kalico"),
                printer_objects_json=str(ROOT / "tests" / "fixtures" / "printer-objects.json"),
                devices_path=None,
            ),
            profiles=profiles,
        )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            local_discovery = tmpdir_path / "discovery.json"
            local_devices = tmpdir_path / "devices.yaml"
            local_discovery.write_text(__import__("json").dumps(result), encoding="utf-8")

            confirm_device(local_devices, local_discovery, "ebb", "btt_ebb36_gen2_can")
            confirmed_result = discover(
                DiscoverOptions(
                    printer_cfg=str(ROOT / "tests" / "fixtures" / "printer.cfg"),
                    serial_dir=str(ROOT / "tests" / "fixtures" / "serial-by-id"),
                    can_query_output=str(ROOT / "tests" / "fixtures" / "can-query.txt"),
                    katapult_path=str(ROOT / "tests" / "fixtures" / "katapult"),
                    klipper_path=str(ROOT / "tests" / "fixtures" / "missing-klipper"),
                    kalico_path=str(ROOT / "tests" / "fixtures" / "missing-kalico"),
                    printer_objects_json=str(ROOT / "tests" / "fixtures" / "printer-objects.json"),
                    devices_path=str(local_devices),
                ),
                profiles=profiles,
            )

        devices = {device["id"]: device for device in confirmed_result["devices"]}
        self.assertEqual(devices["ebb"]["confirmation_status"], "confirmed")
        self.assertEqual(devices["ebb"]["confirmed_profile"], "btt_ebb36_gen2_can")

    def test_build_plan_uses_confirmed_profile(self):
        profiles = load_profiles([ROOT / "profiles"])
        result = discover(
            DiscoverOptions(
                printer_cfg=str(ROOT / "tests" / "fixtures" / "printer.cfg"),
                serial_dir=str(ROOT / "tests" / "fixtures" / "serial-by-id"),
                can_query_output=str(ROOT / "tests" / "fixtures" / "can-query.txt"),
                katapult_path=str(ROOT / "tests" / "fixtures" / "katapult"),
                klipper_path=str(ROOT / "tests" / "fixtures" / "missing-klipper"),
                kalico_path=str(ROOT / "tests" / "fixtures" / "missing-kalico"),
                printer_objects_json=str(ROOT / "tests" / "fixtures" / "printer-objects.json"),
                devices_path=None,
            ),
            profiles=profiles,
        )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            discovery_path = tmpdir_path / "discovery.json"
            devices_path = tmpdir_path / "devices.yaml"
            firmware_repo = tmpdir_path / "kalico"
            firmware_repo.mkdir()
            subprocess.run(["git", "init"], cwd=firmware_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=firmware_repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=firmware_repo, check=True)
            (firmware_repo / "README.md").write_text("Kalico test repo", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=firmware_repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=firmware_repo, check=True, capture_output=True)
            subprocess.run(["git", "tag", "v2026.06.00"], cwd=firmware_repo, check=True)
            discovery_path.write_text(__import__("json").dumps(result), encoding="utf-8")
            confirm_device(devices_path, discovery_path, "ebb", "btt_ebb36_gen2_can")

            plan = create_build_plan(
                device_id="ebb",
                firmware_ref="v2026.06.00",
                devices_path=devices_path,
                discovery_path=discovery_path,
                profile_paths=[ROOT / "profiles"],
                klipper_path=None,
                kalico_path=str(firmware_repo),
            )

        self.assertEqual(plan["profile"]["id"], "btt_ebb36_gen2_can")
        self.assertEqual(plan["firmware_source"]["selected_ref"], "v2026.06.00")
        self.assertEqual(plan["build_config"]["name"], "ebb")
        self.assertEqual(plan["klipper_config_preview"]["can_rx_pin"], "PB12")
        self.assertEqual(plan["klipper_config_preview"]["can_tx_pin"], "PB13")

    def test_prepare_build_writes_workspace_files(self):
        profiles = load_profiles([ROOT / "profiles"])
        result = discover(
            DiscoverOptions(
                printer_cfg=str(ROOT / "tests" / "fixtures" / "printer.cfg"),
                serial_dir=str(ROOT / "tests" / "fixtures" / "serial-by-id"),
                can_query_output=str(ROOT / "tests" / "fixtures" / "can-query.txt"),
                katapult_path=str(ROOT / "tests" / "fixtures" / "katapult"),
                klipper_path=str(ROOT / "tests" / "fixtures" / "missing-klipper"),
                kalico_path=str(ROOT / "tests" / "fixtures" / "missing-kalico"),
                printer_objects_json=str(ROOT / "tests" / "fixtures" / "printer-objects.json"),
                devices_path=None,
            ),
            profiles=profiles,
        )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            discovery_path = tmpdir_path / "discovery.json"
            devices_path = tmpdir_path / "devices.yaml"
            firmware_repo = tmpdir_path / "kalico"
            firmware_repo.mkdir()
            subprocess.run(["git", "init"], cwd=firmware_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=firmware_repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=firmware_repo, check=True)
            (firmware_repo / "README.md").write_text("Kalico test repo", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=firmware_repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=firmware_repo, check=True, capture_output=True)
            discovery_path.write_text(__import__("json").dumps(result), encoding="utf-8")
            confirm_device(devices_path, discovery_path, "ebb", "btt_ebb36_gen2_can")

            prepared = prepare_build_workspace(
                device_id="ebb",
                firmware_ref="current",
                devices_path=devices_path,
                discovery_path=discovery_path,
                profile_paths=[ROOT / "profiles"],
                klipper_path=None,
                kalico_path=str(firmware_repo),
                build_root=tmpdir_path / "builds",
                artifact_root=tmpdir_path / "artifacts",
            )

            config_path = Path(prepared["workspace"]["config"])
            self.assertTrue(config_path.exists())
            self.assertTrue(config_path.is_absolute())
            self.assertEqual(config_path.name, "config.ebb")
            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn("CONFIG_LOW_LEVEL_OPTIONS=y", config_text)
            self.assertIn("CONFIG_STM32_FLASH_START_2000=y", config_text)
            self.assertIn("CONFIG_STM32_CLOCK_REF_8M=y", config_text)
            self.assertIn("CONFIG_STM32_MMENU_CANBUS_PB12_PB13=y", config_text)
            self.assertIn("CONFIG_CAN_UUID_USE_CHIPID=y", config_text)
            self.assertIn("CONFIG_CANBUS_FREQUENCY=1000000", config_text)

    def test_build_firmware_without_execute_is_plan_only(self):
        profiles = load_profiles([ROOT / "profiles"])
        result = discover(
            DiscoverOptions(
                printer_cfg=str(ROOT / "tests" / "fixtures" / "printer.cfg"),
                serial_dir=str(ROOT / "tests" / "fixtures" / "serial-by-id"),
                can_query_output=str(ROOT / "tests" / "fixtures" / "can-query.txt"),
                katapult_path=str(ROOT / "tests" / "fixtures" / "katapult"),
                klipper_path=str(ROOT / "tests" / "fixtures" / "missing-klipper"),
                kalico_path=str(ROOT / "tests" / "fixtures" / "missing-kalico"),
                printer_objects_json=str(ROOT / "tests" / "fixtures" / "printer-objects.json"),
                devices_path=None,
            ),
            profiles=profiles,
        )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            discovery_path = tmpdir_path / "discovery.json"
            devices_path = tmpdir_path / "devices.yaml"
            firmware_repo = tmpdir_path / "kalico"
            firmware_repo.mkdir()
            subprocess.run(["git", "init"], cwd=firmware_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=firmware_repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=firmware_repo, check=True)
            (firmware_repo / "README.md").write_text("Kalico test repo", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=firmware_repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=firmware_repo, check=True, capture_output=True)
            discovery_path.write_text(__import__("json").dumps(result), encoding="utf-8")
            confirm_device(devices_path, discovery_path, "ebb", "btt_ebb36_gen2_can")

            plan = build_firmware(
                device_id="ebb",
                firmware_ref="current",
                devices_path=devices_path,
                discovery_path=discovery_path,
                profile_paths=[ROOT / "profiles"],
                klipper_path=None,
                kalico_path=str(firmware_repo),
                build_root=tmpdir_path / "builds",
                artifact_root=tmpdir_path / "artifacts",
                execute=False,
            )

        self.assertEqual(plan["action"], "build_firmware")
        self.assertFalse(plan["execute"])
        self.assertEqual(plan["build"]["status"], "planned")
        self.assertTrue(any("make" in command for command in plan["build"]["commands"]))

    def test_artifact_manifest_records_identity_and_hash(self):
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            artifact = Path(tmpdir) / "firmware.bin"
            artifact.write_bytes(b"firmware")
            generated_config = tmpdir_path / "config.ebb"
            resolved_config = tmpdir_path / ".config.resolved"
            generated_config.write_text("CONFIG_MACH_STM32G0B1=y\n", encoding="utf-8")
            resolved_config.write_text("CONFIG_MACH_STM32G0B1=y\nCONFIG_CANBUS=y\n", encoding="utf-8")

            result = {
                "action": "build_firmware",
                "device": {
                    "id": "ebb",
                    "mcu_section": "mcu ebb",
                    "transport": "can",
                    "can_interface": "can0",
                    "canbus_uuid": "5a66cbaa9878",
                    "detected_chip": "stm32g0b1xx",
                },
                "profile": {
                    "id": "btt_ebb36_gen2_can",
                    "name": "BigTreeTech EBB36 Gen2",
                },
                "firmware_source": {
                    "project": "kalico",
                    "selected_ref": "current",
                    "current_version": "v2026.06.00-5-g30c65e09",
                },
                "workspace": {
                    "build_config_name": "ebb",
                    "config": str(generated_config),
                },
                "build": {
                    "artifact": str(artifact),
                    "artifact_size": artifact.stat().st_size,
                    "artifact_sha256": file_sha256(artifact),
                    "source_head": "30c65e09de06db847d75f20336c14ab598fcda4a",
                    "started_at": "2026-06-20T19:06:43+00:00",
                    "completed_at": "2026-06-20T19:06:44+00:00",
                    "resolved_config": str(resolved_config),
                    "build_log": "builds/ebb/current/build.log",
                },
            }

            manifest = create_artifact_manifest(result)
            self.assertEqual(
                manifest["artifact"]["sha256"],
                "c3bf47ea1f4a4a605470313cacb3a44f4a461f68c6faeab07e737610cb5ac835",
            )
            self.assertEqual(manifest["device"]["canbus_uuid"], "5a66cbaa9878")
            self.assertEqual(manifest["profile"]["id"], "btt_ebb36_gen2_can")
            self.assertEqual(manifest["firmware_source"]["source_head"], "30c65e09de06db847d75f20336c14ab598fcda4a")
            self.assertEqual(manifest["build"]["build_config_name"], "ebb")
            self.assertEqual(manifest["build"]["generated_config_sha256"], file_sha256(generated_config))
            self.assertEqual(manifest["build"]["resolved_config_sha256"], file_sha256(resolved_config))

    def test_firmware_state_detects_matching_runtime_and_artifact(self):
        state = firmware_state(
            {"firmware_version": "v2026.06.00-5-g30c65e09"},
            {
                "status": "ok",
                "exists": True,
                "firmware_source": {
                    "current_version": "v2026.06.00-5-g30c65e09",
                    "source_head": "30c65e09de06db847d75f20336c14ab598fcda4a",
                },
            },
        )

        self.assertEqual(state["status"], "already_matching")
        self.assertTrue(state["already_matching"])
        self.assertFalse(state["needs_flash"])

    def test_validate_resolved_config_rejects_usb_fallback_for_can_profile(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".config.resolved"
            config_path.write_text(
                "\n".join(
                    [
                        "CONFIG_MACH_STM32G0B1=y",
                        "CONFIG_STM32_FLASH_START_2000=y",
                        "CONFIG_CLOCK_REF_FREQ=8000000",
                        "CONFIG_USB=y",
                        "CONFIG_USBSERIAL=y",
                        "CONFIG_CANBUS_FREQUENCY=1000000",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(RuntimeError):
                validate_resolved_config(
                    config_path,
                    {
                        "processor": "STM32G0B1",
                        "clock_reference": "8MHz crystal",
                        "bootloader_offset": "8KiB",
                        "communication": "canbus",
                        "can_rx_pin": "PB12",
                        "can_tx_pin": "PB13",
                        "can_bitrate": "1000000",
                    },
                )

    def test_validate_resolved_config_accepts_clock_menu_symbol_without_derived_frequency(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".config.resolved"
            config_path.write_text(
                "\n".join(
                    [
                        "CONFIG_MACH_STM32H723=y",
                        "CONFIG_STM32_FLASH_START_20000=y",
                        "CONFIG_STM32_CLOCK_REF_25M=y",
                        "CONFIG_STM32_USBCANBUS_PA11_PA12=y",
                        "CONFIG_USBCANBUS=y",
                        "CONFIG_STM32_CMENU_CANBUS_PD0_PD1=y",
                        "CONFIG_CANBUS=y",
                        "CONFIG_CAN_UUID_USE_CHIPID=y",
                        "CONFIG_CANBUS_FREQUENCY=1000000",
                    ]
                ),
                encoding="utf-8",
            )

            validate_resolved_config(
                config_path,
                {
                    "processor": "STM32H723",
                    "clock_reference": "25MHz crystal",
                    "bootloader_offset": "128KiB",
                    "communication": "usb_to_canbus_bridge",
                    "usb_pins": "PA11/PA12",
                    "can_rx_pin": "PD0",
                    "can_tx_pin": "PD1",
                    "can_bitrate": "1000000",
                },
            )

    def test_generate_h723_usb_can_bridge_enables_low_level_clock_choice(self):
        config = generate_klipper_dot_config(
            {
                "architecture": "stm32",
                "processor": "STM32H723",
                "clock_reference": "25MHz crystal",
                "bootloader_offset": "128KiB",
                "communication": "usb_to_canbus_bridge",
                "usb_pins": "PA11/PA12",
                "can_rx_pin": "PD0",
                "can_tx_pin": "PD1",
                "can_bitrate": "1000000",
            }
        )

        self.assertIn("CONFIG_LOW_LEVEL_OPTIONS=y", config)
        self.assertIn("CONFIG_STM32_CLOCK_REF_25M=y", config)
        self.assertIn("CONFIG_STM32_USBCANBUS_PA11_PA12=y", config)
        self.assertIn("CONFIG_STM32_CMENU_CANBUS_PD0_PD1=y", config)

    def test_ouroboros_uses_official_direct_usb_dfu_config_without_bootloader(self):
        profile = next(
            profile
            for profile in load_profiles([ROOT / "profiles"])
            if profile.id == "isik_ouroboros_usb"
        )

        klipper_config = generate_klipper_dot_config(profile.build)

        self.assertIn("CONFIG_MACH_STM32H723=y", klipper_config)
        self.assertIn("CONFIG_STM32_CLOCK_REF_25M=y", klipper_config)
        self.assertNotIn("CONFIG_STM32_FLASH_START_20000=y", klipper_config)
        self.assertIn("CONFIG_STM32_USB_PA11_PA12=y", klipper_config)
        self.assertIn("CONFIG_USBSERIAL=y", klipper_config)
        self.assertFalse(profile.bootloader)
        self.assertEqual(profile.initial_flash["method"], "klipper_make_flash_dfu")
        self.assertEqual(profile.update["method"], "klipper_make_flash_usb")

    def test_validate_resolved_config_accepts_can_profile(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".config.resolved"
            config_path.write_text(
                "\n".join(
                    [
                        "CONFIG_MACH_STM32G0B1=y",
                        "CONFIG_STM32_FLASH_START_2000=y",
                        "CONFIG_CLOCK_REF_FREQ=8000000",
                        "CONFIG_CANSERIAL=y",
                        "CONFIG_CANBUS=y",
                        "CONFIG_CAN_UUID_USE_CHIPID=y",
                        "CONFIG_STM32_MMENU_CANBUS_PB12_PB13=y",
                        "CONFIG_STM32_CANBUS_PB12_PB13=y",
                        "CONFIG_CANBUS_FREQUENCY=1000000",
                    ]
                ),
                encoding="utf-8",
            )

            validate_resolved_config(
                config_path,
                {
                    "processor": "STM32G0B1",
                    "clock_reference": "8MHz crystal",
                    "bootloader_offset": "8KiB",
                    "communication": "canbus",
                    "can_rx_pin": "PB12",
                    "can_tx_pin": "PB13",
                    "can_bitrate": "1000000",
                },
            )

    def test_flash_plan_uses_confirmed_can_device_and_artifact_manifest(self):
        profiles = load_profiles([ROOT / "profiles"])
        result = discover(
            DiscoverOptions(
                printer_cfg=str(ROOT / "tests" / "fixtures" / "printer.cfg"),
                serial_dir=str(ROOT / "tests" / "fixtures" / "serial-by-id"),
                can_query_output=str(ROOT / "tests" / "fixtures" / "can-query.txt"),
                katapult_path=str(ROOT / "tests" / "fixtures" / "katapult"),
                klipper_path=str(ROOT / "tests" / "fixtures" / "missing-klipper"),
                kalico_path=str(ROOT / "tests" / "fixtures" / "missing-kalico"),
                printer_objects_json=str(ROOT / "tests" / "fixtures" / "printer-objects.json"),
                devices_path=None,
            ),
            profiles=profiles,
        )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            discovery_path = tmpdir_path / "discovery.json"
            devices_path = tmpdir_path / "devices.yaml"
            artifact = tmpdir_path / "ebb.bin"
            artifact.write_bytes(b"firmware")
            manifest_path = tmpdir_path / "artifact.json"
            discovery_path.write_text(__import__("json").dumps(result), encoding="utf-8")
            confirm_device(devices_path, discovery_path, "ebb", "btt_ebb36_gen2_can")
            manifest_path.write_text(
                __import__("json").dumps(
                    {
                        "device": {
                            "id": "ebb",
                            "canbus_uuid": "123456789abc",
                        },
                        "profile": {
                            "id": "btt_ebb36_gen2_can",
                        },
                        "artifact": {
                            "path": str(artifact),
                        },
                    }
                ),
                encoding="utf-8",
            )

            plan = create_flash_plan(
                device_id="ebb",
                devices_path=devices_path,
                discovery_path=discovery_path,
                profile_paths=[ROOT / "profiles"],
                katapult_path=ROOT / "tests" / "fixtures" / "katapult",
                artifact_manifest=manifest_path,
            )

        self.assertEqual(plan["action"], "flash_plan")
        self.assertFalse(plan["execute"])
        self.assertEqual(plan["device"]["canbus_uuid"], "123456789abc")
        self.assertEqual(plan["preflight"]["artifact"]["status"], "ok")
        self.assertEqual(plan["preflight"]["manifest_checks"]["status"], "ok")
        self.assertTrue(any("-r -u 123456789abc" in command for command in plan["commands_preview"]))
        self.assertTrue(any("-f " in command and "-u 123456789abc" in command for command in plan["commands_preview"]))

    def test_flash_plan_flags_manifest_mismatch(self):
        profiles = load_profiles([ROOT / "profiles"])
        result = discover(
            DiscoverOptions(
                printer_cfg=str(ROOT / "tests" / "fixtures" / "printer.cfg"),
                serial_dir=str(ROOT / "tests" / "fixtures" / "serial-by-id"),
                can_query_output=str(ROOT / "tests" / "fixtures" / "can-query.txt"),
                katapult_path=str(ROOT / "tests" / "fixtures" / "katapult"),
                klipper_path=str(ROOT / "tests" / "fixtures" / "missing-klipper"),
                kalico_path=str(ROOT / "tests" / "fixtures" / "missing-kalico"),
                printer_objects_json=str(ROOT / "tests" / "fixtures" / "printer-objects.json"),
                devices_path=None,
            ),
            profiles=profiles,
        )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            discovery_path = tmpdir_path / "discovery.json"
            devices_path = tmpdir_path / "devices.yaml"
            artifact = tmpdir_path / "ebb.bin"
            artifact.write_bytes(b"firmware")
            manifest_path = tmpdir_path / "artifact.json"
            discovery_path.write_text(__import__("json").dumps(result), encoding="utf-8")
            confirm_device(devices_path, discovery_path, "ebb", "btt_ebb36_gen2_can")
            manifest_path.write_text(
                __import__("json").dumps(
                    {
                        "device": {
                            "id": "ebb",
                            "canbus_uuid": "wrong",
                        },
                        "profile": {
                            "id": "btt_ebb36_gen2_can",
                        },
                        "artifact": {
                            "path": str(artifact),
                            "sha256": "bad",
                        },
                    }
                ),
                encoding="utf-8",
            )

            plan = create_flash_plan(
                device_id="ebb",
                devices_path=devices_path,
                discovery_path=discovery_path,
                profile_paths=[ROOT / "profiles"],
                katapult_path=ROOT / "tests" / "fixtures" / "katapult",
                artifact_manifest=manifest_path,
            )

        self.assertEqual(plan["preflight"]["manifest_checks"]["status"], "failed")
        self.assertIn("canbus_uuid", plan["preflight"]["manifest_checks"]["failed"])
        self.assertIn("artifact_sha256", plan["preflight"]["manifest_checks"]["failed"])

    def test_flash_firmware_without_execute_is_plan_only(self):
        profiles = load_profiles([ROOT / "profiles"])
        result = discover(
            DiscoverOptions(
                printer_cfg=str(ROOT / "tests" / "fixtures" / "printer.cfg"),
                serial_dir=str(ROOT / "tests" / "fixtures" / "serial-by-id"),
                can_query_output=str(ROOT / "tests" / "fixtures" / "can-query.txt"),
                katapult_path=str(ROOT / "tests" / "fixtures" / "katapult"),
                klipper_path=str(ROOT / "tests" / "fixtures" / "missing-klipper"),
                kalico_path=str(ROOT / "tests" / "fixtures" / "missing-kalico"),
                printer_objects_json=str(ROOT / "tests" / "fixtures" / "printer-objects.json"),
                devices_path=None,
            ),
            profiles=profiles,
        )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            discovery_path = tmpdir_path / "discovery.json"
            devices_path = tmpdir_path / "devices.yaml"
            artifact = tmpdir_path / "ebb.bin"
            artifact.write_bytes(b"firmware")
            manifest_path = tmpdir_path / "artifact.json"
            discovery_path.write_text(__import__("json").dumps(result), encoding="utf-8")
            confirm_device(devices_path, discovery_path, "ebb", "btt_ebb36_gen2_can")
            manifest_path.write_text(
                __import__("json").dumps(
                    {
                        "device": {
                            "id": "ebb",
                            "canbus_uuid": "123456789abc",
                        },
                        "profile": {
                            "id": "btt_ebb36_gen2_can",
                        },
                        "artifact": {
                            "path": str(artifact),
                        },
                    }
                ),
                encoding="utf-8",
            )

            plan = flash_firmware(
                device_id="ebb",
                devices_path=devices_path,
                discovery_path=discovery_path,
                profile_paths=[ROOT / "profiles"],
                katapult_path=ROOT / "tests" / "fixtures" / "katapult",
                artifact_manifest=manifest_path,
                execute=False,
            )

        self.assertEqual(plan["action"], "flash_firmware")
        self.assertFalse(plan["execute"])
        self.assertEqual(plan["flash"]["status"], "planned")
        self.assertTrue(plan["flash"]["flash_log"].endswith("flash.log"))

    def test_flash_preflight_rejects_manifest_mismatch(self):
        with self.assertRaises(RuntimeError):
            validate_preflight(
                {
                    "preflight": {
                        "katapult": {"status": "ok"},
                        "artifact": {"status": "ok"},
                        "manifest_checks": {"status": "failed"},
                    }
                }
            )

    def test_query_application_parser_accepts_expected_application(self):
        require_query_application("Detected UUID: 123456789abc, Application: Katapult", "123456789abc", "katapult")
        require_query_application("Detected UUID: 123456789abc, Application: Klipper", "123456789abc", "klipper")
        require_query_application("[can0] Found canbus_uuid=123456789abc, Application: Kalico, Assigned: 04", "123456789abc", "klipper")
        with self.assertRaises(RuntimeError):
            require_query_application(
                "Detected UUID: 123456789abc, Application: Katapult\nDetected UUID: fedcba987654, Application: Klipper",
                "123456789abc", "klipper",
            )

    def test_query_application_parser_rejects_wrong_application(self):
        with self.assertRaises(RuntimeError):
            require_query_application("Detected UUID: 123456789abc, Application: Klipper", "123456789abc", "katapult")

    def test_verify_device_checks_runtime_against_manifest(self):
        profiles = load_profiles([ROOT / "profiles"])
        result = discover(
            DiscoverOptions(
                printer_cfg=str(ROOT / "tests" / "fixtures" / "printer.cfg"),
                serial_dir=str(ROOT / "tests" / "fixtures" / "serial-by-id"),
                can_query_output=str(ROOT / "tests" / "fixtures" / "can-query.txt"),
                katapult_path=str(ROOT / "tests" / "fixtures" / "katapult"),
                klipper_path=str(ROOT / "tests" / "fixtures" / "missing-klipper"),
                kalico_path=str(ROOT / "tests" / "fixtures" / "missing-kalico"),
                printer_objects_json=str(ROOT / "tests" / "fixtures" / "printer-objects.json"),
                devices_path=None,
            ),
            profiles=profiles,
        )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            discovery_path = tmpdir_path / "discovery.json"
            devices_path = tmpdir_path / "devices.yaml"
            manifest_path = tmpdir_path / "artifact.json"
            discovery_path.write_text(__import__("json").dumps(result), encoding="utf-8")
            confirm_device(devices_path, discovery_path, "ebb", "btt_ebb36_gen2_can")
            manifest_path.write_text(
                __import__("json").dumps(
                    {
                        "device": {
                            "id": "ebb",
                            "canbus_uuid": "123456789abc",
                            "detected_chip": "stm32g0b1xx",
                        },
                        "profile": {
                            "id": "btt_ebb36_gen2_can",
                        },
                        "artifact": {
                            "path": "firmware.bin",
                            "sha256": "abc",
                            "size": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )

            verification = verify_device(
                device_id="ebb",
                printer_cfg=str(ROOT / "tests" / "fixtures" / "printer.cfg"),
                serial_dir=str(ROOT / "tests" / "fixtures" / "serial-by-id"),
                profile_paths=[ROOT / "profiles"],
                can_interface="can0",
                katapult_path=str(ROOT / "tests" / "fixtures" / "katapult"),
                klipper_path=str(ROOT / "tests" / "fixtures" / "missing-klipper"),
                kalico_path=str(ROOT / "tests" / "fixtures" / "missing-kalico"),
                moonraker_url=None,
                devices_path=str(devices_path),
                artifact_manifest=manifest_path,
                printer_objects_json=str(ROOT / "tests" / "fixtures" / "printer-objects.json"),
                can_query_output=str(ROOT / "tests" / "fixtures" / "can-query.txt"),
            )

        self.assertEqual(verification["status"], "ok")
        self.assertEqual(verification["device"]["runtime_app"], "Klipper")
        self.assertEqual(verification["device"]["detected_chip"], "stm32g0b1xx")
        self.assertTrue(verification["checks"]["profile_matches_manifest"])

    def test_verify_device_accepts_kalico_runtime(self):
        checks = runtime_checks(
            {
                "runtime_app": "Kalico",
                "firmware_version": "v2026.06.00-5-g30c65e09",
                "detected_chip": "stm32g0b1xx",
                "canbus_uuid": "5a66cbaa9878",
                "confirmed_profile": "btt_ebb36_gen2_can",
            },
            {
                "device": {
                    "canbus_uuid": "5a66cbaa9878",
                    "detected_chip": "stm32g0b1xx",
                },
                "profile": {
                    "id": "btt_ebb36_gen2_can",
                },
            },
        )

        self.assertTrue(checks["runtime_app_supported"])

    def test_status_summary_and_printer_cfg_snippets(self):
        devices = [
            {"confirmation_status": "confirmed", "transport": "can", "recovery": {"active": False}},
            {"confirmation_status": "needs_confirmation", "transport": "dfu", "recovery": {"active": True}},
        ]

        self.assertEqual(
            status_summary(devices),
            {"total": 2, "ready": 1, "needs_confirmation": 1, "dfu": 1, "issues": 1},
        )
        self.assertEqual(
            printer_cfg_snippet(
                {"name": "ebb", "canbus_uuid": "5a66cbaa9878", "can_interface": "can0"}
            ),
            "[mcu ebb]\ncanbus_uuid: 5a66cbaa9878\ncanbus_interface: can0",
        )
        self.assertEqual(
            printer_cfg_snippet(
                {"name": "mcu", "serial": "/dev/serial/by-id/usb-Klipper_stm32h723xx-if00"}
            ),
            "[mcu]\nserial: /dev/serial/by-id/usb-Klipper_stm32h723xx-if00",
        )

    def test_artifact_history_resolves_last_good_to_build_ref(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ebb"
            build_dir = root / "v0.12.0"
            build_dir.mkdir(parents=True)
            firmware = build_dir / "firmware.bin"
            firmware.write_bytes(b"firmware")
            manifest = build_dir / "artifact.json"
            manifest.write_text(
                __import__("json").dumps(
                    {
                        "artifact": {"path": str(firmware), "size": firmware.stat().st_size},
                        "firmware_source": {"current_version": "v0.12.0"},
                        "build": {"completed_at": "2026-08-06T10:00:00+00:00"},
                    }
                ),
                encoding="utf-8",
            )
            (root / "last-good-artifact.json").write_text(
                __import__("json").dumps(
                    {
                        "artifact": {"path": str(firmware), "size": firmware.stat().st_size},
                        "manifest": str(manifest),
                        "flashed_at": "2026-08-06T10:05:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            history = artifact_history_for_device(device_id="ebb", build_root=tmpdir)

        self.assertEqual(history[0]["kind"], "last_good")
        self.assertEqual(history[0]["ref"], "v0.12.0")
        self.assertTrue(history[0]["exists"])
        self.assertEqual(history[1]["label"], "v0.12.0")


if __name__ == "__main__":
    unittest.main()
