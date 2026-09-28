from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
import re


USB_HARDWARE = [
    "AFC-Lite for Boxturtle",
    "BigTreeTech EBB36 Gen2",
    "BigTreeTech EBB42 Gen2",
    "BigTreeTech Eddy",
    "BigTreeTech Kraken",
    "BigTreeTech MMB",
    "BigTreeTech Manta E3EZ",
    "BigTreeTech Manta M5P V1.0",
    "BigTreeTech Manta M8P V1.1",
    "BigTreeTech Manta M8P V2.0",
    "BigTreeTech Octopus",
    "BigTreeTech Octopus Max EZ",
    "BigTreeTech Octopus X7 (Troodon)",
    "BigTreeTech SB2209-USB",
    "BigTreeTech SKR Pico",
    "BigTreeTech SKR-3",
    "BigTreeTech SKR-3 EZ",
    "BigTreeTech SKRat v1.0",
    "DragonDinghy",
    "Fysetc H36",
    "Fysetc H36 V2",
    "Fysetc PITB V1.0",
    "Fysetc PITB V2.0",
    "Fysetc SB Combo V2",
    "Fysetc Spider V1.0",
    "Fysetc Spider v2.2",
    "Fysetc Spider v2.3",
    "Fysetc Spider v3.0",
    "Fysetc Spider v3.0 H7",
    "LDO Leviathan v1.2",
    "LDO Leviathan v1.3",
    "LDO Nitehawk SB",
    "LDO Nitehawk36",
    "MKS Monster8 v2",
    "Mellow FLY-D5P",
    "Mellow Fly-Super8",
]


EXACT_IDS = {
    "BigTreeTech EBB36 Gen2": {"can": "btt_ebb36_gen2_can", "usb": "btt_ebb36_gen2_usb"},
    "BigTreeTech EBB42 Gen2": {"can": "btt_ebb42_gen2_can", "usb": "btt_ebb42_gen2_usb"},
    "BigTreeTech EBB36 V1.2": {"can": "btt_ebb36_v12_can"},
    "Fysetc Spider V1.0": {"can": "fysetc_spider"},
    "Fysetc Spider v2.2": {"can": "fysetc_spider"},
    "Fysetc Spider v2.3": {"can": "fysetc_spider"},
    "Fysetc Spider v3.0": {"can": "fysetc_spider"},
    "Fysetc Spider v3.0 H7": {"can": "fysetc_spider_v3_h7"},
    "Fysetc Spider King": {"can": "fysetc_spider_king"},
    "Fysetc H36": {"can": "fysetc_h36_combo"},
    "Fysetc H36 V2.0": {"can": "fysetc_h36_combo_v2"},
    "Fysetc SB Combo V2": {"can": "fysetc_sb_combo_v2"},
    "Fysetc SB-CAN-TH": {"can": "fysetc_sb_can_toolhead"},
    "Fysetc Hexa Distro Fusion": {"can": "fysetc_hexa_distro_fusion"},
}


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = repo_root.parent
    can_root = workspace / "esoterical-voron-canbus"
    write_can_catalogs(repo_root, can_root)
    write_usb_catalogs(repo_root)


def write_can_catalogs(repo_root: Path, can_root: Path) -> None:
    toolheads = list_hardware(can_root / "toolhead_flashing" / "common_hardware")
    mainboards = list_hardware(can_root / "mainboard_flashing" / "common_hardware")
    write_catalog(
        repo_root / "profiles" / "toolhead" / "esoterical_can_toolheads_catalog.yaml",
        toolheads,
        family="toolhead",
        transport="can",
        base_url="https://canbus.esoterical.online/toolhead_flashing/common_hardware",
        prefix="esoterical_can_toolhead",
    )
    write_catalog(
        repo_root / "profiles" / "mainboard" / "esoterical_can_mainboards_catalog.yaml",
        mainboards,
        family="mainboard",
        transport="can",
        base_url="https://canbus.esoterical.online/mainboard_flashing/common_hardware",
        prefix="esoterical_can_mainboard",
    )


