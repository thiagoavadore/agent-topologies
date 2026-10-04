"""Hand-written fixes for the Harbour Bikes fixture: one per planted risk, applied to a scratch copy."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import yaml

from topologies.harbour import FIXTURE


def copy_fixture(destination: Path) -> Path:
    shutil.copytree(FIXTURE, destination)
    return destination


def write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def replace(repo: Path, relative: str, old: str, new: str) -> None:
    text = (repo / relative).read_text()
    assert old in text, f"{old!r} not in {relative}"
    (repo / relative).write_text(text.replace(old, new))


def edit_yaml(repo: Path, relative: str, change: Callable[[dict], None]) -> None:
    data = yaml.safe_load((repo / relative).read_text())
    change(data)
    (repo / relative).write_text(yaml.safe_dump(data, sort_keys=False))


def set_service(repo: Path, service: str, field: str, value) -> None:
    edit_yaml(repo, f"{service}/service.yaml", lambda data: data.__setitem__(field, value))


def set_override(repo: Path, service: str, key: str, value) -> None:
    def change(data: dict) -> None:
        data["overrides"] = {**(data.get("overrides") or {}), key: value}

    edit_yaml(repo, f"{service}/service.yaml", change)


def set_hub(repo: Path, key: str, value) -> None:
    edit_yaml(repo, "platform.yaml", lambda data: data.__setitem__(key, value))


def set_npm_dependencies(repo: Path, dependencies: dict) -> None:
    manifest = json.loads((repo / "pricing-engine/package.json").read_text())
    manifest["dependencies"] = dependencies
    write(repo, "pricing-engine/package.json", json.dumps(manifest, indent=2))


def set_ingress_cidrs(repo: Path, cidrs: list) -> None:
    document = json.loads((repo / "admin-console/network.tf.json").read_text())
    document["resource"]["aws_security_group_rule"]["admin_console_ingress"]["cidr_blocks"] = cidrs
    write(repo, "admin-console/network.tf.json", json.dumps(document, indent=2))


BOOKINGS_CALL = 'requests.post(PAYMENTS_URL + "/charge", json=booking.to_payment())'
SMTP_CALL = "smtplib.SMTP(SMTP_HOST, 587)"


def fix_bookings_timeout(repo: Path) -> None:
    """Read the timeout from the platform, and give bookings-api its own 15 s override."""
    replace(repo, "bookings-api/bookings_api.py", "import requests\n", "import requests\nfrom platform_config import http_timeout\n")
    replace(
        repo,
        "bookings-api/bookings_api.py",
        BOOKINGS_CALL,
        'requests.post(PAYMENTS_URL + "/charge", json=booking.to_payment(), timeout=http_timeout("bookings-api"))',
    )
    set_override(repo, "bookings-api", "http.default_timeout", "15s")


def fix_bookings_rate_limit(repo: Path) -> None:
    set_override(repo, "bookings-api", "http.default_rate_limit", "10/s")


def fix_payments_secret(repo: Path) -> None:
    replace(repo, "payments-gateway/config.yaml", "cp_live_9f2c8e71d4a04b6fa3e1c55d0b8a2f17", "${PROCESSOR_API_KEY}")


def fix_fleet_spof(repo: Path) -> None:
    set_service(repo, "fleet-telemetry", "instances", 2)


def fix_fleet_owner(repo: Path) -> None:
    set_service(repo, "fleet-telemetry", "owner", {"team": "Team Fleet", "contact": "fleet@harbourbikes.example"})


def fix_profiles_backup(repo: Path) -> None:
    set_service(repo, "customer-profiles", "backup", "daily/30d")


def fix_pricing_pins(repo: Path) -> None:
    set_npm_dependencies(
        repo, {"express": "4.21.1", "decimal.js": "10.4.3", "demand-model-client": "2.3.1", "pino": "9.5.0"}
    )


def fix_admin_exposure(repo: Path) -> None:
    set_ingress_cidrs(repo, ["${var.staff_vpn_cidr}"])


def fix_notifications_timeout(repo: Path) -> None:
    replace(repo, "notifications/notifications.py", 'SMTP_USER = "notifications"\n', 'SMTP_USER = "notifications"\nSMTP_TIMEOUT_S = 10\n')
    replace(repo, "notifications/notifications.py", SMTP_CALL, "smtplib.SMTP(SMTP_HOST, 587, timeout=SMTP_TIMEOUT_S)")


def fix_notifications_pins(repo: Path) -> None:
    write(repo, "notifications/requirements.txt", "celery==5.4.0\nredis==5.2.0\njinja2==3.1.4\nfirebase-admin==6.6.0\n")


def fix_scheduler_spof(repo: Path) -> None:
    set_service(repo, "maintenance-scheduler", "instances", 2)


def fix_scheduler_backup(repo: Path) -> None:
    set_service(repo, "maintenance-scheduler", "backup", "daily/30d")


FIXES: dict[str, Callable[[Path], None]] = {
    "bookings-api.missing_timeout": fix_bookings_timeout,
    "bookings-api.no_rate_limit": fix_bookings_rate_limit,
    "payments-gateway.hardcoded_secret": fix_payments_secret,
    "fleet-telemetry.single_point_of_failure": fix_fleet_spof,
    "fleet-telemetry.no_owner": fix_fleet_owner,
    "customer-profiles.missing_backup": fix_profiles_backup,
    "pricing-engine.unpinned_dependency": fix_pricing_pins,
    "admin-console.public_exposure": fix_admin_exposure,
    "notifications.missing_timeout": fix_notifications_timeout,
    "notifications.unpinned_dependency": fix_notifications_pins,
    "maintenance-scheduler.single_point_of_failure": fix_scheduler_spof,
    "maintenance-scheduler.missing_backup": fix_scheduler_backup,
}


def reference_fix(repo: Path) -> Path:
    for fix in FIXES.values():
        fix(repo)
    return repo
