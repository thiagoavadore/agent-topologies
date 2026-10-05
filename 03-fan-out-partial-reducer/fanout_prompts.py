"""Every prompt the harness sends, and the schemas of the replies it accepts."""

from pathlib import Path

from fanout_config import OVERRIDES_ALLOWED_ARM, WORKERS
from fanout_overrides import HTTP_KEYS
from topologies.harbour import HUB_FILE, HUB_KEYS, NEED_MEASURES, NEEDS, SERVICES, world_files, worker_contract

# Read-only context every worker gets besides its own services.
REFERENCE_FILES = (HUB_FILE, "teams.yaml", "libs/platform_config.py")

ONE_LINE = {"type": "string", "minLength": 1, "maxLength": 200, "pattern": "^[^\\n]+$"}
HUB_VALUE = {"anyOf": [ONE_LINE, {"type": "integer"}]}


def hub_change_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["key", "value", "reason"],
        "properties": {"key": {"enum": list(HUB_KEYS)}, "value": HUB_VALUE, "reason": ONE_LINE},
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
            "hub_changes": {"type": "array", "items": hub_change_schema()},
            "notes": {"type": "string", "maxLength": 600},
        },
    }


OVERRIDE_GRANT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["service", "key", "value", "reason"],
    "properties": {"service": {"enum": list(SERVICES)}, "key": {"enum": list(HTTP_KEYS)}, "value": ONE_LINE, "reason": ONE_LINE},
}
GRANTS = {"type": "array", "items": OVERRIDE_GRANT}

CONFLICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["value", "reason"],
    "properties": {"value": ONE_LINE, "reason": ONE_LINE, "overrides": GRANTS},
}

HUB_OWNER_SCHEMA = {
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
                "properties": {"key": {"enum": list(HUB_KEYS)}, "value": ONE_LINE, "reason": ONE_LINE},
            },
        }
    },
}

WORKER_SYSTEM = """You fix operational risks in services of a bike-rental platform. You own the services listed in the prompt. Other workers fix other services at the same time and you cannot see their work.
You see the platform file platform.yaml, teams.yaml and libs/platform_config.py (read-only context) and every file of your own services.
Find every operational risk in your services (hardcoded secrets, outbound calls that can wait forever, single points of failure, a missing owner, unpinned dependencies, data kept on a disk or VM with no backup, internal surfaces open to the internet, public endpoints with no rate limit) and fix each one. Each service.yaml lists `needs` the service must keep meeting.
Platform defaults live in platform.yaml and every service inherits them.
{override_rule}
{hub_rule}
The rules for changing the services (fixes are scored automatically by reading the files; a change outside these rules does not count):

{contract}

{addendum}Return the full new text of every file you change and leave unchanged files out. Reply with JSON only, no prose, matching:
{{"files": [{{"path": "<file path as shown>", "content": "<full new file text>"}}], "hub_changes": [{{"key": "<hub key>", "value": "<new value>", "reason": "<one line>"}}], "notes": "<optional, what you fixed>"}}"""

OVERRIDE_RULE_ALLOWED = "You may edit your own service files, propose platform values through hub_changes, and write `overrides:` in your own service.yaml."
OVERRIDE_RULE_OWNED = "You may edit your own service files and propose platform values through hub_changes. You may not write `overrides:` in any service.yaml: only the merge owner grants an override, and an override you write is removed."
ADDENDUM_OWNED = "Addendum to rule 2 for this task: do not write `overrides:`; the merge owner may grant one.\n\n"

HUB_RULE_OWN_COPY = "Your hub_changes are written to platform.yaml in your own copy of the repo."
HUB_RULE_REQUESTS = "You cannot edit platform.yaml. Your hub_changes are requests to the hub owner, who decides the final values."

SUPERVISOR_SYSTEM = """You own the merge of a shared platform file after workers fixed services in parallel without seeing each other's work.
Two or more workers set the same hub key to different values. Pick the final value for that key so every service keeps working. You may pick one worker's value or write a different one.
Workers could not write service overrides; you can. If the hub value would break a service or leave its need unmet, you may grant that service its own override (only http.default_timeout or http.default_rate_limit, in that service's service.yaml). The needs of every service with one are listed, including services no worker touched.
Reply with JSON only, no prose, matching: {"value": "<final value>", "reason": "<one line>", "overrides": [{"service": "<service>", "key": "<http key>", "value": "<value>", "reason": "<one line>"}]} (overrides may be empty)"""

HUB_OWNER_SYSTEM = """You own platform.yaml. Workers fixed services in parallel without seeing each other's work and could not edit platform.yaml: they sent change requests.
Decide the final value of every requested key so every service keeps working. You may accept a request, merge several, or keep the current value.
Workers could not write service overrides; you can. If a hub value would break a service or leave its need unmet, you may grant that service its own override (only http.default_timeout or http.default_rate_limit, in that service's service.yaml). The needs of every service with one are listed, including services no worker touched.
Reply with JSON only, no prose, matching: {"hub": [{"key": "<hub key>", "value": "<final value>", "reason": "<one line>"}], "overrides": [{"service": "<service>", "key": "<http key>", "value": "<value>", "reason": "<one line>"}]} (overrides may be empty)"""

