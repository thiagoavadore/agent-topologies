"""The owner of the channel: what it sees, its one round of actions, and the harness code that carries them out."""

from dataclasses import dataclass, field

from bus_channel import Bus
from bus_config import SUBSCRIPTIONS, TEAMS
from bus_faults import valid_copy
from bus_prompts import ACTIONS_SCHEMA, PLATFORM_AGENT_SYSTEM, STEWARD_SYSTEM, owner_prompt
from topologies.guards import InvalidAfterRetry, call_and_validate
from topologies.harbour import SERVICES
from topologies.model import Backend

EMPTY_VALUES = ("", [], None)


def compact(entry: dict) -> dict:
    """Drop empty fields so a prompt carries only what the channel recorded."""
    return {key: value for key, value in entry.items() if value not in EMPTY_VALUES}


@dataclass
class OwnerResult:
    called: bool = False
    actions: list[dict] = field(default_factory=list)
    tokens: int = 0
    served_models: tuple[str, ...] = ()
    error: str = ""
    prompt_chars: int = 0


def owner_view(arm: str, bus: Bus) -> list[dict]:
    """What the arm's owner is shown: the whole store, only the exception channels, or nothing."""
    if arm == "steward-agent":
        return [compact(event) for event in bus.store]
    if arm == "platform-owned":
        return [compact({"channel": "dead_letter", **entry}) for entry in bus.dead_letters] + [
            compact({"channel": "invalid", **entry}) for entry in bus.invalid
        ]
    return []


def ask_owner(arm: str, *, backend: Backend, model: str, bus: Bus) -> OwnerResult:
    """One call to the steward or the platform agent; not made when there is no owner or nothing to show."""
    view = owner_view(arm, bus)
    if not view:
        return OwnerResult()
    prompt = owner_prompt(view, SUBSCRIPTIONS, TEAMS)
    system = STEWARD_SYSTEM if arm == "steward-agent" else PLATFORM_AGENT_SYSTEM
    try:
        outcome = call_and_validate(backend, model=model, system=system, prompt=prompt, schema=ACTIONS_SCHEMA)
    except Exception as error:  # a backend failure takes the owner out, not the run
        return OwnerResult(called=True, error=f"{type(error).__name__}: {str(error)[:300]}", prompt_chars=len(prompt))
    if isinstance(outcome, InvalidAfterRetry):
        return OwnerResult(True, [], outcome.tokens, outcome.served_models, outcome.error, len(prompt))
    return OwnerResult(True, outcome.payload["actions"], outcome.tokens, outcome.served_models, "", len(prompt))


def execute(actions: list[dict], *, bus: Bus, visible_ids: set[str]) -> list[dict]:
    """Carry out the owner's actions in order: queue corrective copies for pass 2. Returns the action log."""
    for action in actions:
        bus.record_action(action, run_action(action, bus, visible_ids))
    return bus.action_log


def run_action(action: dict, bus: Bus, visible_ids: set[str]) -> str:
    kind = action["action"]
    if kind == "assign_owner":
        return assign_owner(action.get("service", ""), action.get("team", ""), bus)
    message_id = action.get("message_id", "")
    if message_id not in visible_ids or message_id not in bus.messages:
        return "ignored: no such message in what the owner was shown"
    original = bus.messages[message_id]
    if kind == "return_to_producer":
        risk_id = bus.risk_of.get(message_id)
        if not risk_id:
            return "ignored: the producer has no risk for this message"
        return bus.publish(bus.copy_for_retry(original, kind, body=valid_copy(risk_id)))
    service = action.get("service", "") if kind == "route" else original.body.get("service", "")
    if service not in SERVICES:
        return "ignored: route needs a known service"
    team = bus.route(service)
    if team is None:
        return f"ignored: no team owns {service}"
    body = {**original.body, "service": service}
    bus.enqueue(team, bus.copy_for_retry(original, f"route:{service}" if kind == "route" else kind, body=body))
    return f"queued:{team}"


def assign_owner(service: str, team: str, bus: Bus) -> str:
    if service not in SERVICES or team not in TEAMS:
        return "ignored: assign_owner needs a known service and team"
    if bus.route(service) is not None:
        return f"ignored: {service} already has an owner"
    bus.assignments[service] = team
    pending = [bus.messages[message_id] for message_id in bus.undelivered if bus.messages[message_id].body.get("service") == service]
    for message in pending:
        bus.enqueue(team, bus.copy_for_retry(message, "assign_owner"))
    return f"assigned {team}; queued {len(pending)} message(s)"
