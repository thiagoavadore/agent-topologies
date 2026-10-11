"""Import-safe setup and scripted model replies for the folder-05 tests.

Other folders also have modules named `experiment` and `run`, so importing this module first puts this folder at
the front of `sys.path` and drops any same-named module cached from another folder.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

FOLDER = Path(__file__).resolve().parents[1]
ROOT = FOLDER.parent
MODULES = ("experiment", "run", "bus", "bus_config", "bus_faults", "bus_channel", "bus_prompts", "bus_owner", "bus_trace")


def _isolate() -> None:
    for name in MODULES:
        cached = sys.modules.get(name)
        if cached is not None and Path(getattr(cached, "__file__", "") or "").resolve().parent != FOLDER:
            del sys.modules[name]
    for path in (str(ROOT / "tests"), str(FOLDER)):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


_isolate()

from harbour_fixes import FIXES  # noqa: E402

from topologies.harbour import world_files  # noqa: E402
from topologies.model import ScriptedBackend  # noqa: E402

ENTRIES_HEADER = "Channel records, oldest first:\n"


def consumer_fix(repo: Path, message: dict, owned: list[str]) -> dict:
    """The reply of a consumer that applies the reference fix for the reported risk on top of the repo as it is now."""
    if not all(key in message for key in ("category", "file", "locator")):
        return {"outcome": "cannot_fix", "files": [], "reason": "the message lacks the fields needed to act"}
    service = message["service"]
    if service not in owned or not message["file"].startswith(service + "/"):
        return {"outcome": "not_mine", "files": [], "reason": f"{message['file']} is not a file of {service}"}
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "repo"
        shutil.copytree(repo, copy)
        try:
            FIXES[f"{service}.{message['category']}"](copy)
        except AssertionError:  # the text the fix replaces is gone: already fixed
            return {"outcome": "fixed", "files": [], "reason": "already fixed"}
        files = [
            {"path": path, "content": (copy / path).read_text()}
            for path in sorted(world_files(copy))
            if path.split("/")[0] == service and (copy / path).read_text() != (repo / path).read_text()
        ]
    return {"outcome": "fixed", "files": files, "reason": f"applied the fix for {message['category']}"}


def smart_actions(entries: list[dict]) -> list[dict]:
    """An owner that repairs everything it is shown: assign unowned services, route declines, return bad messages, redeliver silence."""
    bodies = {item["message_id"]: item["body"] for item in entries if "body" in item}
    actions, assigned = [], set()
    for item in entries:
        kind, reason, message_id = item.get("event") or item.get("channel"), item.get("reason", ""), item["message_id"]
        service = item.get("service") or item.get("body", {}).get("service")
        if kind in ("discarded", "dead_letter") and "no subscriber" in reason:
            if service not in assigned:
                assigned.add(service)
                actions.append({"action": "assign_owner", "service": service, "team": "Team Fleet", "reason": "nobody owns it"})
        elif "not_mine" in (item.get("outcome"), reason) or (kind == "dead_letter" and "replied not_mine" in reason):
            actions.append({"action": "route", "message_id": message_id, "service": bodies[message_id]["file"].split("/")[0], "reason": "the file names the service"})
        elif item.get("outcome") == "cannot_fix" or kind == "invalid" or "replied cannot_fix" in reason:
            actions.append({"action": "return_to_producer", "message_id": message_id, "reason": "ask the scanner again"})
        elif kind == "no_reply":
            actions.append({"action": "redeliver", "message_id": message_id, "reason": "no outcome was reported"})
    return actions


def literal_first_problem(item: dict, mine: list[dict], subscriptions: str) -> str:
    """The first problem a record shows outright: a body that is short or names another service, silence, a repeat."""
    receipts = [record for record in mine if record.get("event") in ("received", "delivered")]
    bodies = [record["body"] for record in mine if "body" in record]
    if item["service"] in subscriptions.splitlines()[-1]:
        return "no_subscriber"
    if any("file" not in body for body in bodies) or any(record.get("event") == "invalid" for record in mine):
        return "missing_fields"
    if any(body["service"] != item["service"] for body in bodies):
        return "wrong_team"
    outcomes = [record for record in mine if record.get("outcome")]
    if any(record.get("event") == "no_reply" for record in mine) or (receipts and "received" in {r["event"] for r in receipts} and len(outcomes) < len(receipts)):
        return "consumer_failed"
    if len([r for r in receipts if not r.get("history")]) >= 2 or any("acknowledged" in record.get("reason", "") for record in mine):
        return "delivered_twice"
    return "none"


def literal_investigator(prompt: str) -> dict:
    """An investigator that trusts only what a record says outright; it cannot tell why a local log shows a decline."""
    blocks = prompt.split("\n\n")
    ledger = [json.loads(line) for line in blocks[0].splitlines()[1:]]
    records = [json.loads(line) for block in blocks[2:] for line in block.splitlines() if line.startswith("{")]
    risks = []
    for item in ledger:
        mine = [record for record in records if record.get("message_id") == item["message_id"]]
        reasons = [record.get("reason", "") for record in mine]
        events = [record.get("event") for record in mine]
        if any(record.get("outcome") == "fixed" for record in mine):
            status, cause = "handled", "none"
        elif "invalid" in events:
            status, cause = "lost", "missing_fields"
        elif any("no subscriber" in reason for reason in reasons):
            status, cause = "lost", "no_subscriber"
        elif any("replied not_mine" in reason for reason in reasons):
            status, cause = "lost", "wrong_team"
        elif any(record.get("outcome") in ("not_mine", "cannot_fix") for record in mine):
            status, cause = "lost", "consumer_declined"
        elif "received" in events or "delivered" in events:
            status, cause = "lost", "consumer_failed"
        else:
            status, cause = "lost", "unknown"
        risks.append({"risk_id": item["risk_id"], "status": status, "cause": cause, "first_problem": literal_first_problem(item, mine, blocks[1]), "evidence": [item["message_id"]]})
    return {"risks": risks}


class Script:
    """Scripted replies for every role. `owner` is a function from the entries the owner was shown to its actions."""

    def __init__(self, repo: Path, owner=None, investigator=literal_investigator, consumer=consumer_fix) -> None:
        self.repo, self.owner, self.investigator, self.consumer = repo, owner or (lambda entries: []), investigator, consumer
        self.prompts: list[tuple[str, str, str]] = []  # (role, system, prompt)

    @staticmethod
    def role_of(system: str) -> str:
        if system.startswith("You are the agent of"):
            return "consumer"
        if "Below is everything the channel recorded" in system:
            return "steward"
        if "Below are only the exceptions" in system:
            return "platform-agent"
        if system.startswith("A scanner published"):
            return "investigator"
        raise AssertionError(f"unknown system prompt: {system[:80]}")

    def __call__(self, system: str, prompt: str) -> str:
        role = self.role_of(system)
        self.prompts.append((role, system, prompt))
        if role == "consumer":
            team = system.removeprefix("You are the agent of ").split(" at a bike-rental")[0]
            owned = system.split("Your team owns: ")[1].split(". Messages")[0].split(", ")
            message = json.loads(prompt.splitlines()[1])
            reply = self.consumer(self.repo, message, owned)
            self.last_team = team
            return json.dumps(reply)
        if role in ("steward", "platform-agent"):
            entries = [json.loads(line) for line in prompt.split(ENTRIES_HEADER)[1].splitlines() if line.startswith("{")]
            return json.dumps({"actions": self.owner(entries)})
        return json.dumps(self.investigator(prompt))

    def prompts_for(self, role: str) -> list[str]:
        return [prompt for found, _, prompt in self.prompts if found == role]

    def backend(self) -> ScriptedBackend:
        return ScriptedBackend(self)