REPORT_SYSTEM = """You own the merge of a parallel fix across services. Write the merge report for the platform lead in at most 150 words: what the merged repo now contains, which hub values were decided and why, and anything the lead should know."""


def worker_system(arm: str) -> str:
    hub_rule = HUB_RULE_REQUESTS if arm == "hub-owner" else HUB_RULE_OWN_COPY
    owned = arm != OVERRIDES_ALLOWED_ARM
    return WORKER_SYSTEM.format(
        override_rule=OVERRIDE_RULE_OWNED if owned else OVERRIDE_RULE_ALLOWED,
        hub_rule=hub_rule,
        contract=worker_contract(),
        addendum=ADDENDUM_OWNED if owned else "",
    )


def service_paths(repo: Path, services: list[str]) -> list[str]:
    return sorted(path for path in world_files(repo) if path.split("/")[0] in services)


def file_block(repo: Path, path: str) -> str:
    return f"=== {path} ===\n{(repo / path).read_text(encoding='utf-8')}"


def worker_prompt(repo: Path, worker: str) -> str:
    services = WORKERS[worker]
    parts = [f"Your services: {', '.join(services)}", "Read-only context:"]
    parts += [file_block(repo, path) for path in REFERENCE_FILES]
    parts.append("Your files:")
    parts += [file_block(repo, path) for path in service_paths(repo, services)]
    return "\n\n".join(parts)


def bounds_text(minimum: str | None, maximum: str | None) -> str:
    if minimum and maximum:
        return f"between {minimum} and {maximum}"
    return f"at least {minimum}" if minimum else f"at most {maximum}"


def needs_block() -> str:
    """What every service with a stated need requires, so a reducer or human can see who a hub change hurts."""
    lines = ["Service needs (every service that states one, including services no worker touched):"]
    for need in NEEDS.values():
        where = f"read from {need.measures}" if need.measures in HUB_KEYS else "the timeout of a call in its code"
        lines.append(f"- {need.service} {need.name}: {bounds_text(need.minimum, need.maximum)} ({where} unless the service has its own override)")
    return "\n".join(lines)


def conflict_lines(key: str, base_value: str, options: list[dict]) -> list[str]:
    lines = [f"Hub key in conflict: {key} (current value: {base_value})"]
    for option in options:
        owners = "; ".join(f"{worker} (services: {', '.join(WORKERS[worker])})" for worker in option["workers"])
        lines.append(f"- value {option['value']!r} from {owners}. Reason: {' / '.join(option['reasons'])}")
    return lines


def conflict_prompt(hub_text: str, key: str, base_value: str, options: list[dict]) -> str:
    return f"=== {HUB_FILE} (current) ===\n{hub_text}\n\n{needs_block()}\n\n" + "\n".join(conflict_lines(key, base_value, options))


def hub_owner_prompt(hub_text: str, changes: list) -> str:
    requests = "\n".join(
        f"- {change.worker} (services: {', '.join(WORKERS[change.worker])}) asks {change.key} = {change.value!r}. Reason: {change.reason}"
        for change in changes
    )
    return f"=== {HUB_FILE} (current) ===\n{hub_text}\n\n{needs_block()}\n\nChange requests:\n{requests}"


def report_prompt(runs: list, base_hub: dict[str, str], final_hub: dict[str, str], resolutions: list[dict], grants: list[dict]) -> str:
    lines = ["Workers:"]
    for run in runs:
        if run.failed:
            lines.append(f"- {run.worker} ({', '.join(run.services)}): returned nothing usable ({run.status}); its services were not changed")
        else:
            lines.append(f"- {run.worker} ({', '.join(run.services)}): returned a fix")
    lines.append("Hub keys the workers asked to change, and what the merged platform.yaml says now:")
    for item in resolutions:
        wanted = "; ".join(
            f"{', '.join(option['workers'])} wanted {option['value']} ({' / '.join(option['reasons'])})" for option in item["options"]
        )
        status = "contested" if item["contested"] else "uncontested"
        lines.append(f"- {item['key']}: {base_hub[item['key']]} -> {final_hub[item['key']]} [{status}, decided by {item['decided_by']}]. {wanted}")
    if not resolutions:
        lines.append("- none")
    lines.append("Service overrides the merge owner granted:")
    lines += [f"- {item['service']}: {item['key']} = {item['value']} ({item['reason']})" for item in grants] or ["- none"]
    stripped = [f"{run.worker}: {edit['service']} {edit['key']} = {edit['value']}" for run in runs for edit in run.override_edits if run.overrides_stripped]
    lines.append("Overrides workers wrote that were removed (workers may not write overrides):")
    lines += [f"- {item}" for item in stripped] or ["- none"]
    return "\n".join(lines)
