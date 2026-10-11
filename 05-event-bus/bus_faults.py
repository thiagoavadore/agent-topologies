"""The scanner (producer) and the faults the harness injects on the bus, aimed at catalogue risks by id."""

import random

from bus_config import FAULT_KINDS, FAULT_OFFSETS, OWNER, ROUTABLE
from topologies.harbour import RISKS, SERVICES


def faults_for_run(index: int) -> dict[str, str]:
    """Fault kind -> target risk id for 0-based run `index`; the same in every arm."""
    faults = {kind: ROUTABLE[(index + FAULT_OFFSETS[kind]) % len(ROUTABLE)] for kind in FAULT_KINDS}
    assert len(set(faults.values())) == len(FAULT_KINDS), faults
    return faults


def wrong_destination(service: str) -> str:
    """The next service in SERVICES order (wrapping) that is not fleet-telemetry and belongs to another team."""
    start = SERVICES.index(service)
    for step in range(1, len(SERVICES)):
        candidate = SERVICES[(start + step) % len(SERVICES)]
        if candidate != "fleet-telemetry" and OWNER[candidate] != OWNER[service]:
            return candidate
    raise ValueError(f"no wrong destination for {service}")


def body_of(risk) -> dict:
    return {"type": "risk.reported", "service": risk.service, "category": risk.category, "file": risk.file, "locator": risk.locator}


def scanner_messages(index: int) -> tuple[list[dict], dict[str, str]]:
    """One `risk.reported` per catalogue risk, shuffled per run; returns the messages and message id -> risk id."""
    risks = list(RISKS)
    random.Random(index).shuffle(risks)
    messages = [{"message_id": f"m{position:02d}", "risk_id": risk.id, "body": body_of(risk)} for position, risk in enumerate(risks, start=1)]
    return messages, {item["message_id"]: item["risk_id"] for item in messages}


def malform(body: dict) -> dict:
    return {key: value for key, value in body.items() if key not in ("category", "file", "locator")}


def misroute(body: dict) -> dict:
    return {**body, "service": wrong_destination(body["service"])}


def valid_copy(risk_id: str) -> dict:
    """The body the scanner would publish for this risk if asked again."""
    return body_of(next(risk for risk in RISKS if risk.id == risk_id))
