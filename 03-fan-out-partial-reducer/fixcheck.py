"""Deterministic checker: which planted risks the merged repo fixed, and which card needs it broke.

Input is the merged cards (file name -> text) and the merged hub (key -> value). Every rule is
listed in CHECKER_RULES.md; no model is involved.
"""

import json
import re
from pathlib import Path

PLANTED_PATH = Path(__file__).parent / "fixtures" / "planted.json"

DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds?|m|min|mins|minutes?)$", re.I)
RATE = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:req(?:uests?)?\s*)?(?:/|per\s+)\s*(s|sec|second|m|min|minute)$", re.I)
PER_UNIT_SECONDS = {"ms": 0.001, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
                    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60}

PRICING_MAX_TIMEOUT_SECONDS = 0.3
PAYMENTS_MIN_RATE_PER_SECOND = 20

SCHEDULE_WORDS = r"(?:daily|nightly|hourly|weekly|every\s+\d+\s*(?:h|hours?|days?))"
BACKUP_WORDS = r"(?:snapshots?|dumps?|back(?:ups?|ed up|s up)|pg_dump)"
BACKUP_SCHEDULE = re.compile(
    rf"\b{SCHEDULE_WORDS}\b[^.\n]{{0,40}}\b{BACKUP_WORDS}|\b{BACKUP_WORDS}\w*\b[^.\n]{{0,40}}\b{SCHEDULE_WORDS}", re.I
)
REDUNDANCY_UNITS = r"(?:instances|replicas|vms|nodes|brokers|schedulers|hosts)"
REDUNDANCY = re.compile(
    rf"\b(?:[2-9]|two|three|four)\s+(?:\w+\s+){{0,2}}{REDUNDANCY_UNITS}\b"
    rf"|(?<!no )\bsecond\s+(?:vm|instance|scheduler|node|host)\b"
    rf"|(?<!no )\b(?:standby|failover|fail-over)\b",
    re.I,
)
EXACT_REQUIREMENT = re.compile(r"^[A-Za-z0-9_.\-\[\]]+==[A-Za-z0-9_.+\-]+$")
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
PLACEHOLDER_OWNER = r"(?:tbd|unknown|none|n/a)"


def parse_duration(text: str) -> float | None:
    match = DURATION.match(text.strip())
    return float(match[1]) * PER_UNIT_SECONDS[match[2].lower()] if match else None


def parse_rate(text: str) -> float | None:
    """Requests per second, or None if the text is not a rate."""
    match = RATE.match(text.strip())
    return float(match[1]) / PER_UNIT_SECONDS[match[2].lower()] if match else None


def section(card: str, heading: str) -> str:
    """Text under `## heading` up to the next `## `; the whole card if a worker renamed the heading."""
    match = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", card, re.S | re.M | re.I)
    return match[1] if match else card


def code_block(text: str) -> str | None:
    match = re.search(r"```[a-z]*\n(.*?)```", text, re.S)
    return match[1] if match else None


def has_timeout(text: str, hub: dict[str, str]) -> bool:
    """A timeout argument, or a reference to the hub key whose value is a real duration."""
    if "http.default_timeout" in text:
        return (parse_duration(hub["http.default_timeout"]) or 0) > 0
    return bool(re.search(r"timeout\s*=\s*(?!None\b)\S", text))


def has_rate_limit(text: str, hub: dict[str, str]) -> bool:
    if re.search(r"\d+(?:\.\d+)?\s*(?:req(?:uests?)?\s*)?(?:/|per\s+)\s*(?:s|sec|second|m|min|minute)\b", text, re.I):
        return True
    return "http.default_rate_limit" in text and parse_rate(hub["http.default_rate_limit"]) is not None


def has_backup(text: str, hub: dict[str, str]) -> bool:
    if BACKUP_SCHEDULE.search(text):
        return True
    return "backup.policy" in text and hub["backup.policy"].lower() not in ("", "none")


def has_redundancy(text: str, hub: dict[str, str]) -> bool:
    if REDUNDANCY.search(text):
        return True
    minimum = hub["availability.min_instances"]
    return "availability.min_instances" in text and minimum.isdigit() and int(minimum) >= 2


def has_owner(card: str) -> bool:
    return bool(re.search(rf"^Owner:[ \t]*(?!{PLACEHOLDER_OWNER}\b)\S", card, re.I | re.M))


def pinned_by_hub(card: str, hub: dict[str, str]) -> bool:
    return "dependencies.pinning" in card and hub["dependencies.pinning"].lower() in ("exact", "lockfile")


def npm_versions_exact(card: str) -> bool:
    block = code_block(section(card, "package.json"))
    try:
        dependencies = json.loads(block or "")["dependencies"]
    except (ValueError, KeyError, TypeError):
        return False
    return bool(dependencies) and all(EXACT_VERSION.match(str(version)) for version in dependencies.values())


