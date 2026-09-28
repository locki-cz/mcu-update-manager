from pathlib import Path

import json

from mcu_update_manager.cartographer import create_cartographer_flash_plan, inspect_cartographer_device
from mcu_update_manager.devices import confirm_device


def test_cartographer_v4_can_1m_selects_prebuilt_firmware(tmp_path: Path) -> None:
    firmware_root = tmp_path / "cartographer_firmware"
    firmware_root.mkdir()
    (firmware_root / "firmware_list.csv").write_text(
        "\n".join(
            [
                "filepath,filename,firmware_type,protocol,process,probe_version,firmware_version,can_speed,is_lite,min_plugin_version",
                "v4/firmware/6.2.0/CartographerV4_6.2.0_CAN_1M_full_8kib_offset.bin,CartographerV4_6.2.0_CAN_1M_full_8kib_offset.bin,Cartographer,CAN,Update,v4,6.2.0,1000000,No,1.6.0",
                "v4/firmware/6.2.0/CartographerV4_6.2.0_CAN_1M_lite_8kib_offset.bin,CartographerV4_6.2.0_CAN_1M_lite_8kib_offset.bin,Cartographer,CAN,Update,v4,6.2.0,1000000,Yes,1.6.0",
                "v4/firmware/6.2.0/CartographerV4_6.2.0_CAN_500K_full_8kib_offset.bin,CartographerV4_6.2.0_CAN_500K_full_8kib_offset.bin,Cartographer,CAN,Update,v4,6.2.0,500000,No,1.6.0",
            ]
        ),
        encoding="utf-8",
    )
    device = {
        "id": "cartographer",
        "name": "cartographer",
        "transport": "can",
        "detected_chip": "stm32g431xx",
        "firmware_version": "CARTOGRAPHER v4 6.2.0",
        "canbus_uuid": "b5b1f95af16c",
    }

    result = inspect_cartographer_device(
        device,
        cartographer_firmware_path=str(firmware_root),
        can_bitrate="1000000",
    )

    assert result is not None
    assert result["status"] == "ok"
    assert result["probe_version"] == "v4"
    assert result["protocol"] == "CAN"
    assert result["can_speed"] == "1000000"
    assert result["current_version"] == "6.2.0"
    assert result["selected"]["firmware"]["filename"] == "CartographerV4_6.2.0_CAN_1M_full_8kib_offset.bin"
    assert "lite" in result["available_versions"][0]["firmware"]


def test_cartographer_usb_flash_plan_uses_usb_bootloader_flow(tmp_path: Path) -> None:
    firmware_root = tmp_path / "cartographer_firmware"
    firmware_file = firmware_root / "firmware" / "v4" / "firmware" / "6.2.0" / "CartographerV4_6.2.0_USB_full_8kib_offset.bin"
    firmware_file.parent.mkdir(parents=True)
    firmware_file.write_bytes(b"firmware")
    (firmware_root / "firmware_list.csv").write_text(
        "\n".join(
            [
                "filepath,filename,firmware_type,protocol,process,probe_version,firmware_version,can_speed,is_lite,min_plugin_version",
                "v4/firmware/6.2.0/CartographerV4_6.2.0_USB_full_8kib_offset.bin,CartographerV4_6.2.0_USB_full_8kib_offset.bin,Cartographer,USB,Update,v4,6.2.0,,No,1.6.0",
            ]
        ),
        encoding="utf-8",
    )
    klipper = tmp_path / "klipper"
    (klipper / "scripts").mkdir(parents=True)
    (klipper / "lib" / "katapult").mkdir(parents=True)
    (klipper / "lib" / "katapult" / "flashtool.py").write_text("", encoding="utf-8")
    discovery_path = tmp_path / "discovery.json"
    devices_path = tmp_path / "devices.yaml"
    discovery_path.write_text(
        json.dumps(
            {
                "devices": [
                    {
                        "id": "cartographer_usb",
                        "name": "cartographer",
                        "transport": "usb",
                        "serial": "/dev/serial/by-id/usb-Cartographer_stm32g431xx_test",
                        "detected_chip": "stm32g431xx",
                        "firmware_version": "Cartographer v4 6.1.0",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    confirm_device(devices_path, discovery_path, "cartographer_usb", "cartographer_usb")

    plan = create_cartographer_flash_plan(
        device_id="cartographer_usb",
        discovery_path=discovery_path,
        devices_path=devices_path,
        cartographer_firmware_path=str(firmware_root),
        katapult_path=None,
        klipper_path=str(klipper),
        can_bitrate="1000000",
        firmware_version="6.2.0",
        flavour="full",
    )

    command = plan["steps"][1]["command"]
    assert command[:2] == ["bash", "-lc"]
    assert "enter_bootloader" in command[2]
    assert "CartographerV4_6.2.0_USB_full_8kib_offset.bin" in command[2]


def test_cartographer_can_to_usb_plan_flashes_usb_firmware_over_can(tmp_path: Path) -> None:
    firmware_root = tmp_path / "cartographer_firmware"
    firmware_file = firmware_root / "firmware" / "v4" / "firmware" / "6.2.0" / "CartographerV4_6.2.0_USB_full_8kib_offset.bin"
    firmware_file.parent.mkdir(parents=True)
    firmware_file.write_bytes(b"firmware")
    (firmware_root / "firmware_list.csv").write_text(
        "\n".join(
            [
                "filepath,filename,firmware_type,protocol,process,probe_version,firmware_version,can_speed,is_lite,min_plugin_version",
                "v4/firmware/6.2.0/CartographerV4_6.2.0_CAN_1M_full_8kib_offset.bin,CartographerV4_6.2.0_CAN_1M_full_8kib_offset.bin,Cartographer,CAN,Update,v4,6.2.0,1000000,No,1.6.0",
                "v4/firmware/6.2.0/CartographerV4_6.2.0_USB_full_8kib_offset.bin,CartographerV4_6.2.0_USB_full_8kib_offset.bin,Cartographer,USB,Update,v4,6.2.0,,No,1.6.0",
            ]
        ),
        encoding="utf-8",
    )
    katapult = tmp_path / "katapult"
    (katapult / "scripts").mkdir(parents=True)
    (katapult / "scripts" / "flashtool.py").write_text("", encoding="utf-8")
    discovery_path = tmp_path / "discovery.json"
    devices_path = tmp_path / "devices.yaml"
    discovery_path.write_text(
        json.dumps(
            {
                "devices": [
                    {
                        "id": "cartographer",
                        "name": "cartographer",
                        "transport": "can",
                        "can_interface": "can0",
                        "canbus_uuid": "b5b1f95af16c",
                        "detected_chip": "stm32g431xx",
                        "firmware_version": "Cartographer v4 6.2.0",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    confirm_device(devices_path, discovery_path, "cartographer", "cartographer_can")

    plan = create_cartographer_flash_plan(
        device_id="cartographer",
        discovery_path=discovery_path,
        devices_path=devices_path,
        cartographer_firmware_path=str(firmware_root),
        katapult_path=str(katapult),
        klipper_path=None,
        can_bitrate="1000000",
        firmware_version="6.2.0",
        flavour="full",
        target_protocol="USB",
    )

    command = plan["steps"][1]["command"]
    assert command[1:] == [
        str(katapult / "scripts" / "flashtool.py"),
        "-i",
        "can0",
        "-f",
        str(firmware_file),
        "-u",
        "b5b1f95af16c",
    ]
    assert plan["vendor_firmware"]["target_protocol"] == "USB"
