"""Shared test data: the baseline repo, one fixing edit per planted risk, and a scripted model."""

import json
from pathlib import Path

from fanout import FIXTURES, HUB_OWNER_SYSTEM, SUMMARY_SYSTEM, SUPERVISOR_SYSTEM
from hubfile import parse_hub

BASE_CARDS = {path.name: path.read_text() for path in sorted((FIXTURES / "services").glob("*.md"))}
BASE_HUB = parse_hub((FIXTURES / "platform.yaml").read_text())

# (card, category, old text, new text): each edit alone fixes exactly its planted risk.
FIXES = [
    ("bookings-api.md", "missing_timeout", 'json=booking.to_payment())', 'json=booking.to_payment(), timeout=5)'),
    ("bookings-api.md", "no_rate_limit", "Public endpoints accept any volume of requests per client; we scale out when the load balancer reports high CPU.", "Public endpoints accept at most 10 requests per second per client."),
    ("payments-gateway.md", "hardcoded_secret", "api_key: cp_live_9f2c8e71d4a04b6fa3e1c55d0b8a2f17", "api_key: ${PAYMENTS_API_KEY}"),
    ("fleet-telemetry.md", "single_point_of_failure", "one ingest process on a single VM (`telemetry-01`)", "two ingest processes on two VMs (`telemetry-01`, `telemetry-02`)"),
    ("fleet-telemetry.md", "no_owner", "Owner:\n", "Owner: Team Fleet (fleet@harbourbikes.example)\n"),
    ("customer-profiles.md", "missing_backup", "No snapshot schedule or dump job is configured yet; it was on the migration plan for Q3.", "Nightly snapshots go to object storage with 30-day retention."),
    ("pricing-engine.md", "unpinned_dependency", '"express": "latest",\n    "decimal.js": "*",\n    "demand-model-client": "^2.0.0",', '"express": "4.21.1",\n    "decimal.js": "10.4.3",\n    "demand-model-client": "2.0.3",'),
    ("admin-console.md", "public_exposure", '"0.0.0.0/0"', '"10.0.0.0/8"'),
    ("notifications.md", "missing_timeout", "smtplib.SMTP(SMTP_HOST, 587)", "smtplib.SMTP(SMTP_HOST, 587, timeout=10)"),
    ("notifications.md", "unpinned_dependency", "celery\nredis\njinja2==3.1.4\nfirebase-admin>=6", "celery==5.4.0\nredis==5.2.0\njinja2==3.1.4\nfirebase-admin==6.6.0"),
    ("maintenance-scheduler.md", "single_point_of_failure", "Single VM, single crontab entry.", "Two VMs (`ops-cron-01`, `ops-cron-02`), the second one on standby."),
    ("maintenance-scheduler.md", "missing_backup", "it is not copied anywhere else.", "it is backed up nightly to object storage."),
]


def edited(cards: dict[str, str], card: str, old: str, new: str) -> dict[str, str]:
    assert old in cards[card], f"{old!r} not in {card}"
    return {**cards, card: cards[card].replace(old, new)}


def all_fixes_applied() -> dict[str, str]:
    cards = dict(BASE_CARDS)
    for card, _, old, new in FIXES:
        cards = edited(cards, card, old, new)
    return cards


def card_worker_payload(cards: list[str], hub_changes: list[dict] | None = None) -> str:
    """A worker reply that applies this module's FIXES to `cards`."""
    files = []
    for name in cards:
        text = BASE_CARDS[name]
        for card, _, old, new in FIXES:
            if card == name:
                text = text.replace(old, new)
        if text != BASE_CARDS[name]:
            files.append({"path": name, "content": text})
    return json.dumps({"files": files, "hub_changes": hub_changes or []})


def hub_change(key: str, value: str, reason: str = "needs it") -> dict:
    return {"key": key, "value": value, "reason": reason}


class Script:
    """Scripted model: one reply per role. Worker replies are keyed by the first card in the prompt."""

    def __init__(self, workers: dict[str, str], supervisor: str = '{"value": "300ms", "reason": "keeps pricing"}',
                 hub_owner: str = '{"hub": []}', summary: str = "Merged.") -> None:
        self.workers, self.supervisor, self.hub_owner, self.summary = workers, supervisor, hub_owner, summary
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system: str, prompt: str) -> str:
        roles = {SUPERVISOR_SYSTEM: "supervisor", HUB_OWNER_SYSTEM: "hub-owner", SUMMARY_SYSTEM: "summary"}
        role = roles.get(system, "worker")
        self.calls.append((role, prompt))
        if role == "worker":
            first_card = prompt.split("=== ")[2].split(" ===")[0]
            reply = self.workers[first_card]
            if isinstance(reply, Exception):
                raise reply
            return reply
        return {"supervisor": self.supervisor, "hub-owner": self.hub_owner, "summary": self.summary}[role]
