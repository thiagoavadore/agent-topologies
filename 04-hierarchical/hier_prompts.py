"""Every prompt the harness sends, and the schemas of the replies it accepts.

Worker and platform-lead wording follows folder 03 wherever the role is the same, so the two files can be diffed.
"""

from pathlib import Path

from hier_hub import HTTP_KEYS
from topologies.harbour import HUB_FILE, HUB_KEYS, NEEDS, SERVICES, world_files, worker_contract

# Read-only context every worker gets besides its own service.
REFERENCE_FILES = (HUB_FILE, "teams.yaml", "libs/platform_config.py")

WORKER_REASON_MAX, LEAD_REASON_MAX, NOTES_MAX = 200, 400, 1200
NOTES_ASKED = 1000  # the prompt asks for less than the schema allows
ONE_LINE = {"type": "string", "minLength": 1, "maxLength": WORKER_REASON_MAX, "pattern": "^[^\\n]+$"}
LEAD_REASON = {**ONE_LINE, "maxLength": LEAD_REASON_MAX}
HUB_VALUE = {"anyOf": [ONE_LINE, {"type": "integer"}]}


def hub_change_schema(reason: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["key", "value", "reason"],
        "properties": {"key": {"enum": list(HUB_KEYS)}, "value": HUB_VALUE, "reason": reason},
    }


def worker_schema(paths: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["files", "hub_changes"],
        "properties": {
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "content"],
                    "properties": {"path": {"enum": paths}, "content": {"type": "string", "minLength": 1}},
                },
            },
            "hub_changes": {"type": "array", "items": hub_change_schema(ONE_LINE)},
            "notes": {"type": "string", "maxLength": NOTES_MAX},
        },
    }


SUB_LEAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requests"],
    "properties": {"requests": {"type": "array", "items": hub_change_schema(LEAD_REASON)}},
}

OVERRIDE_GRANT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["service", "key", "value", "reason"],
    "properties": {"service": {"enum": list(SERVICES)}, "key": {"enum": list(HTTP_KEYS)}, "value": ONE_LINE, "reason": LEAD_REASON},
}
GRANTS = {"type": "array", "items": OVERRIDE_GRANT}

TOP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["hub"],
    "properties": {
        "overrides": GRANTS,
        "hub": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "value", "reason"],
                "properties": {"key": {"enum": list(HUB_KEYS)}, "value": ONE_LINE, "reason": LEAD_REASON},
            },
        },
    },
}

MANDATE_RULE = 'Platform mandate: the timeout of the outbound call in `charge()` or `send_email()` must be `http_timeout("<service>")`, imported as in rule 3. Any other form is rejected and your edit to that file is reverted.'
OVERRIDE_RULE = "You may not write `overrides:` in any service.yaml: only the platform lead grants an override, and an override a worker writes is removed."

WORKER_SYSTEM = """You fix operational risks in a service of a bike-rental platform. You own the service listed in the prompt. Other workers fix other services at the same time and you cannot see their work.
You see the platform file platform.yaml, teams.yaml and libs/platform_config.py (read-only context) and every file of your own service.
Find every operational risk in your service (hardcoded secrets, outbound calls that can wait forever, single points of failure, a missing owner, unpinned dependencies, data kept on a disk or VM with no backup, internal surfaces open to the internet, public endpoints with no rate limit) and fix each one. Each service.yaml lists `needs` the service must keep meeting.
Platform defaults live in platform.yaml and every service inherits them.
You may edit your own service files and propose platform values through hub_changes. You may not write `overrides:` in any service.yaml: only the platform lead grants an override, and an override you write is removed.
{mandate}
You cannot edit platform.yaml. Your hub_changes are requests to your lead, who decides what to send to the platform lead. Keep each reason to at most {reason_max} characters.
The rules for changing the services (fixes are scored automatically by reading the files; a change outside these rules does not count):

{contract}

Addendum to rule 2 for this task: do not write `overrides:`; the platform lead may grant one.

Return the full new text of every file you change and leave unchanged files out. Reply with JSON only, no prose, matching:
{{"files": [{{"path": "<file path as shown>", "content": "<full new file text>"}}], "hub_changes": [{{"key": "<hub key>", "value": "<new value>", "reason": "<one line>"}}], "notes": "<optional, what you fixed, at most {notes_asked} characters>"}}"""


def rules_block() -> str:
    """The rules the workers decided under, for every lead, so no tier is judged against rules it never saw."""
    return f"{MANDATE_RULE}\n{OVERRIDE_RULE}\n\nThe rules for changing the services:\n\n{worker_contract()}"


def worker_system() -> str:
    return WORKER_SYSTEM.format(mandate=MANDATE_RULE, contract=worker_contract(), reason_max=WORKER_REASON_MAX, notes_asked=NOTES_ASKED)


