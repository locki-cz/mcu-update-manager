from __future__ import annotations

from pathlib import Path
import re


DFU_ID = re.compile(r"^dfu_(\d+)_(\d+)_([0-9a-fA-F]{4})_([0-9a-fA-F]{4})$")


def select_dfu_port(device_id: str | None, vid_pid: str, sysfs: Path = Path("/sys/bus/usb/devices")) -> str:
    """Resolve the live USB topology path for dfu-util -p, never just a shared VID:PID."""
    selected = DFU_ID.fullmatch(device_id or "") if device_id else None
    if device_id and not selected:
        raise ValueError(f"Invalid DFU device ID: {device_id}")
    vendor, product = vid_pid.lower().split(":", 1)
    matches = []
    for entry in sysfs.iterdir():
        if not re.fullmatch(r"\d+-\d+(?:\.\d+)*", entry.name):
            continue
        try:
            if (entry / "idVendor").read_text().strip().lower() != vendor:
                continue
            if (entry / "idProduct").read_text().strip().lower() != product:
                continue
            bus = int((entry / "busnum").read_text().strip())
            address = int((entry / "devnum").read_text().strip())
        except (OSError, ValueError):
            continue
        if selected and (bus, address) != (int(selected[1]), int(selected[2])):
            continue
        if selected and (vendor, product) != (selected[3].lower(), selected[4].lower()):
            continue
        matches.append(entry.name)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one matching DFU device for {device_id or vid_pid}; found {len(matches)}. "
            "Rescan and select the exact device before flashing."
        )
    return matches[0]
