from __future__ import annotations

from typing import Any
from urllib.parse import quote, urljoin
from urllib.request import urlopen
import json


def query_printer_objects(moonraker_url: str, object_names: list[str]) -> tuple[dict[str, Any], str | None]:
    if not object_names:
        return {}, None

    base_url = moonraker_url.rstrip("/") + "/"
    query = "&".join(quote(name, safe="") for name in object_names)
    url = urljoin(base_url, "printer/objects/query") + "?" + query

    try:
        with urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {}, f"Moonraker object query failed: {exc}"

    result = payload.get("result", payload)
    status = result.get("status", {})
    if not isinstance(status, dict):
        return {}, "Moonraker object query returned no status object."

    return status, None
