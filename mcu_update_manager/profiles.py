from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json


@dataclass
class HardwareProfile:
    id: str
    name: str
    vendor: str = ""
    family: str = ""
    match: dict[str, Any] = field(default_factory=dict)
    fingerprint: dict[str, Any] = field(default_factory=dict)
    build: dict[str, Any] = field(default_factory=dict)
    bootloader: dict[str, Any] = field(default_factory=dict)
    initial_flash: dict[str, Any] = field(default_factory=dict)
    update: dict[str, Any] = field(default_factory=dict)
    flash: dict[str, Any] = field(default_factory=dict)
    source: list[str] = field(default_factory=list)
    path: str = ""

    def score(
        self,
        chip: str | None,
        transport: str | None,
        mcu_name: str | None,
        referenced_pins: list[str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        score = 0
        reasons: list[str] = []
        chip = (chip or "").lower()
        transport = (transport or "").lower()
        mcu_name = (mcu_name or "").lower()
        referenced_pins_set = {normalize_pin(pin) for pin in referenced_pins or []}
        chips = [str(item).lower() for item in self.match.get("chips", [])]
        transports = [str(item).lower() for item in self.match.get("transports", [])]

        if chips and chip not in chips:
            return 0, {"reasons": reasons, "fingerprint": {}}

        if transports and transport not in transports:
            return 0, {"reasons": reasons, "fingerprint": {}}

        if chip and chip in chips:
            score += 60
            reasons.append("chip")

        if transport and transport in transports:
            score += 25
            reasons.append("transport")

        for hint in self.match.get("mcu_name_hints", []):
            if str(hint).lower() in mcu_name:
                score += 10
                reasons.append("mcu_name_hint")
                break

        fingerprint = self._fingerprint_score(referenced_pins_set)
        score += fingerprint["score"]
        if fingerprint["matched_strong"]:
            reasons.append("printer_cfg_pins")

        if "custom" in {str(item).lower() for item in self.source}:
            score -= 15
            reasons.append("custom_fallback")

        return score, {"reasons": reasons, "fingerprint": fingerprint}

    def _fingerprint_score(self, referenced_pins: set[str]) -> dict[str, Any]:
        strong_pins = {
            normalize_pin(pin)
            for pin in self.fingerprint.get("printer_cfg_pins", {}).get("strong", [])
        }
        if not strong_pins:
            return {"score": 0, "matched_strong": [], "missing_strong": []}

        matched = sorted(pin for pin in strong_pins if pin in referenced_pins)
        missing = sorted(pin for pin in strong_pins if pin not in referenced_pins)

        return {
            "score": len(matched) * 5,
            "matched_strong": matched,
            "missing_strong": missing,
        }


def automatic_build_ready(config: dict[str, Any]) -> bool:
    blocked = ("requires_manual_menuconfig", "requires_esoterical_image_extraction",
               "import_config", "requires_exact_profile")
    if any(config.get(key) for key in blocked):
        return False
    if not all(config.get(key) for key in ("architecture", "processor", "communication")):
        return False
    if config.get("architecture") == "stm32" and not (config.get("bootloader_offset") or config.get("application_start_offset")):
        return False
    return True


def load_profiles(paths: list[str | Path]) -> list[HardwareProfile]:
    profiles: list[HardwareProfile] = []
    for profile_dir in paths:
        root = Path(profile_dir).expanduser()
        if not root.exists():
            continue

        for file in sorted(root.rglob("*.yaml")):
            data = load_simple_yaml(file)
            entries = data.get("profiles", [data])
            for entry in entries:
                profiles.append(profile_from_data(entry, file))

    return profiles


def profile_from_data(data: dict[str, Any], file: Path) -> HardwareProfile:
    return HardwareProfile(
        id=str(data["id"]),
        name=str(data["name"]),
        vendor=str(data.get("vendor", "")),
        family=str(data.get("family", "")),
        match=dict(data.get("match", {})),
        fingerprint=dict(data.get("fingerprint", {})),
        build=dict(data.get("build", {})),
        bootloader=dict(data.get("bootloader", {})),
        initial_flash=dict(data.get("initial_flash", {})),
        update=dict(data.get("update", {})),
        flash=dict(data.get("flash", {})),
        source=list(data.get("source", [])),
        path=str(file),
    )


def best_profile_matches(
    profiles: list[HardwareProfile],
    chip: str | None,
    transport: str | None,
    mcu_name: str | None,
    referenced_pins: list[str] | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    scored = []
    for profile in profiles:
        score, detail = profile.score(
            chip=chip,
            transport=transport,
            mcu_name=mcu_name,
            referenced_pins=referenced_pins,
        )
        if score:
            scored.append({"id": profile.id, "name": profile.name, "score": score, **detail})

    return sorted(scored, key=lambda item: (-item["score"], item["name"]))[:limit]


def normalize_pin(pin: str) -> str:
    pin = str(pin).strip().lower()
    pin = pin.lstrip("!^~")
    if ":" in pin:
        pin = pin.split(":", 1)[1]
    return pin


def load_simple_yaml(path: str | Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except ModuleNotFoundError:
        return _load_yaml_subset(Path(path).read_text(encoding="utf-8"))


def _load_yaml_subset(text: str) -> dict[str, Any]:
    """Small YAML subset loader for this prototype.

    It supports the profile files in this repository. PyYAML should be used in
    production, but keeping this fallback makes the scanner runnable on a clean
    Python install.
    """
    result: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, result)]

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()

        parent = stack[-1][1]
        if line.startswith("- "):
            value_text = line[2:].strip()
            if isinstance(parent, list):
                if ":" in value_text and not value_text.startswith(("http://", "https://")):
                    key, _, raw_value = value_text.partition(":")
                    item: dict[str, Any] = {key.strip(): _parse_scalar(raw_value.strip()) if raw_value.strip() else {}}
                    parent.append(item)
                    stack.append((indent, item))
                else:
                    parent.append(_parse_scalar(value_text))
            continue

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        if value == "":
            next_container: Any = []
            if _next_non_empty_is_mapping(text, raw_line):
                next_container = {}
            parent[key] = next_container
            stack.append((indent, next_container))
        else:
            parent[key] = _parse_scalar(value)

    return json.loads(json.dumps(result))


def _parse_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _next_non_empty_is_mapping(text: str, current_line: str) -> bool:
    lines = text.splitlines()
    index = lines.index(current_line)
    current_indent = len(current_line) - len(current_line.lstrip(" "))
    for candidate in lines[index + 1 :]:
        if not candidate.strip() or candidate.lstrip().startswith("#"):
            continue
        indent = len(candidate) - len(candidate.lstrip(" "))
        stripped = candidate.strip()
        return indent > current_indent and not stripped.startswith("- ")
    return True
