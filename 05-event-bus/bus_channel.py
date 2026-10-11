"""An in-process Publish-Subscribe Channel whose guarantees are switched on or off by the arm's policy."""

import threading
from dataclasses import dataclass, field

import jsonschema

from bus_config import Policy
from topologies.harbour import FIXTURE, RISKS, SERVICES, world_files

CATEGORIES = sorted({risk.category for risk in RISKS})
# The Datatype Channel. It does not check that `file` sits under `service`, so a wrong-service message passes by design.
DATATYPE_SCHEMA = {
    "type": "object",
    "required": ["message_id", "type", "service", "category", "file", "locator"],
    "properties": {
        "message_id": {"type": "string", "minLength": 1},
        "type": {"const": "risk.reported"},
        "service": {"enum": list(SERVICES)},
        "category": {"enum": CATEGORIES},
        "file": {"enum": sorted(world_files(FIXTURE))},
        "locator": {"type": "string", "minLength": 1},
    },
}


@dataclass
class Message:
    message_id: str
    body: dict
    correlation_id: str = ""  # the id of the message this one is a copy of; only set when the arm carries trace context
    history: list[str] = field(default_factory=list)  # the hops that produced this copy

    def as_json(self) -> dict:
        """What a consumer sees: the id and the body, never the trace context."""
        return {"message_id": self.message_id, **self.body}


@dataclass
class Job:
    message: Message
    attempt: int = 1
    duplicate: bool = False  # the transport delivered it again


class Bus:
    """Queues per team, a Message Store, local team logs, a Dead Letter Channel and an Invalid Message Channel."""

    def __init__(self, policy: Policy, subscriptions: dict[str, list[str]], risk_of: dict[str, str] | None = None) -> None:
        self.policy = policy
        self.subscriptions = {team: list(services) for team, services in subscriptions.items()}
        self.assignments: dict[str, str] = {}  # service -> team, made by an owner's assign_owner action
        self.risk_of = risk_of if risk_of is not None else {}
        self.messages: dict[str, Message] = {}  # the first message published under each id
        self.queues: dict[str, list[Job]] = {team: [] for team in subscriptions}
        self.store: list[dict] = []
        self.team_logs: dict[str, list[dict]] = {team: [] for team in subscriptions}
        self.dead_letters: list[dict] = []
        self.invalid: list[dict] = []
        self.undelivered: list[str] = []  # ids that found no subscriber when published
        self.action_log: list[dict] = []
        self._acked: set[tuple[str, str]] = set()
        self._copies = 0
        self.lock = threading.RLock()

    def route(self, service: str | None) -> str | None:
        """The team that receives messages about `service`: its subscriber, or the team an owner assigned."""
        if service in self.assignments:
            return self.assignments[service]
        return next((team for team, services in self.subscriptions.items() if service in services), None)

    def services_of(self, team: str) -> list[str]:
        return sorted({*self.subscriptions[team], *(service for service, owner in self.assignments.items() if owner == team)})

    def publish(self, message: Message) -> str:
        """Put `message` on the channel and say where it went."""
        with self.lock:
            self.messages.setdefault(message.message_id, message)
            self.record("published", message, body=message.body)
            if self.policy.schema_check:
                problem = self.schema_problem(message)
                if problem:
                    self.invalid.append(self.exception_entry(message, problem))
                    self.record("invalid", message, reason=problem)
                    return "invalid"
            team = self.route(message.body.get("service"))
            if team is None:
                self.undelivered.append(message.message_id)
                if self.policy.dead_letter:
                    self.dead_letter(message, "no subscriber for the service")
                    return "dead-letter:no-subscriber"
                self.record("discarded", message, reason="no subscriber for the service")
                return "discarded:no-subscriber"
            self.enqueue(team, message)
            return f"queued:{team}"

    def schema_problem(self, message: Message) -> str:
        try:
            jsonschema.validate(message.as_json(), DATATYPE_SCHEMA)
        except jsonschema.ValidationError as problem:
            return problem.message[:200]
        return ""

    def enqueue(self, team: str, message: Message, *, attempt: int = 1, duplicate: bool = False) -> None:
        with self.lock:
            self.queues[team].append(Job(message, attempt, duplicate))

    def next_job(self, team: str) -> Job | None:
        with self.lock:
            return self.queues[team].pop(0) if self.queues[team] else None

    def record(self, event: str, message: Message, *, team: str = "", outcome: str = "", reason: str = "", body: dict | None = None) -> None:
        """Append to the Message Store, when the arm keeps one. Trace fields are filled only with trace context."""
        if not self.policy.keep_store:
            return
        with self.lock:
            entry = {
                "seq": len(self.store) + 1, "event": event, "message_id": message.message_id, "service": message.body.get("service", ""),
                "team": team, "outcome": outcome, "reason": reason,
                "correlation_id": message.correlation_id if self.policy.trace_context else "",
                "history": list(message.history) if self.policy.trace_context else [],
            }
            if body is not None:
                entry["body"] = body
            self.store.append(entry)

    def record_action(self, action: dict, result: str) -> None:
        """Log what an owner did, in the action log and (when the arm keeps one) the Message Store."""
        with self.lock:
            self.action_log.append({"seq": len(self.action_log) + 1, **action, "result": result})
            if self.policy.keep_store:
                self.store.append({
                    "seq": len(self.store) + 1, "event": "action", "message_id": action.get("message_id", ""), "service": action.get("service", ""),
                    "team": action.get("team", ""), "outcome": "", "reason": f"{action['action']}: {result}", "correlation_id": "", "history": [],
                })

    def log_received(self, team: str, message: Message) -> None:
        with self.lock:
            self.team_logs[team].append({"event": "received", "message_id": message.message_id, "body": message.body})

    def log_outcome(self, team: str, message: Message, outcome: str, reason: str) -> None:
        with self.lock:
            self.team_logs[team].append({"event": "outcome", "message_id": message.message_id, "outcome": outcome, "reason": reason})

    def exception_entry(self, message: Message, reason: str) -> dict:
        trace = self.policy.trace_context
        return {
            "message_id": message.message_id, "body": message.body, "reason": reason,
            "correlation_id": message.correlation_id if trace else "", "history": list(message.history) if trace else [],
        }

    def dead_letter(self, message: Message, reason: str) -> None:
        with self.lock:
            self.dead_letters.append(self.exception_entry(message, reason))
            self.record("dead_letter", message, reason=reason)

    def ack(self, team: str, message_id: str) -> None:
        with self.lock:
            self._acked.add((team, message_id))

    def is_acked(self, team: str, message_id: str) -> bool:
        with self.lock:
            return (team, message_id) in self._acked

    def copy_for_retry(self, message: Message, hop: str, body: dict | None = None) -> Message:
        """A corrective copy: with trace context the same id, a Correlation Identifier and a longer history; without, a fresh unlinked id."""
        body = dict(body if body is not None else message.body)
        if self.policy.trace_context:
            return Message(message.message_id, body, message.correlation_id or message.message_id, [*message.history, hop])
        with self.lock:
            self._copies += 1
            fresh = f"c{self._copies:02d}"
            self.risk_of[fresh] = self.risk_of.get(message.message_id, "")
        return Message(fresh, body)
