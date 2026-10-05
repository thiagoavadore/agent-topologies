"""Read and edit `platform.yaml`: one `dotted.key: value` line per key, `#` comment lines above."""

import json


def parse_hub(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"platform.yaml line is not `key: value`: {line!r}")
        value = value.strip()
        values[key.strip()] = json.loads(value) if value.startswith('"') else value
    return values


def format_value(value: str) -> str:
    value = value.strip()
    if not value or "\n" in value:
        raise ValueError(f"hub value must be one non-empty line: {value!r}")
    return json.dumps(value) if ": " in value or " #" in value or value.startswith('"') else value


def set_hub_values(text: str, changes: dict[str, str]) -> str:
    """Return `text` with the line of each changed key replaced; comments and other keys stay."""
    unknown = set(changes) - set(parse_hub(text))
    if unknown:
        raise ValueError(f"unknown hub keys: {sorted(unknown)}")
    lines = []
    for line in text.splitlines():
        key = line.partition(":")[0].strip()
        if not line.lstrip().startswith("#") and key in changes:
            line = f"{key}: {format_value(changes[key])}"
        lines.append(line)
    return "\n".join(lines) + "\n"
