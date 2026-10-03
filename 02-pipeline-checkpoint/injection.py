"""Deterministic faults the harness plants in the stage-1 output, so every arm faces the same damage."""

import copy
from dataclasses import dataclass

# Each target is a planted risk plus a phrase that identifies its stage-1 facts (matched on card + phrase).
FAULT_TARGETS = {
    ("pricing-engine.md", "unpinned_dependency"): "latest",
    ("payments-gateway.md", "hardcoded_secret"): "cp_live_",
    ("customer-profiles.md", "missing_backup"): "snapshot",
    ("admin-console.md", "public_exposure"): "0.0.0.0/0",
    ("fleet-telemetry.md", "single_point_of_failure"): "no standby",
}
FAULT_KINDS = ("wrong-card", "dropped")


@dataclass(frozen=True)
class Fault:
    kind: str  # wrong-card | dropped
    card: str
    category: str


@dataclass(frozen=True)
class Injection:
    applied: bool
    kind: str
    card: str
    category: str
    facts_changed: int = 0
    moved_to: str = ""  # wrong-card only


def is_target_fact(fact: dict, card: str, phrase: str) -> bool:
    return fact["card"] == card and phrase.lower() in f"{fact['fact']} {fact['evidence']}".lower()


def inject(output: dict, fault: Fault, cards: list[str]) -> tuple[dict, Injection]:
    """Return a faulted copy of the stage-1 output and what was done; `applied` is False if no fact matched."""
    phrase = FAULT_TARGETS[(fault.card, fault.category)]
    faulted = copy.deepcopy(output)
    hits = [fact for fact in faulted["facts"] if is_target_fact(fact, fault.card, phrase)]
    if not hits:
        return output, Injection(False, fault.kind, fault.card, fault.category)
    if fault.kind == "dropped":
        faulted["facts"] = [fact for fact in faulted["facts"] if fact not in hits]
        return faulted, Injection(True, fault.kind, fault.card, fault.category, len(hits))
    # The next card in name order: deterministic and never the true owner.
    wrong_card = cards[(cards.index(fault.card) + 1) % len(cards)]
    for fact in hits:
        fact["card"] = wrong_card
    return faulted, Injection(True, fault.kind, fault.card, fault.category, len(hits), wrong_card)
