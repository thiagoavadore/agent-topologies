"""What is fixed about the experiment: the models, the arms, the faults, and who owns what in the pristine fixture."""

from dataclasses import dataclass

import yaml

from topologies.harbour import FIXTURE, RISKS, SERVICES

CONSUMER_MODEL = "claude-sonnet-5-5"
OWNER_MODEL = "claude-opus-5-5"  # steward, platform agent and investigator
ARMS = ("no-owner", "steward-agent", "platform-owned")
FAULT_KINDS = ("malformed", "wrong-service", "consumer-crash", "duplicate")
LOSS_FAULT_KINDS = ("malformed", "wrong-service", "consumer-crash")
FAULT_OFFSETS = {"malformed": 0, "wrong-service": 2, "consumer-crash": 4, "duplicate": 6}


def _owner_team(service: str) -> str | None:
    owner = yaml.safe_load((FIXTURE / service / "service.yaml").read_text())["owner"]
    return owner["team"] if owner else None


# All three are read from the pristine fixture, never from a scratch repo an agent has edited.
OWNER = {service: _owner_team(service) for service in SERVICES}
TEAMS = {item["name"]: item["contact"] for item in yaml.safe_load((FIXTURE / "teams.yaml").read_text())["teams"]}
SUBSCRIPTIONS = {team: [service for service in SERVICES if OWNER[service] == team] for team in TEAMS}
ROUTABLE = [risk.id for risk in RISKS if OWNER[risk.service]]
assert len(ROUTABLE) == 9, ROUTABLE


@dataclass(frozen=True)
class Policy:
    schema_check: bool  # Datatype Channel: invalid messages go to the Invalid Message Channel
    acks: bool  # at-least-once: a failed delivery is redelivered once, then dead-lettered
    idempotent_receiver: bool
    dead_letter: bool  # no subscriber, declines and double failures go to the Dead Letter Channel
    trace_context: bool  # Correlation Identifier and Message History on every message
    keep_store: bool  # Message Store of every event
    owner_view: str  # none, store or exceptions


POLICIES = {
    "no-owner": Policy(False, False, False, False, False, False, "none"),
    "steward-agent": Policy(False, False, False, False, False, True, "store"),
    "platform-owned": Policy(True, True, True, True, True, True, "exceptions"),
}
assert tuple(POLICIES) == ARMS
