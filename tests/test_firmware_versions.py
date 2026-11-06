from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mcu_update_manager.firmware_source import firmware_version_options


class FirmwareVersionOptionsTest(unittest.TestCase):
    def test_includes_exact_commit_with_described_version(self):
        commit = 'a' * 40

        def git(_root, args):
            if args[0] == 'log':
                return SimpleNamespace(ok=True, stdout=commit + '\n')
            if args[0] == 'describe':
                return SimpleNamespace(ok=True, stdout='v2026.10.00-17-gaaaaaaaa\n')
            return SimpleNamespace(ok=True, stdout='v2026.10.00\n')

        with patch('mcu_update_manager.firmware_source.run_git', side_effect=git):
            options = firmware_version_options(Path('.'), 'v2026.11.00', 'b' * 12, 'main', 'origin/main')
        option = next(option for option in options if option['kind'] == 'commit')
        self.assertEqual(option['value'], commit)
        self.assertEqual(option['label'], 'v2026.10.00-17-gaaaaaaaa')


if __name__ == '__main__':
    unittest.main()
