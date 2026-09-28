from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from mcu_update_manager.io_utils import atomic_write_json


class AtomicWriteJsonTests(unittest.TestCase):
    def test_writes_complete_json_and_removes_temporary_file(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            atomic_write_json(path, {"status": "running", "steps": [1, 2]})

            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "running")
            self.assertEqual(list(path.parent.glob(".state.json.*.tmp")), [])

    def test_preserves_existing_file_when_replace_fails(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text('{"status":"old"}', encoding="utf-8")

            with patch("mcu_update_manager.io_utils.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    atomic_write_json(path, {"status": "new"})

            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "old")
            self.assertEqual(list(path.parent.glob(".state.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