SUB_LEAD_SYSTEM = """You lead a group of services of a bike-rental platform: {services} (owner teams: {teams}). Each service has a worker who fixed its operational risks without seeing the other workers. Workers cannot edit platform.yaml; they sent you requests to change it. The platform lead owns platform.yaml, decides every value, and is the only one who can grant a service its own override. Send the platform lead the requests your group stands behind: you may forward, merge, change or drop your workers' requests, or add your own. Each request is one hub key, one value and a one-line reason of at most {reason_max} characters.
The rules the workers followed: {rules}
Reply with JSON only, no prose, matching: {{"requests": [{{"key": "<hub key>", "value": "<value>", "reason": "<one line>"}}]}} (requests may be empty)"""

TOP_SYSTEM = """You own platform.yaml. Workers fixed services in parallel without seeing each other's work and could not edit platform.yaml: they sent change requests. {management}
Decide the final value of every requested key so every service keeps working. Keep each reason to at most {reason_max} characters. You may accept a request, merge several, or keep the current value.
Workers could not write service overrides; you can. If a hub value would break a service or leave its need unmet, you may grant that service its own override (only http.default_timeout or http.default_rate_limit, in that service's service.yaml).{needs_sentence}
Reply with JSON only, no prose, matching: {{"hub": [{{"key": "<hub key>", "value": "<final value>", "reason": "<one line>"}}], "overrides": [{{"service": "<service>", "key": "<http key>", "value": "<value>", "reason": "<one line>"}}]}} (overrides may be empty)"""

MANAGEMENT_FLAT = "You manage the eight service workers directly."
MANAGEMENT_TIERED = "Your sub-leads each lead a group of services (listed); they sent the change requests."
NEEDS_SENTENCE = " The needs of every service with one are listed, including services no worker touched."


def top_system(*, tiered: bool, needs: bool) -> str:
    return TOP_SYSTEM.format(reason_max=LEAD_REASON_MAX, management=MANAGEMENT_TIERED if tiered else MANAGEMENT_FLAT, needs_sentence=NEEDS_SENTENCE if needs else "")


def service_paths(repo: Path, service: str) -> list[str]:
    return sorted(path for path in world_files(repo) if path.split("/")[0] == service)


def file_block(repo: Path, path: str) -> str:
    return f"=== {path} ===\n{(repo / path).read_text(encoding='utf-8')}"


def worker_prompt(repo: Path, service: str) -> str:
    parts = [f"Your service: {service}", "Read-only context:"]
    parts += [file_block(repo, path) for path in REFERENCE_FILES]
    parts.append("Your files:")
    parts += [file_block(repo, path) for path in service_paths(repo, service)]
    return "\n\n".join(parts)


def bounds_text(minimum: str | None, maximum: str | None) -> str:
    if minimum and maximum:
        return f"between {minimum} and {maximum}"
    return f"at least {minimum}" if minimum else f"at most {maximum}"


def needs_block() -> str:
    """What every service with a stated need requires, so a lead can see who a hub change hurts."""
    lines = ["Service needs (every service that states one, including services no worker touched):"]
    for need in NEEDS.values():
        where = f"read from {need.measures}" if need.measures in HUB_KEYS else "the timeout of a call in its code"
        lines.append(f"- {need.service} {need.name}: {bounds_text(need.minimum, need.maximum)} ({where} unless the service has its own override)")
    return "\n".join(lines)


def request_lines(requests: list[dict]) -> list[str]:
    return [f"- {item['key']} = {item['value']!r}. Reason: {item['reason']}" for item in requests] or ["- none"]


def sub_lead_prompt(hub_text: str, pristine: Path, services: list[str], worker_results: list) -> str:
    parts = [f"=== {HUB_FILE} (current) ===\n{hub_text}", "Your services:"]
    parts += [file_block(pristine, f"{service}/service.yaml") for service in services]
    parts.append("Your workers' reports:")
    for result in worker_results:
        lines = [f"Worker for {result.service} ({result.status}). Hub change requests:", *request_lines(result.hub_requests)]
        lines.append(f"Notes: {result.notes or 'none'}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def top_prompt(hub_text: str, requests_up: list[dict], groups: dict[str, list[str]], needs: bool) -> str:
    parts = [f"=== {HUB_FILE} (current) ===\n{hub_text}", rules_block()]
    if groups:
        parts.append("Groups:\n" + "\n".join(f"- {name}: {', '.join(services)}" for name, services in groups.items()))
    else:
        parts.append(f"Services you manage directly: {', '.join(SERVICES)}")
    if needs:
        parts.append(needs_block())
    requests = "\n".join(
        f"- {item['from']} ({', '.join(item['services'])}) asks {item['key']} = {item['value']!r}. Reason: {item['reason']}" for item in requests_up
    )
    parts.append(f"Change requests:\n{requests}")
    return "\n\n".join(parts)
