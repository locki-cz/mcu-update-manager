from __future__ import annotations

from pathlib import Path
import json
import re
import sys


HEADING_RE = re.compile(r"^#+\s+(?P<title>.+?)\s*$")
IMAGE_RE = re.compile(r"(?:!\[[^\]]*\]\((?P<md>[^)]+)\)|<img[^>]+src=\"(?P<html>[^\"]+)\")")


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../esoterical-voron-canbus")
    records = []
    for readme in sorted(root.rglob("README.md")):
        rel = readme.relative_to(root)
        if "common_hardware" not in rel.parts:
            continue
        records.append(extract_readme(root, readme))

    print(json.dumps(records, indent=2, sort_keys=True))


def extract_readme(root: Path, readme: Path) -> dict[str, object]:
    current_heading = ""
    images: dict[str, list[str]] = {}
    title = readme.parent.name
    for line in readme.read_text(encoding="utf-8").splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            current_heading = heading.group("title").strip()
            continue

        for match in IMAGE_RE.finditer(line):
            url = match.group("md") or match.group("html")
            images.setdefault(current_heading, []).append(url)

    interesting = {
        heading: urls
        for heading, urls in images.items()
        if "Katapult Config" in heading
        or "Klipper Config" in heading
        or "Klipper USB-CAN-Bridge Config" in heading
    }
    return {
        "title": title,
        "path": str(readme.relative_to(root)),
        "images": interesting,
    }


if __name__ == "__main__":
    main()
