from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import unittest

from mcu_update_manager.flash_firmware import flash_output_dir, mark_last_good_artifact
from mcu_update_manager.status import artifact_history_for_device


class ArtifactRestoreTest(unittest.TestCase):
    def test_successful_flash_keeps_independent_last_good_copy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "builds" / "ebb"
            build = root / "current"
            build.mkdir(parents=True)
            firmware = build / "firmware.bin"
            firmware.write_bytes(b"verified firmware")
            digest = hashlib.sha256(firmware.read_bytes()).hexdigest()
            manifest = build / "artifact.json"
            manifest.write_text(json.dumps({
                "device": {"id": "ebb", "canbus_uuid": "123456789abc"},
                "profile": {"id": "btt_ebb36_gen2_can"},
                "artifact": {"path": str(firmware), "sha256": digest},
            }), encoding="utf-8")
            mark_last_good_artifact(build, {
                "device": {"id": "ebb"},
                "preflight": {
                    "artifact": {"path": str(firmware), "sha256": digest, "size": firmware.stat().st_size},
                    "manifest_checks": {"manifest": str(manifest)},
                },
                "flash": {"completed_at": "2026-09-28T10:00:00Z"},
            })
            firmware.write_bytes(b"newer build")
            record = json.loads((root / "last-good-artifact.json").read_text())
            self.assertEqual(Path(record["artifact"]["path"]).read_bytes(), b"verified firmware")
            self.assertEqual(flash_output_dir(record["manifest"], "ebb"), root / "restore-last-good")
            self.assertEqual(artifact_history_for_device(device_id="ebb", build_root=str(root.parent))[0]["ref"], "last_good")


if __name__ == "__main__":
    unittest.main()
