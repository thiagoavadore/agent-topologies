"""What is fixed about the experiment: the split, the arms, the models."""

WORKER_MODEL = "claude-sonnet-5-5"
REDUCER_MODEL = "claude-opus-5-5"

# Built to collide: for every shared hub key, the services that want it sit on at least two workers.
WORKERS = {
    "w1": ["bookings-api", "maintenance-scheduler", "admin-console"],
    "w2": ["notifications", "customer-profiles", "fleet-telemetry"],
    "w3": ["pricing-engine", "payments-gateway"],
}
ARMS = ("first-wins", "supervisor-merges", "hub-owner", "overrides-allowed", "human-decides")
AUTOMATED_ARMS = tuple(arm for arm in ARMS if arm != "human-decides")
HUMAN_ARM = "human-decides"
# Only the merge owner may write service.yaml overrides; in `overrides-allowed` every worker may write its own.
OVERRIDES_ALLOWED_ARM = "overrides-allowed"
