from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mcu_update_manager.custom_profiles import profile_fields, save_custom_profile
from mcu_update_manager.dfu_flash import generate_katapult_dot_config, profile_catalog
from mcu_update_manager.hardware_options import processor_options
from mcu_update_manager.prepare_build import generate_klipper_dot_config
from mcu_update_manager.profiles import automatic_build_ready, load_profiles
from mcu_update_manager.status import device_actions


CATALOG = Path(__file__).resolve().parents[1] / "profiles"


class ProfileCatalogAuditTest(unittest.TestCase):
    def test_every_ready_catalog_profile_has_supported_menu_and_copyable_template(self) -> None:
        profiles = load_profiles([CATALOG])
        options = processor_options()
        self.assertGreaterEqual(len(profiles), 181)
        ready = 0
        with TemporaryDirectory() as temp:
            custom = Path(temp)
            for profile in profiles:
                with self.subTest(profile=profile.id):
                    build_ready = automatic_build_ready(profile.build)
                    boot_ready = automatic_build_ready(profile.bootloader)
                    if build_ready:
                        ready += 1
                        fields = profile_fields(profile)
                        processor = options[fields["processor"]]
                        self.assertEqual(fields["architecture"], processor["architecture"])
                        self.assertIn(fields["chip"], processor["chips"])
                        self.assertIn(fields["bootloader_offset"], processor["offsets"])
                        self.assertIn(fields["communication"], processor["communications"])
                        if fields["communication"] != "usb":
                            pair = f"{fields['can_rx_pin']}/{fields['can_tx_pin']}"
                            choices = processor["bridge_can"] if fields["communication"] == "usb_to_canbus_bridge" else processor["can"]
                            self.assertIn(pair, choices)
                        config = generate_klipper_dot_config(profile.build)
                        self.assertIn("CONFIG_MACH_", config)
                        fields["name"] = f"Audit {profile.id}"
                        save_custom_profile([CATALOG, custom], custom, fields, template_id=profile.id)
                    if boot_ready:
                        config = generate_katapult_dot_config(profile.bootloader)
                        self.assertNotIn("CONFIG_FLASH_APPLICATION_ADDRESS=", config)
                        if profile.bootloader.get("architecture") == "stm32":
                            self.assertIn("CONFIG_STM32_APP_START_", config)
        self.assertEqual(ready, 46)

    def test_catalog_exposes_incomplete_templates_without_offering_dfu_for_sdcard(self) -> None:
        with TemporaryDirectory() as temp:
            catalog = profile_catalog([CATALOG], temp)
        self.assertEqual(len(catalog), 181)
        self.assertEqual(sum(bool(item["supports_klipper"]) for item in catalog), 46)
        self.assertEqual(sum(bool(item["custom_template"]) for item in catalog), 55)
        sdcard = next(item for item in catalog if item["id"] == "mellow_fly_super8_can")
        self.assertFalse(sdcard["supports_dfu"])
        self.assertEqual(sdcard["targets"], [])
        for item in catalog:
            with self.subTest(profile=item["id"]):
                if not item["supports_katapult"]:
                    self.assertNotIn("katapult", {target["kind"] for target in item["targets"]})

    def test_esoterical_sht36_and_sht42_match_published_can_menu(self) -> None:
        profiles = {profile.id: profile for profile in load_profiles([CATALOG])}
        for name in ("mellow_fly_sht36_can", "mellow_fly_sht42_can"):
            with self.subTest(profile=name):
                profile = profiles[name]
                for settings in (profile.build, profile.bootloader):
                    self.assertEqual((settings["can_rx_pin"], settings["can_tx_pin"]), ("PB8", "PB9"))
                    self.assertEqual(settings["clock_reference"], "8MHz crystal")
                self.assertIn("CONFIG_STM32_MMENU_CANBUS_PB8_PB9=y", generate_klipper_dot_config(profile.build))
                self.assertIn("CONFIG_STM32_MMENU_CANBUS_PB8_PB9=y", generate_katapult_dot_config(profile.bootloader))

    def test_esoterical_ebb_gen2_usb_menu(self) -> None:
        profiles = {profile.id: profile for profile in load_profiles([CATALOG])}
        for name in ("btt_ebb36_gen2_usb", "btt_ebb42_gen2_usb"):
            with self.subTest(profile=name):
                profile = profiles[name]
                self.assertEqual(profile.build["usb_pins"], "PA11/PA12")
                self.assertEqual(profile.bootloader["usb_pins"], "PA11/PA12")
                self.assertEqual(profile.bootloader["status_led_pin"], "PA2")
                self.assertIn('CONFIG_STATUS_LED_PIN="PA2"', generate_katapult_dot_config(profile.bootloader))

    def test_unverified_can_profile_does_not_offer_flash(self) -> None:
        profiles = {profile.id: profile for profile in load_profiles([CATALOG])}
        device = {"id": "erb", "transport": "can", "canbus_uuid": "123456789abc"}
        confirmed = {"confirmed_profile": "mellow_fly_sht36_v2_gd32f103_can"}
        self.assertFalse(device_actions(device, confirmed, profiles["mellow_fly_sht36_v2_gd32f103_can"])["can_flash"])
        self.assertTrue(device_actions(device, confirmed, profiles["mellow_fly_sht36_can"])["can_flash"])

    def test_catalog_exposes_settings_and_reason_for_unverified_profile(self) -> None:
        catalog = {item["id"]: item for item in profile_catalog([CATALOG])}
        incomplete = next(item for item in catalog.values() if item["verification"]["reasons"]
                          and any("source images" in reason for reason in item["verification"]["reasons"]))
        self.assertFalse(incomplete["verification"]["ready"])
        self.assertFalse(incomplete["supports_klipper"])
        self.assertIsInstance(incomplete["settings"]["klipper"], dict)
        self.assertTrue(incomplete["settings"]["sources"])

        ready = catalog["mellow_fly_sht36_can"]
        self.assertTrue(ready["verification"]["ready"])
        self.assertEqual(ready["settings"]["klipper"]["can_rx_pin"], "PB8")
        self.assertEqual(ready["settings"]["katapult"]["can_tx_pin"], "PB9")
        self.assertEqual(ready["settings"]["update_method"], "can_katapult")


if __name__ == "__main__":
    unittest.main()
