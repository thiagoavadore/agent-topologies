"""The Harbour Bikes fixture world: where it lives, its 12 planted risks and its numeric needs."""

from dataclasses import dataclass
from pathlib import Path

import yaml

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "harbour-bikes"

SERVICES = (
    "admin-console",
    "bookings-api",
    "customer-profiles",
    "fleet-telemetry",
    "maintenance-scheduler",
    "notifications",
    "payments-gateway",
    "pricing-engine",
)

HUB_FILE = "platform.yaml"
HUB_KEYS = (
    "http.default_timeout",
    "http.default_rate_limit",
    "backup.policy",
    "dependencies.pinning",
    "availability.min_instances",
)


@dataclass(frozen=True)
class Risk:
    service: str
    category: str
    file: str  # relative to the repo root
    locator: str  # where in the file: a function call, or a YAML/JSON path

    @property
    def id(self) -> str:
        return f"{self.service}.{self.category}"


RISKS = (
    Risk("bookings-api", "missing_timeout", "bookings-api/bookings_api.py", "charge(): requests.post(timeout=)"),
    Risk("bookings-api", "no_rate_limit", "bookings-api/service.yaml", "overrides.http.default_rate_limit"),
    Risk("payments-gateway", "hardcoded_secret", "payments-gateway/config.yaml", "processor.api_key"),
    Risk("fleet-telemetry", "single_point_of_failure", "fleet-telemetry/service.yaml", "instances"),
    Risk("fleet-telemetry", "no_owner", "fleet-telemetry/service.yaml", "owner"),
    Risk("customer-profiles", "missing_backup", "customer-profiles/service.yaml", "backup"),
    Risk("pricing-engine", "unpinned_dependency", "pricing-engine/package.json", "dependencies"),
    Risk(
        "admin-console",
        "public_exposure",
        "admin-console/network.tf.json",
        "resource.aws_security_group_rule.admin_console_ingress.cidr_blocks",
    ),
    Risk("notifications", "missing_timeout", "notifications/notifications.py", "send_email(): smtplib.SMTP(timeout=)"),
    Risk("notifications", "unpinned_dependency", "notifications/requirements.txt", "every requirement"),
    Risk("maintenance-scheduler", "single_point_of_failure", "maintenance-scheduler/service.yaml", "instances"),
    Risk("maintenance-scheduler", "missing_backup", "maintenance-scheduler/service.yaml", "backup"),
)
RISKS_BY_ID = {risk.id: risk for risk in RISKS}

# What each need measures: a hub key resolved for the service, or the timeout of a call in code.
NEED_MEASURES = {
    "bookings-api.payment_call_timeout": "bookings-api.missing_timeout",
    "bookings-api.public_rate_limit_per_client": "http.default_rate_limit",
    "notifications.smtp_timeout": "notifications.missing_timeout",
    "pricing-engine.demand_model_timeout": "http.default_timeout",
    "payments-gateway.inbound_rate_per_caller": "http.default_rate_limit",
}
# Needs met at the start: breaking one of these is a regression, not an unfixed risk.
REGRESSION_NEEDS = ("pricing-engine.demand_model_timeout", "payments-gateway.inbound_rate_per_caller")


@dataclass(frozen=True)
class Need:
    service: str
    name: str
    minimum: str | None  # in the value's own grammar, e.g. "12s" or "20/s"
    maximum: str | None
    measures: str

    @property
    def id(self) -> str:
        return f"{self.service}.{self.name}"


def load_needs(root: Path = FIXTURE) -> dict[str, Need]:
    """Read the numeric needs from the pristine fixture's service.yaml files, never from a repo under check."""
    needs = {}
    for service in SERVICES:
        declared = yaml.safe_load((root / service / "service.yaml").read_text()).get("needs") or {}
        for name, bounds in declared.items():
            need_id = f"{service}.{name}"
            needs[need_id] = Need(service, name, bounds.get("min"), bounds.get("max"), NEED_MEASURES[need_id])
    if set(needs) != set(NEED_MEASURES):
        raise ValueError(f"fixture needs {sorted(needs)} do not match NEED_MEASURES {sorted(NEED_MEASURES)}")
    return needs


NEEDS = load_needs()


def planted_secret(root: Path = FIXTURE) -> str:
    return yaml.safe_load((root / "payments-gateway" / "config.yaml").read_text())["processor"]["api_key"]
