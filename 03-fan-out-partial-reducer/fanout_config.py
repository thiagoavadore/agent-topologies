"""What is fixed about the experiment: the split, the arms, the models."""

WORKER_MODEL = "claude-sonnet-5-5"
REDUCER_MODEL = "claude-opus-5-5"

# Built to collide: for every shared hub key, the services that want it sit on at least two workers.
WORKERS = {
    "w1": ["bookings-api", "maintenance-scheduler", "admin-console"],
    "w2": ["notifications", "customer-profiles", "fleet-telemetry"],
    "w3": ["pricing-engine", "payments-gateway"],
}
ARMS = ("first-wins", "supervisor-merges", "hub-owner", "overrides-allowed", "code-local", "human-decides")
AUTOMATED_ARMS = tuple(arm for arm in ARMS if arm != "human-decides")
HUMAN_ARM = "human-decides"
# The ladder of routes around the hub. `overrides-allowed`: workers may write their own overrides and code literals.
# `code-local`: no overrides, code literals allowed. Mandated arms: no overrides, and the timeout in code must read the
# hub (`http_timeout(...)`); only their merge owner may grant an override.
OVERRIDES_ALLOWED_ARM = "overrides-allowed"
CODE_LOCAL_ARM = "code-local"
MANDATED_ARMS = ("first-wins", "supervisor-merges", "hub-owner", "human-decides")
FIRST_WINS_ARMS = ("first-wins", OVERRIDES_ALLOWED_ARM, CODE_LOCAL_ARM)
