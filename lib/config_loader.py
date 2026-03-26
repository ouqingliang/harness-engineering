from __future__ import annotations

from pathlib import Path
from typing import Any


def _strip_comment(line: str) -> str:
    hash_index = line.find("#")
    if hash_index == -1:
        return line.rstrip()
    return line[:hash_index].rstrip()


def _parse_scalar(raw_value: str) -> Any:
    value = raw_value.strip()
    if value == "":
        return ""
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def load_config_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    payload: dict[str, Any] = {}
    current_list_key: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = _strip_comment(raw_line)
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current_list_key is None:
                raise ValueError(f"list item without key in {path}")
            payload.setdefault(current_list_key, [])
            payload[current_list_key].append(_parse_scalar(stripped[2:]))
            continue

        current_list_key = None
        if ":" not in line:
            raise ValueError(f"unsupported config line in {path}: {raw_line!r}")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value == "":
            payload[key] = []
            current_list_key = key
            continue
        payload[key] = _parse_scalar(value)

    return payload