def pip_requirements_exact(card: str) -> bool:
    block = code_block(section(card, "requirements.txt"))
    lines = [line.strip() for line in (block or "").splitlines() if line.strip()]
    return bool(lines) and all(EXACT_REQUIREMENT.match(line) for line in lines)


def effective(card: str, hub: dict[str, str], key: str) -> str:
    """The card's own `Override: <key> = <value>` line if it has one, else the hub value."""
    match = re.search(rf"^Override:\s*{re.escape(key)}\s*=\s*(.+)$", card, re.M)
    return match[1].strip() if match else hub[key]


def admin_console_not_public(card: str) -> bool:
    """Fixed when no `0.0.0.0/0` ingress remains, or the card says SSO or a VPN guards the console."""
    guarded = re.search(r"\b(?:behind|requires?|via|through|enforces?)\b[^.\n]{0,30}\b(?:SSO|VPN|single sign-on)\b", card, re.I)
    return "0.0.0.0/0" not in card or bool(guarded)


# One rule per planted risk, keyed (card, category): True means the merged repo fixed it.
RISK_RULES = {
    ("bookings-api.md", "missing_timeout"): lambda card, hub: has_timeout(section(card, "Payment call"), hub),
    ("bookings-api.md", "no_rate_limit"): lambda card, hub: has_rate_limit(section(card, "Endpoints"), hub),
    ("payments-gateway.md", "hardcoded_secret"): lambda card, hub: not re.search(r"cp_live_[0-9a-f]{8,}", card),
    ("fleet-telemetry.md", "single_point_of_failure"): lambda card, hub: has_redundancy(card, hub),
    ("fleet-telemetry.md", "no_owner"): lambda card, hub: has_owner(card),
    ("customer-profiles.md", "missing_backup"): lambda card, hub: has_backup(section(card, "Data"), hub),
    ("pricing-engine.md", "unpinned_dependency"): lambda card, hub: npm_versions_exact(card) or pinned_by_hub(card, hub),
    ("admin-console.md", "public_exposure"): lambda card, hub: admin_console_not_public(card),
    ("notifications.md", "missing_timeout"): lambda card, hub: has_timeout(section(card, "Email sending"), hub),
    ("notifications.md", "unpinned_dependency"): lambda card, hub: pip_requirements_exact(card) or pinned_by_hub(card, hub),
    ("maintenance-scheduler.md", "single_point_of_failure"): lambda card, hub: has_redundancy(card, hub),
    ("maintenance-scheduler.md", "missing_backup"): lambda card, hub: has_backup(section(card, "Data"), hub),
}


def pricing_keeps_300ms(cards: dict[str, str], hub: dict[str, str]) -> bool:
    seconds = parse_duration(effective(cards["pricing-engine.md"], hub, "http.default_timeout"))
    return seconds is not None and seconds <= PRICING_MAX_TIMEOUT_SECONDS


def payments_keeps_20_per_second(cards: dict[str, str], hub: dict[str, str]) -> bool:
    value = effective(cards["payments-gateway.md"], hub, "http.default_rate_limit")
    if value.lower() == "none":
        return True
    rate = parse_rate(value)
    return rate is not None and rate >= PAYMENTS_MIN_RATE_PER_SECOND


# One rule per planted card need, keyed by name: True means the need still holds.
NEED_RULES = {
    "pricing-engine:demand-model-300ms": pricing_keeps_300ms,
    "payments-gateway:20-per-second": payments_keeps_20_per_second,
}


def load_planted(path: Path = PLANTED_PATH) -> set[tuple[str, str]]:
    planted = {(item["file"], item["category"]) for item in json.loads(path.read_text())["planted"]}
    if planted != set(RISK_RULES):
        raise ValueError(f"planted.json and RISK_RULES disagree: {sorted(planted ^ set(RISK_RULES))}")
    return planted


def check(cards: dict[str, str], hub: dict[str, str]) -> dict:
    results = {f"{card}:{category}": rule(cards[card], hub) for (card, category), rule in RISK_RULES.items()}
    regressed = [name for name, still_holds in NEED_RULES.items() if not still_holds(cards, hub)]
    return {
        "risks_total": len(results),
        "risks_fixed": sum(results.values()),
        "fixed": sorted(name for name, fixed in results.items() if fixed),
        "unfixed": sorted(name for name, fixed in results.items() if not fixed),
        "regressed": sorted(regressed),
    }


def load_repo(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    from hubfile import parse_hub

    cards = {path.name: path.read_text() for path in sorted(root.glob("*.md"))}
    return cards, parse_hub((root / "platform.yaml").read_text())
