from pathlib import Path
import tempfile
import unittest

from mcu_update_manager.operation_cache import (
    finish_operation,
    is_active,
    mark_interrupted,
    new_operation,
    read_operation,
    write_operation,
)


class OperationCacheTests(unittest.TestCase):
    def test_operation_cache_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cache" / "operation.json"
            operation = new_operation("flash", "ebb")
            operation["status"] = "running"
            write_operation(path, operation)

            loaded = read_operation(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["job_id"], operation["job_id"])
            self.assertEqual(loaded["device_id"], "ebb")
            self.assertTrue(is_active(loaded))

            finished = finish_operation(path, loaded, status="ok", result={"status": "ok"})
            self.assertFalse(is_active(finished))
            self.assertEqual(read_operation(path)["result"], {"status": "ok"})

    def test_running_operation_is_marked_interrupted_after_service_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "operation.json"
            operation = new_operation("build", "mcu")
            operation["status"] = "running"
            write_operation(path, operation)

            interrupted = mark_interrupted(path)

            self.assertIsNotNone(interrupted)
            self.assertEqual(interrupted["status"], "interrupted")
            self.assertTrue(interrupted["completed_at"])
            self.assertIn("Moonraker restarted", interrupted["error"])
