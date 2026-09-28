from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mcu_update_manager.dfu_target import select_dfu_port


class DfuTargetTest(unittest.TestCase):
    def test_selects_exact_live_bus_and_device(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for port, address in (("1-1", 5), ("1-2.3", 6)):
                entry = root / port
                entry.mkdir()
                for name, value in (("idVendor", "0483"), ("idProduct", "df11"),
                                    ("busnum", "1"), ("devnum", str(address))):
                    (entry / name).write_text(value)
            self.assertEqual(select_dfu_port("dfu_001_006_0483_df11", "0483:df11", root), "1-2.3")
            with self.assertRaisesRegex(RuntimeError, "Rescan"):
                select_dfu_port("dfu_001_007_0483_df11", "0483:df11", root)
            with self.assertRaisesRegex(RuntimeError, "found 2"):
                select_dfu_port(None, "0483:df11", root)


if __name__ == "__main__":
    unittest.main()