def write_usb_catalogs(repo_root: Path) -> None:
    toolheads = [name for name in USB_HARDWARE if classify_family(name) == "toolhead"]
    mainboards = [name for name in USB_HARDWARE if classify_family(name) == "mainboard"]
    write_catalog(
        repo_root / "profiles" / "toolhead" / "esoterical_usb_toolheads_catalog.yaml",
        toolheads,
        family="toolhead",
        transport="usb",
        base_url="https://usb.esoterical.online/hardware_config",
        prefix="esoterical_usb_toolhead",
    )
    write_catalog(
        repo_root / "profiles" / "mainboard" / "esoterical_usb_mainboards_catalog.yaml",
        mainboards,
        family="mainboard",
        transport="usb",
        base_url="https://usb.esoterical.online/hardware_config",
        prefix="esoterical_usb_mainboard",
    )


def list_hardware(root: Path) -> list[str]:
    names = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if entry.is_dir() and (entry / "README.md").exists():
            names.append(title_from_readme(entry / "README.md") or entry.name)
    return names


def title_from_readme(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines()[:12]:
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip()
    return None


def write_catalog(
    path: Path,
    names: list[str],
    *,
    family: str,
    transport: str,
    base_url: str,
    prefix: str,
) -> None:
    lines = [
        "# Auto-generated from Esoterical hardware lists.",
        "# Precise build settings must be promoted from requires_esoterical_image_extraction",
        "# after the Katapult/Klipper menuconfig screenshot is parsed and reviewed.",
        "profiles:",
    ]
    for name in names:
        exact_id = EXACT_IDS.get(name, {}).get(transport)
        if exact_id:
            lines.extend(
                [
                    f"  # {name} is provided by exact profile: {exact_id}",
                ]
            )
            continue
        profile_id = f"{prefix}_{slug(name)}"
        lines.extend(profile_yaml(profile_id, name, family, transport, source_url(base_url, name), True))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def profile_yaml(profile_id: str, name: str, family: str, transport: str, url: str, pending: bool) -> list[str]:
    vendor = vendor_name(name)
    lines = [
        f"  - id: {profile_id}",
        f"    name: {name} {'USB' if transport == 'usb' and 'USB' not in name else ''}".rstrip(),
        f"    vendor: {vendor}",
        f"    family: {family}",
        "    source:",
        f"      - {url}",
        "    match:",
        "      mcu_name_hints:",
    ]
    for hint in hints(name):
        lines.append(f"        - {hint}")
    lines.extend(
        [
            "      transports:",
            f"        - {transport}",
            "    build:",
        ]
    )
    if pending:
        lines.append("      requires_esoterical_image_extraction: true")
    else:
        lines.append("      provided_by_exact_profile: true")
    lines.extend(
        [
            "    flash:",
            "      method: pending_verified_profile" if pending else "      method: provided_by_exact_profile",
        ]
    )
    return lines


def classify_family(name: str) -> str:
    lower = name.lower()
    toolhead_words = ["ebb", "sb", "h36", "pitb", "edd", "mmb", "afc", "dragondinghy", "nitehawk"]
    if any(word in lower for word in toolhead_words):
        return "toolhead"
    return "mainboard"


def vendor_name(name: str) -> str:
    if name.lower().startswith("bigtreetech"):
        return "BigTreeTech"
    if name.lower().startswith("fysetc"):
        return "FYSETC"
    if name.lower().startswith("mellow"):
        return "Mellow"
    if name.lower().startswith("mks"):
        return "MKS"
    if name.lower().startswith("ldo"):
        return "LDO"
    return "Esoterical"


def hints(name: str) -> list[str]:
    raw = re.split(r"[^A-Za-z0-9]+", name.lower())
    ignored = {"bigtreetech", "fysetc", "mellow", "fly", "v", "can", "usb", "for", "and", "the"}
    result = []
    for item in raw:
        if len(item) < 2 or item in ignored:
            continue
        if item not in result:
            result.append(item)
    return result[:5] or [slug(name)]


def source_url(base_url: str, name: str) -> str:
    if "usb.esoterical" in base_url:
        return f"{base_url}/{quote(name, safe='')}.html"
    return f"{base_url}/{quote(name, safe='')}/README.html"


def slug(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    value = value.replace("v1_0", "v10").replace("v2_0", "v20").replace("v3_0", "v30")
    return value


if __name__ == "__main__":
    main()
