"""Platform settings for Python services: the service's override from service.yaml, else platform.yaml."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def setting(service: str, key: str):
    service_file = yaml.safe_load((ROOT / service / "service.yaml").read_text())
    overrides = service_file.get("overrides") or {}
    if key in overrides:
        return overrides[key]
    return yaml.safe_load((ROOT / "platform.yaml").read_text())[key]


def http_timeout(service: str) -> float:
    """Seconds an outbound call may wait, from `http.default_timeout` (e.g. 300ms or 12s)."""
    value = str(setting(service, "http.default_timeout")).strip()
    if value.endswith("ms"):
        return float(value[:-2]) / 1000
    if value.endswith("s"):
        return float(value[:-1])
    raise ValueError(f"http.default_timeout needs a unit (ms or s): {value!r}")
