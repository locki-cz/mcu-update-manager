from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from mcu_update_manager.firmware_source import switch_firmware_ref
from mcu_update_manager.build_firmware import prepare_selected_source


class FirmwareSourceTest(unittest.TestCase):
    def test_building_old_ref_uses_detached_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            init_repo(repo)
            old_head = git(repo, "rev-parse", "HEAD")
            (repo / "file.txt").write_text("second\n", encoding="utf-8")
            git(repo, "add", "file.txt")
            git(repo, "commit", "-m", "second")
            current_head = git(repo, "rev-parse", "HEAD")
            worktree = Path(temp_dir) / "old-source"
            prepare_selected_source(repo, old_head, worktree, Path(temp_dir) / "build.log")
            self.assertEqual(git(repo, "rev-parse", "HEAD"), current_head)
            self.assertEqual((worktree / "file.txt").read_text(), "first\n")
            git(repo, "worktree", "remove", "--force", str(worktree))

    def test_switch_firmware_ref_checks_out_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = init_repo(Path(temp_dir))
            first_head = git(repo, "rev-parse", "HEAD")
            git(repo, "tag", "v1.0.0")
            (repo / "file.txt").write_text("second\n", encoding="utf-8")
            git(repo, "add", "file.txt")
            git(repo, "commit", "-m", "second")

            result = switch_firmware_ref(
                firmware_ref="v1.0.0",
                klipper_path=str(repo),
                kalico_path=None,
                execute=True,
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(git(repo, "rev-parse", "HEAD"), first_head)

    def test_switch_firmware_ref_blocks_dirty_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = init_repo(Path(temp_dir))
            git(repo, "tag", "v1.0.0")
            (repo / "file.txt").write_text("dirty\n", encoding="utf-8")

            result = switch_firmware_ref(
                firmware_ref="v1.0.0",
                klipper_path=str(repo),
                kalico_path=None,
                execute=True,
            )

            self.assertEqual(result["status"], "blocked")

    def test_switch_firmware_ref_preserves_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = init_repo(Path(temp_dir))
            old_head = git(repo, "rev-parse", "HEAD")
            (repo / "file.txt").write_text("second\n", encoding="utf-8")
            git(repo, "add", "file.txt")
            git(repo, "commit", "-m", "second")
            (repo / "local-custom.cfg").write_text("custom\n", encoding="utf-8")
            result = switch_firmware_ref(
                firmware_ref=old_head, klipper_path=str(repo), kalico_path=None, execute=True,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual((repo / "local-custom.cfg").read_text(), "custom\n")


def init_repo(path: Path) -> Path:
    git(path, "init")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "MCU Update Manager Test")
    (path / "file.txt").write_text("first\n", encoding="utf-8")
    git(path, "add", "file.txt")
    git(path, "commit", "-m", "first")
    return path


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(path),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


if __name__ == "__main__":
    unittest.main()
