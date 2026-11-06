from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
import threading
from unittest.mock import AsyncMock, Mock, patch

from mcu_update_manager.moonraker_component import MCUUpdateManagerComponent


class LegacyUpdateTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.component = MCUUpdateManagerComponent.__new__(MCUUpdateManagerComponent)
        self.component.repo_path = self.root
        self.component.cache_path = self.root / "operation.json"
        self.component.devices_path = "devices.yaml"
        self.component.katapult_path = "~/katapult"
        self.component.can_bitrate = "1000000"
        self.component.moonraker_url = "http://localhost:7125"
        self.component._profile_paths = Mock(return_value=[])
        self.component._ensure_not_printing = AsyncMock()
        self.target = {"id": "ebb", "identity": "abc", "profile": "board", "chip": "stm32"}
        self.component._validate_legacy_device = AsyncMock(return_value=self.target)
        self.operation = {"device_id": "ebb"}
        self.manifest = self.root / "artifact.json"
        self.manifest.touch()
        for name, marker in (("klippy/mcu.py", "mcu_kconfig"), ("klippy/msgproto.py", "get_kconfig"),
                             ("scripts/buildcommands.py", "kconfig"), ("Makefile", "savedefconfig")):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(marker, encoding="utf-8")

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_failed_build_never_flashes(self):
        self.component._run_json = AsyncMock(return_value={"build": {"status": "failed"}})
        with patch("mcu_update_manager.moonraker_component.write_operation"):
            result = await self.component._run_legacy_update(self.operation, ["build"])
        self.assertEqual(result["build"]["status"], "failed")
        self.assertEqual(self.component._run_json.await_count, 1)

    async def test_changed_identity_prevents_flash(self):
        build = {"build": {"status": "ok", "artifact_manifest": str(self.manifest)},
                 "firmware_source": {"current_version": "v1", "path": str(self.root)}}
        self.component._run_json = AsyncMock(return_value=build)
        self.component._validate_legacy_device = AsyncMock(side_effect=[self.target, {**self.target, "identity": "changed"}])
        with patch("mcu_update_manager.moonraker_component.write_operation"), patch(
            "mcu_update_manager.moonraker_component.urlopen", return_value=BytesIO(b'{"result":{"software_version":"v1"}}')
        ):
            with self.assertRaisesRegex(RuntimeError, "changed during the build"):
                await self.component._run_legacy_update(self.operation, ["build"])
        self.assertEqual(self.component._run_json.await_count, 1)

    async def test_matching_build_flashes_after_idle_and_identity_checks(self):
        build = {"build": {"status": "ok", "artifact_manifest": str(self.manifest)},
                 "firmware_source": {"current_version": "v1", "path": str(self.root)}}
        self.component._run_json = AsyncMock(side_effect=[build, {"flash": {"status": "ok"}}])
        with patch("mcu_update_manager.moonraker_component.write_operation"), patch(
            "mcu_update_manager.moonraker_component.urlopen", return_value=BytesIO(b'{"result":{"software_version":"v1"}}')
        ):
            result = await self.component._run_legacy_update(self.operation, ["build"])
        self.assertEqual(result["flash"]["status"], "ok")
        self.component._ensure_not_printing.assert_awaited_once()
        self.assertEqual(self.component._validate_legacy_device.await_count, 2)
        self.assertIn("flash-firmware", self.component._run_json.await_args_list[1].args[0])

    async def test_host_version_mismatch_prevents_flash(self):
        build = {"build": {"status": "ok", "artifact_manifest": str(self.manifest)},
                 "firmware_source": {"current_version": "v2"}}
        self.component._run_json = AsyncMock(return_value=build)
        with patch("mcu_update_manager.moonraker_component.write_operation"), patch(
            "mcu_update_manager.moonraker_component.urlopen", return_value=BytesIO(b'{"result":{"software_version":"v1"}}')
        ):
            with self.assertRaisesRegex(RuntimeError, "does not match the running host"):
                await self.component._run_legacy_update(self.operation, ["build"])
        self.assertEqual(self.component._run_json.await_count, 1)

    async def test_host_http_query_does_not_block_event_loop_thread(self):
        build = {"build": {"status": "ok", "artifact_manifest": str(self.manifest)},
                 "firmware_source": {"current_version": "v2"}}
        self.component._run_json = AsyncMock(return_value=build)
        event_loop_thread = threading.get_ident()
        request_threads = []

        def response(*args, **kwargs):
            request_threads.append(threading.get_ident())
            return BytesIO(b'{"result":{"software_version":"v1"}}')

        with patch("mcu_update_manager.moonraker_component.write_operation"), patch(
            "mcu_update_manager.moonraker_component.urlopen", side_effect=response
        ):
            with self.assertRaisesRegex(RuntimeError, "does not match the running host"):
                await self.component._run_legacy_update(self.operation, ["build"])
        self.assertEqual(len(request_threads), 1)
        self.assertNotEqual(request_threads[0], event_loop_thread)
        self.assertEqual(self.component._run_json.await_count, 1)

    async def test_host_without_embedded_config_support_does_not_flash(self):
        (self.root / "Makefile").write_text("old source", encoding="utf-8")
        build = {"build": {"status": "ok", "artifact_manifest": str(self.manifest)},
                 "firmware_source": {"current_version": "v1", "path": str(self.root)}}
        self.component._run_json = AsyncMock(return_value=build)
        with patch("mcu_update_manager.moonraker_component.write_operation"), patch(
            "mcu_update_manager.moonraker_component.urlopen", return_value=BytesIO(b'{"result":{"software_version":"v1"}}')
        ):
            with self.assertRaisesRegex(RuntimeError, "does not provide embedded MCU configuration"):
                await self.component._run_legacy_update(self.operation, ["build"])
        self.assertEqual(self.component._run_json.await_count, 1)

    async def test_live_can_bitrate_mismatch_stops_before_build(self):
        self.component._discover_command = Mock(return_value=["discover"])
        self.component._run_json = AsyncMock(return_value={"devices": [{
            "id": "ebb", "transport": "can", "canbus_uuid": "abc", "can_interface": "can0",
            "detected_chip": "stm32g0b1xx", "firmware_version": "old", "mcu_kconfig": None,
        }]})
        saved = {"devices": [{"id": "ebb", "confirmed_by_user": True, "confirmed_profile": "board",
                              "canbus_uuid": "abc", "can_interface": "can0"}]}
        profile = SimpleNamespace(id="board", build={"architecture": "stm32", "processor": "STM32G0B1",
                                                       "communication": "canbus", "bootloader_offset": "8KiB"},
                                  match={"transports": ["can"], "chips": ["stm32g0b1xx"]},
                                  flash={"method": "can_katapult"})
        with patch("mcu_update_manager.moonraker_component.load_devices", return_value=saved), patch(
            "mcu_update_manager.moonraker_component.load_profiles", return_value=[]
        ), patch("mcu_update_manager.moonraker_component.get_profile", return_value=profile), patch(
            "mcu_update_manager.moonraker_component.first_available_source", return_value={"path": str(self.root)}
        ), patch("mcu_update_manager.moonraker_component.subprocess.run", return_value=SimpleNamespace(
            returncode=0, stdout="can state ERROR-ACTIVE bitrate 500000"
        )):
            with self.assertRaisesRegex(RuntimeError, "CAN bitrate"):
                await MCUUpdateManagerComponent._validate_legacy_device(self.component, "ebb")
