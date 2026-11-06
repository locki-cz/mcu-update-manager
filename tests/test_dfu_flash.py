import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from mcu_update_manager.dfu_flash import benign_stm32_leave_error as benign_dfu_leave_error, firmware_targets
from mcu_update_manager.flash_firmware import benign_stm32_leave_error as benign_update_leave_error, validate_preflight, verify_usb_runtime
from mcu_update_manager.flash_plan import direct_usb_flash_steps
from mcu_update_manager.profiles import HardwareProfile


class DfuFlashTest(unittest.TestCase):
    def test_incomplete_profile_has_no_automatic_dfu_target(self):
        profile = HardwareProfile(
            id="placeholder", name="Unverified board", family="mainboard",
            build={"communication": "usb", "requires_esoterical_image_extraction": True},
            initial_flash={"method": "dfu_util", "dfu_vid_pid": "0483:df11"},
        )
        self.assertEqual(firmware_targets(profile), [])

    def test_stm32_disconnect_after_successful_download_is_not_a_flash_failure(self):
        output = "File downloaded successfully\nSubmitting leave request...\ndfu-util: Error during download get_status\n"

        self.assertTrue(
            benign_dfu_leave_error(
                ["make", "flash", "FLASH_DEVICE=0483:df11"],
                output,
            )
        )
        self.assertTrue(
            benign_update_leave_error(
                ["make", "flash", "FLASH_DEVICE=/dev/serial/by-id/usb-Klipper_TEST"],
                output,
            )
        )
        self.assertFalse(
            benign_dfu_leave_error(
                ["make", "flash", "FLASH_DEVICE=0483:df11"],
                "dfu-util: Error during download get_status",
            )
        )

    def test_flash_usb_leave_error_requires_complete_ordered_download(self):
        command = ["python3", "/home/pi/klipper/scripts/flash_usb.py", "-t", "stm32h723", "-d", "/dev/test", "-s", "0x8000000", "firmware.bin"]
        output = "File downloaded successfully\nSubmitting leave request...\ndfu-util: Error during download get_status\n"
        self.assertTrue(benign_update_leave_error(command, output))
        self.assertFalse(benign_update_leave_error(command, "dfu-util: Error during download get_status"))
        self.assertFalse(benign_update_leave_error(command, "Submitting leave request\nFile downloaded successfully\ndfu-util: Error during download get_status"))
        self.assertFalse(benign_update_leave_error(["python3", "/tmp/other.py", *command[2:]], output))

    def test_leave_error_requires_matching_running_mcu_version_and_chip(self):
        result = {"device": {"mcu_section": "mcu ouroboros", "detected_chip": "stm32h723xx"},
                  "flash": {"steps": [{"id": "verify_runtime", "status": "pending"}]}}
        manifest = {"firmware_source": {"current_version": "v1"}}
        payload = b'{"result":{"status":{"mcu ouroboros":{"mcu_version":"v1","mcu_constants":{"MCU":"stm32h723xx"}}}}}'
        with patch("mcu_update_manager.flash_firmware.urlopen", return_value=BytesIO(payload)), patch(
            "mcu_update_manager.flash_firmware.write_result"
        ), patch("mcu_update_manager.flash_firmware.append_log"):
            verify_usb_runtime(result, Path("result.json"), Path("flash.log"), "http://localhost:7125", manifest)
        self.assertEqual(result["flash"]["steps"][0]["status"], "done")

    def test_leave_error_does_not_accept_wrong_running_version(self):
        result = {"device": {"mcu_section": "mcu ouroboros", "detected_chip": "stm32h723xx"},
                  "flash": {"steps": [{"id": "verify_runtime", "status": "pending"}]}}
        payload = b'{"result":{"status":{"mcu ouroboros":{"mcu_version":"old","mcu_constants":{"MCU":"stm32h723xx"}}}}}'
        with patch("mcu_update_manager.flash_firmware.urlopen", return_value=BytesIO(payload)), patch(
            "mcu_update_manager.flash_firmware.write_result"
        ), patch("mcu_update_manager.flash_firmware.time.sleep"), patch(
            "mcu_update_manager.flash_firmware.time.monotonic", side_effect=[0, 0, 50]
        ):
            with self.assertRaisesRegex(RuntimeError, "could not be verified"):
                verify_usb_runtime(result, Path("result.json"), Path("flash.log"), "http://localhost:7125", {"firmware_source": {"current_version": "v1"}})
        self.assertEqual(result["flash"]["steps"][0]["status"], "failed")

    def test_profile_with_bootloader_only_offers_katapult_for_initial_dfu_flash(self):
        profile = HardwareProfile(
            id="board_with_katapult",
            name="Board with Katapult",
            family="toolhead",
            build={
                "architecture": "stm32",
                "processor": "STM32G0B1",
                "bootloader_offset": "8KiB",
                "communication": "usb",
                "can_rx_pin": "PB0",
                "can_tx_pin": "PB1",
            },
            bootloader={
                "architecture": "stm32",
                "processor": "STM32G0B1",
                "application_start_offset": "8KiB",
                "communication": "usb",
                "can_rx_pin": "PB0",
                "can_tx_pin": "PB1",
            },
            initial_flash={"method": "dfu_util", "dfu_vid_pid": "0483:df11"},
        )

        targets = firmware_targets(profile)

        self.assertEqual(
            targets,
            [
                {"kind": "katapult", "communication": "usb", "label": "Katapult (usb)"},
                {"kind": "katapult", "communication": "canbus", "label": "Katapult (CAN)"},
            ],
        )
        self.assertNotIn("klipper", {target["kind"] for target in targets})

    def test_profile_without_bootloader_can_offer_direct_klipper_dfu_flash(self):
        profile = HardwareProfile(
            id="board_without_katapult",
            name="Board without Katapult",
            family="mainboard",
            build={
                "architecture": "stm32", "processor": "STM32H723",
                "bootloader_offset": "No bootloader", "communication": "usb",
                "can_rx_pin": "PD0", "can_tx_pin": "PD1",
            },
            initial_flash={"method": "dfu_util", "dfu_vid_pid": "0483:df11"},
        )

        targets = firmware_targets(profile)

        self.assertEqual(
            targets,
            [
                {"kind": "klipper", "communication": "usb", "label": "Klipper (usb)"},
                {
                    "kind": "klipper",
                    "communication": "usb_to_canbus_bridge",
                    "label": "Klipper USB-CAN bridge",
                },
            ],
        )

    def test_sdcard_profile_has_no_automatic_dfu_target(self):
        profile = HardwareProfile(
            id="sdcard", name="SD-card board", family="mainboard",
            build={"architecture": "stm32", "processor": "STM32F407",
                   "bootloader_offset": "32KiB", "communication": "canbus"},
            bootloader={"architecture": "stm32", "processor": "STM32F407",
                        "application_start_offset": "32KiB", "communication": "usb"},
            initial_flash={"method": "sdcard"},
        )
        self.assertEqual(firmware_targets(profile), [])

    def test_direct_usb_update_uses_stable_serial_without_katapult(self):
        manifest = {
            "firmware_source": {"path": "/home/pi/klipper"},
            "build": {"generated_config": "/tmp/ouroboros.config"},
            "artifact": {"path": "/tmp/ouroboros.bin"},
        }
        serial = "/dev/serial/by-id/usb-Klipper_stm32h723xx_TEST-if00"

        steps = direct_usb_flash_steps(
            manifest, serial,
            {"processor": "STM32H723", "bootloader_offset": "No bootloader"},
            "/tmp/ouroboros.bin",
        )

        self.assertEqual([step["id"] for step in steps], ["stop_klipper", "flash_firmware", "verify_usb", "start_klipper"])
        self.assertIn(f"-d {serial}", steps[1]["command"])
        self.assertIn("-s 0x8000000 /tmp/ouroboros.bin", steps[1]["command"])
        self.assertNotIn("katapult", " ".join(step["command"] for step in steps).lower())

        validate_preflight(
            {
                "profile": {"flash_method": "klipper_make_flash_usb"},
                "preflight": {
                    "katapult": {"status": "not_required"},
                    "artifact": {"status": "ok"},
                    "manifest_checks": {"status": "ok"},
                },
            }
        )


if __name__ == "__main__":
    unittest.main()
