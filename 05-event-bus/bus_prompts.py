"""Every prompt the harness sends, and the schemas of the replies it accepts."""

import json
from pathlib import Path

from bus_channel import Message
from bus_config import TEAMS
from topologies.harbour import RISKS, SERVICES, world_files, worker_contract

# Read-only context every consumer gets besides the files of the message's service.
REFERENCE_FILES = ("platform.yaml", "teams.yaml", "libs/platform_config.py")
RISK_IDS = [risk.id for risk in RISKS]
CAUSES = ["no_subscriber", "missing_fields", "wrong_team", "consumer_failed", "consumer_declined", "unknown"]
FIRST_PROBLEMS = ["none", "no_subscriber", "missing_fields", "wrong_team", "consumer_failed", "delivered_twice", "consumer_declined"]

ONE_LINE = {"type": "string", "minLength": 1, "maxLength": 200, "pattern": "^[^\\n]+$"}

CONSUMER_SYSTEM = """You are the agent of {team} at a bike-rental platform. Your team owns: {services}. Messages arrive on a shared channel; each `risk.reported` message reports one operational risk in one service.
Fix the risk this message reports, and only that risk, in the service the message names. If the message is not about a service your team owns or about a file of that service, reply `not_mine`. If you cannot find or cannot fix the reported risk, reply `cannot_fix`. In both cases change no files.
The rules for changing the services (fixes are scored automatically by reading the files; a change outside these rules does not count): {contract}
Return the full new text of every file you change and leave unchanged files out. Reply with JSON only, no prose, matching: {{"outcome": "fixed|not_mine|cannot_fix", "files": [{{"path": "<file path as shown>", "content": "<full new file text>"}}], "reason": "<one line>"}}"""

ACTIONS_EXAMPLE = (
    '{"actions": [{"action": "redeliver|route|assign_owner|return_to_producer", "message_id": "<message id, for every action but assign_owner>", '
    '"service": "<service, for route and assign_owner>", "team": "<team, for assign_owner>", "reason": "<one line>"}]}'
)
OWNER_SYSTEM = """You look after the shared message channel of a bike-rental platform. Every message must reach a team that fixes what it reports. {view} Decide what to do about messages that did not end in a fix. Actions: redeliver a message to its team; route a message to another service (rewrites its service and delivers it to that service's team); assign an owner team to a service that has none (its undelivered messages then go to that team); return a message to the producer (it republishes a valid message for the same risk). You get one round of actions. Reply with JSON only, matching: {schema}"""
STEWARD_VIEW = "Below is everything the channel recorded: each message published, delivered, and the outcome its consumer reported."
PLATFORM_VIEW = (
    "The platform's channel validates every message, retries a failed delivery once and records every hop. Below are only the exceptions: "
    "the Dead Letter Channel (messages no team took, or that a team declined, or that failed twice) and the Invalid Message Channel (messages that broke the schema)."
)
STEWARD_SYSTEM = OWNER_SYSTEM.replace("{view}", STEWARD_VIEW).replace("{schema}", ACTIONS_EXAMPLE)
PLATFORM_AGENT_SYSTEM = OWNER_SYSTEM.replace("{view}", PLATFORM_VIEW).replace("{schema}", ACTIONS_EXAMPLE)

INVESTIGATOR_SYSTEM = """A scanner published one message per operational risk on a shared channel. Below are the records this platform kept about what happened next. For each risk, give three things.
status: handled (a team reported a fix) or lost.
cause: if lost, where it was lost: no_subscriber (no team owns the service), missing_fields (the message lacked the fields needed to act), wrong_team (it reached a team that does not own the risk), consumer_failed (a consumer took it and never reported), consumer_declined (the right team declined it), unknown. If handled, none.
first_problem: the first thing that went wrong with this risk's message, even if it was put right later: none, no_subscriber, missing_fields, wrong_team, consumer_failed, delivered_twice (a consumer was handed the same message twice), consumer_declined (the right team received a complete message and declined it).
Cite the message ids your answer rests on. Use only the records.
Reply with JSON only, matching: {"risks": [{"risk_id": "<id>", "status": "handled|lost", "cause": "none|no_subscriber|missing_fields|wrong_team|consumer_failed|consumer_declined|unknown", "first_problem": "none|no_subscriber|missing_fields|wrong_team|consumer_failed|delivered_twice|consumer_declined", "evidence": ["<message id>"]}]}"""


