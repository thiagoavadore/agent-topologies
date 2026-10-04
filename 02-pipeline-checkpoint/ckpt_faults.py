"""Deterministic faults the harness plants in the stage-1 output, aimed at a catalogue risk by ID."""

import copy
from dataclasses import dataclass

from topologies.harbour import RISKS_BY_ID, SERVICES, Risk

# Five risks with evidence in one file each; rotated over the faulted runs.
FAULT_TARGETS = (
    "payments-gateway.hardcoded_secret",
    "admin-console.public_exposure",
    "customer-profiles.missing_backup",
    "bookings-api.missing_timeout",
    "fleet-telemetry.single_point_of_failure",
)
FAULT_KINDS = ("wrong-service", "dropped")
assert all(target in RISKS_BY_ID for target in FAULT_TARGETS)


@dataclass(frozen=True)
class Fault:
    kind: str  # wrong-service | dropped
    risk_id: str


@dataclass(frozen=True)
class Injection:
    kind: str
    risk_id: str
    matched_by: str  # locator | file
    facts_changed: int
    moved_to: str = ""  # wrong-service only


def locator_parts(locator: str) -> tuple[str, tuple[str, ...]]:
    """Split a locator into a kind and its parts: code (callee name), path (dotted keys) or file (whole file)."""
    text = locator.strip().lower()
    if not text:
        return "empty", ()
    if "(" in text:
        return "code", (text.split("(")[0].strip(),)
    first = text.split()[0].rstrip(":")
    if first == "every":
        return "file", ()
    return "path", tuple(first.split("."))


def locator_matches(cited: str, expected: str) -> bool:
    """Say whether a fact's locator points at the catalogue's locator, within the same file."""
    cited_kind, cited_parts = locator_parts(cited)
    expected_kind, expected_parts = locator_parts(expected)
    if cited_kind == "empty":
        return False
    if expected_kind == "file":
        return True
    if cited_kind != expected_kind:
        return False
    if expected_kind == "code":
        return cited_parts == expected_parts
    shorter = min(len(cited_parts), len(expected_parts))
    return cited_parts[:shorter] == expected_parts[:shorter]


def target_facts(facts: list[dict], risk: Risk) -> tuple[list[dict], str]:
    """The facts a fault hits: those citing the risk's file and locator, else every fact citing its file."""
    in_file = [fact for fact in facts if fact["file"] == risk.file]
    exact = [fact for fact in in_file if locator_matches(fact["locator"], risk.locator)]
    return (exact, "locator") if exact else (in_file, "file")


def inject(output: dict, fault: Fault) -> tuple[dict, Injection]:
    """Return a faulted copy of the stage-1 output and what was done. Stage 1 must cover every file, so a miss is a bug."""
    risk = RISKS_BY_ID[fault.risk_id]
    faulted = copy.deepcopy(output)
    hits, matched_by = target_facts(faulted["facts"], risk)
    assert hits, f"no stage-1 fact cites {risk.file}: the file-coverage check should have stopped the run"
    if fault.kind == "dropped":
        faulted["facts"] = [fact for fact in faulted["facts"] if not any(fact is hit for hit in hits)]
        return faulted, Injection(fault.kind, fault.risk_id, matched_by, len(hits))
    # The next service in catalogue order: deterministic and never the true owner.
    wrong = SERVICES[(SERVICES.index(risk.service) + 1) % len(SERVICES)]
    for fact in hits:
        fact["service"] = wrong
    return faulted, Injection(fault.kind, fault.risk_id, matched_by, len(hits), wrong)
