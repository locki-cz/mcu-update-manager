from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
import glob
import re


SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*(?:[#;].*)?$")
OPTION_RE = re.compile(r"^\s*(?P<key>[A-Za-z0-9_.-]+)\s*:\s*(?P<value>.*?)\s*(?:[#;].*)?$")


@dataclass
class ConfigOption:
    key: str
    value: str
    source: str
    line: int


@dataclass
class ConfigSection:
    name: str
    source: str
    line: int
    options: dict[str, ConfigOption] = field(default_factory=dict)


@dataclass
class ParsedConfig:
    root: str
    sections: list[ConfigSection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def sections_by_prefix(self, prefix: str) -> list[ConfigSection]:
        return [section for section in self.sections if section.name == prefix or section.name.startswith(prefix + " ")]


class KlipperConfigParser:
    def __init__(self, root_file: str | Path):
        self.root_file = Path(root_file).expanduser()
        self._visited: set[Path] = set()
        self._sections: list[ConfigSection] = []
        self._warnings: list[str] = []

    def parse(self) -> ParsedConfig:
        self._parse_file(self.root_file)
        return ParsedConfig(root=str(self.root_file), sections=self._sections, warnings=self._warnings)

    def _parse_file(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved in self._visited:
            return

        if not resolved.exists():
            self._warnings.append(f"Missing config file: {path}")
            return

        self._visited.add(resolved)
        current: ConfigSection | None = None

        for line_number, raw_line in enumerate(resolved.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue

            section_match = SECTION_RE.match(raw_line)
            if section_match:
                section_name = section_match.group("name").strip()
                if section_name.startswith("include "):
                    include_pattern = section_name.removeprefix("include ").strip()
                    self._parse_includes(resolved.parent, include_pattern)
                    current = None
                    continue

                current = ConfigSection(name=section_name, source=str(resolved), line=line_number)
                self._sections.append(current)
                continue

            option_match = OPTION_RE.match(raw_line)
            if option_match and current:
                key = option_match.group("key").lower()
                current.options[key] = ConfigOption(
                    key=key,
                    value=option_match.group("value").strip(),
                    source=str(resolved),
                    line=line_number,
                )

    def _parse_includes(self, base_dir: Path, include_pattern: str) -> None:
        candidates = self._expand_include(base_dir, include_pattern)
        if not candidates:
            self._warnings.append(f"Include matched no files: {include_pattern}")
            return

        for candidate in candidates:
            self._parse_file(candidate)

    def _expand_include(self, base_dir: Path, include_pattern: str) -> Iterable[Path]:
        path_pattern = Path(include_pattern).expanduser()
        if not path_pattern.is_absolute():
            path_pattern = base_dir / path_pattern

        matches = glob.glob(str(path_pattern))
        return sorted(Path(match) for match in matches)


def mcu_name_from_section(section_name: str) -> str:
    if section_name == "mcu":
        return "mcu"

    return section_name.removeprefix("mcu ").strip()


def option_value(section: ConfigSection, key: str) -> str | None:
    option = section.options.get(key.lower())
    return option.value if option else None