def consumer_system(team: str, services: list[str]) -> str:
    return CONSUMER_SYSTEM.format(team=team, services=", ".join(services) or "nothing yet", contract=worker_contract())


def service_paths(repo: Path, service: str) -> list[str]:
    return sorted(path for path in world_files(repo) if path.split("/")[0] == service)


def file_block(repo: Path, path: str) -> str:
    return f"=== {path} ===\n{(repo / path).read_text(encoding='utf-8')}"


def consumer_prompt(repo: Path, message: Message) -> str:
    """The message (without trace context), the reference files, then every file of the service it names."""
    service = message.body.get("service")
    parts = [f"Message:\n{json.dumps(message.as_json())}", "Read-only context:"]
    parts += [file_block(repo, path) for path in REFERENCE_FILES]
    if service in SERVICES:
        parts.append(f"Files of {service}:")
        parts += [file_block(repo, path) for path in service_paths(repo, service)]
    else:
        parts.append("The message does not name a known service, so there are no service files to show.")
    return "\n\n".join(parts)


def consumer_schema(paths: list[str]) -> dict:
    files = {"type": "array", "maxItems": 0} if not paths else {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["path", "content"],
            "properties": {"path": {"enum": paths}, "content": {"type": "string", "minLength": 1}},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["outcome", "files", "reason"],
        "properties": {"outcome": {"enum": ["fixed", "not_mine", "cannot_fix"]}, "files": files, "reason": ONE_LINE},
    }


ACTIONS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["actions"],
    "properties": {
        "actions": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "reason"],
                "properties": {
                    "action": {"enum": ["redeliver", "route", "assign_owner", "return_to_producer"]},
                    "message_id": {"type": "string"},
                    "service": {"enum": list(SERVICES)},
                    "team": {"enum": list(TEAMS)},
                    "reason": ONE_LINE,
                },
            },
        }
    },
}

INVESTIGATOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["risks"],
    "properties": {
        "risks": {
            "type": "array",
            "minItems": len(RISK_IDS),
            "maxItems": len(RISK_IDS),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["risk_id", "status", "cause", "first_problem"],
                "properties": {
                    "risk_id": {"enum": RISK_IDS},
                    "status": {"enum": ["handled", "lost"]},
                    "cause": {"enum": ["none", *CAUSES]},
                    "first_problem": {"enum": FIRST_PROBLEMS},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


def subscription_block(subscriptions: dict[str, list[str]]) -> str:
    lines = ["Subscriptions (team: services it receives messages for):"]
    lines += [f"- {team}: {', '.join(services) or 'none'}" for team, services in subscriptions.items()]
    owned = {service for services in subscriptions.values() for service in services}
    lines.append(f"Services no team subscribes to: {', '.join(service for service in SERVICES if service not in owned) or 'none'}")
    return "\n".join(lines)


def json_lines(items: list[dict]) -> str:
    return "\n".join(json.dumps(item) for item in items) or "(none)"


def owner_prompt(entries: list[dict], subscriptions: dict[str, list[str]], teams: dict[str, str]) -> str:
    team_lines = [f"- {name}: {contact}" for name, contact in teams.items()]
    return "\n\n".join([subscription_block(subscriptions), "Teams:\n" + "\n".join(team_lines), "Channel records, oldest first:\n" + json_lines(entries)])


def investigator_prompt(published: list[dict], subscriptions: dict[str, list[str]], records: str) -> str:
    return "\n\n".join([
        "The scanner published (message id, risk, service):\n" + json_lines(published),
        subscription_block(subscriptions),
        records,
    ])
