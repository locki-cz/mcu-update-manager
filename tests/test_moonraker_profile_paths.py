from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mcu_update_manager.moonraker_component import MCUUpdateManagerComponent


CATALOG = Path(__file__).resolve().parents[1] / "profiles"


class ProfilePathsTest(unittest.IsolatedAsyncioTestCase):
    async def test_profile_detail_resolves_catalog_relative_to_checkout(self) -> None:
        with TemporaryDirectory() as temp:
            component = MCUUpdateManagerComponent.__new__(MCUUpdateManagerComponent)
            component.repo_path = CATALOG.parent
            component.profile_dirs = "profiles"
            component.custom_profile_dir = Path(temp) / "custom"

            class Request:
                @staticmethod
                def get_str(key: str) -> str:
                    if key != "profile_id":
                        raise AssertionError(key)
                    return "btt_ebb36_gen2_can"

            detail = await component._handle_get_profile(Request())
            self.assertEqual(detail["fields"]["processor"], "STM32G0B1")
            self.assertEqual(detail["fields"]["can_rx_pin"], "PB12")
            self.assertEqual(detail["fields"]["can_tx_pin"], "PB13")
            self.assertEqual(component._profile_paths()[0], str(CATALOG))


if __name__ == "__main__":
    unittest.main()
