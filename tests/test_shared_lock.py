import os
import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

from mcu_update_manager.shared_lock import aldis_operation_lock


class SharedLockContractTests(unittest.TestCase):
    def test_busy_aldis_lock_is_reported(self):
        fake = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=mock.Mock(side_effect=BlockingIOError()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aldis.lock"
            path.touch()
            with mock.patch.dict(sys.modules, {"fcntl": fake}):
                with self.assertRaisesRegex(RuntimeError, "Another firmware operation"):
                    with aldis_operation_lock(str(path)):
                        self.fail("lock should not be acquired")
            fake.flock.assert_called_once()


@unittest.skipUnless(os.name == "posix", "requires Unix flock")
class SharedLockTests(unittest.TestCase):
    def test_lock_excludes_other_operations_and_is_released(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "aldis.lock")
            with aldis_operation_lock(path):
                with self.assertRaisesRegex(RuntimeError, "Another firmware operation"):
                    with aldis_operation_lock(path):
                        self.fail("A second operation acquired the lock")
            with aldis_operation_lock(path):
                self.assertTrue(Path(path).is_file())

    def test_body_errors_are_not_reported_as_lock_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(OSError, "build failed"):
                with aldis_operation_lock(str(Path(directory) / "aldis.lock")):
                    raise OSError("build failed")
