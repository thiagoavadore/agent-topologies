"""What is fixed about the experiment: the models, the arms and the two ways to cut the services into groups."""

import yaml

from topologies.harbour import FIXTURE

WORKER_MODEL = "claude-sonnet-5-5"
LEAD_MODEL = "claude-opus-5-5"
ARMS = ("flat", "by-team", "by-runtime", "by-runtime-registry")
# by-team groups whole teams along the booking call graph; fleet-telemetry has no owner and sits with Team Fleet.
CUTS = {
    "by-team": {
        "rentals-and-money": ["bookings-api", "pricing-engine", "payments-gateway"],
        "fleet": ["fleet-telemetry", "maintenance-scheduler"],
        "customer-and-back-office": ["notifications", "customer-profiles", "admin-console"],
    },
    "by-runtime": {
        "python": ["bookings-api", "notifications", "maintenance-scheduler"],
        "jvm-and-go": ["customer-profiles", "payments-gateway"],
        "node-ruby-rust": ["pricing-engine", "admin-console", "fleet-telemetry"],
    },
}
CUT_OF_ARM = {"by-team": "by-team", "by-runtime": "by-runtime", "by-runtime-registry": "by-runtime"}
REGISTRY_ARMS = ("by-runtime-registry",)
# The platform lead sees every service's needs in flat (it manages all 8 workers) and where the registry is attached.
NEEDS_ARMS = ("flat", *REGISTRY_ARMS)


def owner_team(service: str) -> str | None:
    """The owner team in the pristine service.yaml, or None for the unowned service."""
    owner = yaml.safe_load((FIXTURE / service / "service.yaml").read_text(encoding="utf-8")).get("owner")
    return owner["team"] if owner else None


def group_of(cut: str, service: str) -> str:
    return next(group for group, services in CUTS[cut].items() if service in services)
