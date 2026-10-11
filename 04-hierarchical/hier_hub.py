"""Hub-file, override and mandate helpers copied from folder 03 at 7888d48 so this folder runs on its own."""

import ast
import json
import shutil
from pathlib import Path

import yaml

from topologies.fixcheck import BOOKINGS_CALL, NOTIFICATIONS_CALL, parse_duration
from topologies.harbour import FIXTURE, OutsideContract
from topologies.pytimeouts import Resolver, Unresolved, call_name, effective_timeout

HTTP_KEYS = ("http.default_timeout", "http.default_rate_limit")
MANDATED_FILES = {"bookings-api/bookings_api.py": BOOKINGS_CALL, "notifications/notifications.py": NOTIFICATIONS_CALL}


def scratch_copy(dest: Path) -> Path:
    """A copy of the pristine world without the answer key."""
    shutil.copytree(FIXTURE, dest, ignore=shutil.ignore_patterns("CHECKER.md", "__pycache__", ".git"))
    return dest


# platform.yaml: one `dotted.key: value` line per key, `#` comment lines above.


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


# Service-level overrides in service.yaml: find a worker's edits, strip them, or grant one as the platform lead.


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


# The platform mandate, checked like CI: an outbound call's timeout must be `http_timeout("<service>")`.


def mandate_violation(path: str, text: str) -> str | None:
    """Why `text` breaks the mandate for the Python file at `path`, or None when it keeps it (or is not mandated)."""
    site = MANDATED_FILES.get(path)
    if site is None:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return f"{path} does not parse: {error}"
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == site.function]
    if len(functions) != 1:
        return f"expected one module-level {site.function}(), found {len(functions)}"
    calls = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Call) and call_name(node) in site.call_names]
    if len(calls) != 1:
        return f"expected one {'/'.join(site.call_names)} call in {site.function}(), found {len(calls)}"
    call = calls[0]
    expression = next((keyword.value for keyword in call.keywords if keyword.arg == "timeout"), None)
    if expression is None and site.timeout_position is not None and len(call.args) > site.timeout_position:
        expression = call.args[site.timeout_position]
    if expression is None:
        return f"the call in {site.function}() has no timeout"
    try:
        is_hub_call = isinstance(expression, ast.Call) and Resolver(tree, functions[0], site.service, lambda: 0.0).is_hub_call(expression, 0)
    except (OutsideContract, Unresolved) as error:
        return str(error)
    return None if is_hub_call else f"`{ast.unparse(expression)}` is not http_timeout({site.service!r})"
