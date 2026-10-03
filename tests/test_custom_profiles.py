from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import unittest

from mcu_update_manager.custom_profiles import get_profile, profile_fields, save_custom_profile
from mcu_update_manager.dfu_flash import profile_catalog
from mcu_update_manager.flash_plan import check_manifest
from mcu_update_manager.profiles import load_profiles, load_simple_yaml


CATALOG = Path(__file__).resolve().parents[1] / "profiles"


class CustomProfilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.custom = Path(self.temp.name) / "profiles"
        self.paths = [CATALOG, self.custom]
        self.base = get_profile(load_profiles([CATALOG]), "btt_ebb36_gen2_can")

    def fields(self, name: str = "My EBB36 Gen2") -> dict:
        return {**profile_fields(self.base), "name": name}

    def test_copy_profile_is_independent_and_loadable(self) -> None:
        result = save_custom_profile(self.paths, self.custom, self.fields(), template_id=self.base.id)
        self.assertEqual(result["id"], "user_my_ebb36_gen2")
        self.assertEqual(result["fields"]["processor"], "STM32G0B1")
        saved = get_profile(load_profiles(self.paths), result["id"])
        self.assertEqual(saved.build["can_rx_pin"], "PB12")
        self.assertEqual(saved.bootloader["application_start_offset"], "8KiB")
        self.assertNotIn("update", load_simple_yaml(saved.path))
        self.assertEqual(self.base.name, "BigTreeTech EBB36 Gen2")
        item = next(item for item in profile_catalog(self.paths, self.custom) if item["id"] == result["id"])
        self.assertTrue(item["custom"])
        self.assertEqual(item["chips"], ["stm32g0b1xx"])

    def test_reject_duplicate_id_and_name(self) -> None:
        save_custom_profile(self.paths, self.custom, self.fields(), template_id=self.base.id)
        with self.assertRaisesRegex(ValueError, "ID already exists"):
            save_custom_profile(self.paths, self.custom, self.fields(), template_id=self.base.id)
        with self.assertRaisesRegex(ValueError, "name already exists"):
            save_custom_profile(self.paths, self.custom, self.fields("BIGTREETECH EBB36 GEN2"), template_id=self.base.id)

    def test_duplicate_id_in_catalog_is_not_silently_overridden(self) -> None:
        saved = save_custom_profile(self.paths, self.custom, self.fields(), template_id=self.base.id)
        duplicate = self.custom / "duplicate.yaml"
        duplicate.write_text((self.custom / f"{saved['id']}.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate profile ID"):
            load_profiles(self.paths)

    def test_catalog_profile_cannot_be_edited(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be edited"):
            save_custom_profile(self.paths, self.custom, self.fields(), profile_id=self.base.id)

    def test_edit_custom_profile_preserves_id(self) -> None:
        result = save_custom_profile(self.paths, self.custom, self.fields(), template_id=self.base.id)
        changed = self.fields("Renamed EBB")
        updated = save_custom_profile(self.paths, self.custom, changed, profile_id=result["id"])
        self.assertEqual(updated["id"], result["id"])
        self.assertEqual(updated["name"], "Renamed EBB")

    def test_old_artifact_is_rejected_after_profile_edit(self) -> None:
        result = save_custom_profile(self.paths, self.custom, self.fields(), template_id=self.base.id)
        digest = hashlib.sha256(Path(result["path"]).read_bytes()).hexdigest()
        save_custom_profile(self.paths, self.custom, self.fields("Renamed EBB"), profile_id=result["id"])
        manifest = {
            "device": {"id": "ebb", "canbus_uuid": "abc123"},
            "profile": {"id": result["id"], "digest": digest},
            "artifact": {"path": "/tmp/firmware.bin", "sha256": "abc"},
        }
        current = hashlib.sha256(Path(result["path"]).read_bytes()).hexdigest()
        checks = check_manifest(manifest, "ebb", result["id"], "abc123", None,
                                "/tmp/firmware.bin", {"sha256": "abc"}, current)
        self.assertEqual(checks["status"], "failed")
        self.assertIn("profile_digest", checks["failed"])

    def test_reject_mismatched_offsets(self) -> None:
        fields = self.fields()
        fields["application_start_offset"] = "16KiB"
        with self.assertRaisesRegex(ValueError, "must match"):
            save_custom_profile(self.paths, self.custom, fields, template_id=self.base.id)
        self.assertFalse(self.custom.exists())

    def test_reject_unsupported_fields(self) -> None:
        fields = self.fields()
        fields["command"] = "sh -c something"
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            save_custom_profile(self.paths, self.custom, fields, template_id=self.base.id)

    def test_copy_direct_usb_profile_without_katapult(self) -> None:
        original = get_profile(load_profiles([CATALOG]), "isik_ouroboros_usb")
        fields = {**profile_fields(original), "name": "My Ouroboros"}
        saved = save_custom_profile(self.paths, self.custom, fields, template_id=original.id)
        profile = get_profile(load_profiles(self.paths), saved["id"])
        self.assertFalse(profile.bootloader)
        self.assertEqual(profile.flash["method"], "klipper_make_flash_usb")

    def test_copy_usb_can_bridge_uses_can_transport(self) -> None:
        original = get_profile(load_profiles([CATALOG]), "esoterical_bigtreetech_manta_m8p_v20_usb_can_bridge")
        fields = {**profile_fields(original), "name": "My Manta bridge"}
        self.assertEqual(fields["transport"], "can")
        self.assertEqual(fields["processor"], "STM32H723")
        self.assertEqual(fields["can_rx_pin"], "PD0")
        saved = save_custom_profile(self.paths, self.custom, fields, template_id=original.id)
        self.assertEqual(saved["fields"]["transport"], "can")

    def test_create_profile_without_template(self) -> None:
        fields = self.fields("My new board")
        saved = save_custom_profile(self.paths, self.custom, fields)
        profile = get_profile(load_profiles(self.paths), saved["id"])
        self.assertEqual(profile.build["processor"], "STM32G0B1")
        self.assertEqual(profile.flash["method"], "can_katapult")
        self.assertEqual(profile.initial_flash["dfu_vid_pid"], "0483:df11")

    def test_completed_fallback_copy_removes_catalog_blocker(self) -> None:
        fallback = get_profile(load_profiles([CATALOG]), "custom_stm32h723_can")
        fields = {**profile_fields(fallback), "name": "My verified H723 board"}
        saved = save_custom_profile(self.paths, self.custom, fields, template_id=fallback.id)
        profile = get_profile(load_profiles(self.paths), saved["id"])
        self.assertNotIn("requires_exact_profile", profile.build)
        self.assertEqual(profile.build["can_bitrate"], "{{ can_bitrate }}")

    def test_reject_unsupported_can_pin_pair(self) -> None:
        fields = self.fields()
        fields["can_rx_pin"] = "PA0"
        with self.assertRaisesRegex(ValueError, "Unsupported STM32 CAN pin pair"):
            save_custom_profile(self.paths, self.custom, fields, template_id=self.base.id)

    def test_unicode_name_has_stable_id(self) -> None:
        result = save_custom_profile(self.paths, self.custom, self.fields("Moje \u010desk\u00e1 deska"), template_id=self.base.id)
        self.assertEqual(result["id"], "user_moje_ceska_deska")


if __name__ == "__main__":
    unittest.main()
