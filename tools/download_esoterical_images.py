from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen
import hashlib
import json
import shutil
import sys


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    index_path = root / "esoterical-image-index.json"
    out_dir = root / "esoterical-images"
    can_root = root.parent / "esoterical-voron-canbus"
    out_dir.mkdir(parents=True, exist_ok=True)
    index = json.loads(index_path.read_text(encoding="utf-8-sig"))

    manifest = []
    for record in index:
        title = record["title"]
        for heading, urls in record.get("images", {}).items():
            for idx, url in enumerate(urls, start=1):
                filename = f"{slug(title)}__{slug(heading)}__{idx:02d}__{hash_url(url)}.png"
                path = out_dir / filename
                if not path.exists():
                    if url.startswith(("http://", "https://")):
                        download(url, path)
                    else:
                        source = can_root / record["path"]
                        source = source.parent / url
                        shutil.copyfile(source, path)
                manifest.append(
                    {
                        "title": title,
                        "heading": heading,
                        "url": url,
                        "path": str(path.relative_to(root)),
                    }
                )

    (root / "esoterical-images-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"downloaded/manifested {len(manifest)} images")


def download(url: str, path: Path) -> None:
    request = Request(url, headers={"User-Agent": "MCU-Update-Manager/0.1"})
    with urlopen(request, timeout=30) as response:
        path.write_bytes(response.read())


def slug(value: str) -> str:
    output = "".join(char.lower() if char.isalnum() else "_" for char in value)
    while "__" in output:
        output = output.replace("__", "_")
    return output.strip("_")


def hash_url(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]


if __name__ == "__main__":
    main()
