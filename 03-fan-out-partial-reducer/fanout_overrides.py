"""Service-level overrides in service.yaml: find a worker's edits, strip them, or grant one as the merge owner."""

from pathlib import Path

import yaml

from topologies.fixcheck import BOOKINGS_CALL, NOTIFICATIONS_CALL, parse_duration
from topologies.harbour import OutsideContract
from topologies.pytimeouts import Unresolved, effective_timeout

HTTP_KEYS = ("http.default_timeout", "http.default_rate_limit")


def overrides_of(text: str) -> dict | None:
    """The `overrides:` mapping of a service.yaml text, or None when the text does not parse to a mapping."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    overrides = data.get("overrides") or {}
    return overrides if isinstance(overrides, dict) else None


def override_edits(base_text: str, new_text: str) -> list[dict]:
    """Overrides present in `new_text` that are missing or different in `base_text`, as [{key, value}]."""
    base, new = overrides_of(base_text) or {}, overrides_of(new_text)
    if new is None:
        return []
    return [{"key": key, "value": str(value)} for key, value in new.items() if base.get(key) != value]


def strip_overrides(base_text: str, new_text: str) -> str:
    """Put the base `overrides:` back into `new_text`, keeping the worker's other edits (comments are lost)."""
    data = yaml.safe_load(new_text)
    data["overrides"] = overrides_of(base_text) or {}
    return yaml.safe_dump(data, sort_keys=False)


def grant_override(text: str, key: str, value: str) -> str:
    """Add or replace one override in a service.yaml text (comments are lost)."""
    data = yaml.safe_load(text)
    data["overrides"] = {**(data.get("overrides") or {}), key: value}
    return yaml.safe_dump(data, sort_keys=False)


def service_overrides(root: Path, services: tuple[str, ...]) -> dict[str, dict]:
    """Every non-empty `overrides:` mapping in the repo at `root`, by service."""
    found = {}
    for service in services:
        overrides = overrides_of((root / service / "service.yaml").read_text(encoding="utf-8"))
        if overrides:
            found[service] = overrides
    return found


def effective_timeouts(root: Path, services: tuple[str, ...], hub_timeout: str) -> dict[str, float | None]:
    """The timeout in seconds each service ends up with: the real call's for the two Python services, else its override or the hub value.

    None means the value could not be read (for example a call timeout that does not resolve).
    """
    overrides = service_overrides(root, services)
    setting = {service: overrides.get(service, {}).get("http.default_timeout", hub_timeout) for service in services}
    found: dict[str, float | None] = {service: parse_duration(value) for service, value in setting.items()}
    for site in (BOOKINGS_CALL, NOTIFICATIONS_CALL):
        try:
            found[site.service] = effective_timeout(root, site, lambda service=site.service: parse_duration(setting[service])).seconds
        except (OutsideContract, Unresolved, ValueError, OSError):
            found[site.service] = None
    return found
