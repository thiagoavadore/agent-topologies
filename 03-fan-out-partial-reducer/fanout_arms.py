"""The four ways a hub conflict gets resolved: who owns the merge."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fanout_config import WORKERS
from fanout_prompts import CONFLICT_SCHEMA, HUB_OWNER_SCHEMA, HUB_OWNER_SYSTEM, SUPERVISOR_SYSTEM, conflict_prompt, hub_owner_prompt
from topologies.guards import InvalidAfterRetry, call_and_validate
from topologies.model import Backend


@dataclass
class Spend:
    """Tokens and served models of one role's calls."""

    tokens: int = 0
    served: set[str] = field(default_factory=set)
    primary: set[str] = field(default_factory=set)  # the model that answered most, per call
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, tokens: int, served_models: tuple[str, ...] | list[str]) -> None:
        with self.lock:
            self.tokens += tokens
            self.served.update(served_models)
            if served_models:
                self.primary.add(served_models[0])


@dataclass(frozen=True)
class HubChange:
    worker: str
    key: str
    value: str
    reason: str


def group_by_key(changes: list[HubChange]) -> dict[str, list[HubChange]]:
    grouped: dict[str, list[HubChange]] = {}
    for change in changes:
        grouped.setdefault(change.key, []).append(change)
    return grouped


def options_of(changes: list[HubChange]) -> list[dict]:
    """One option per distinct value, in worker order: value, who set it, why."""
    options: dict[str, dict] = {}
    for change in sorted(changes, key=lambda item: item.worker):
        option = options.setdefault(change.value, {"value": change.value, "workers": [], "reasons": []})
        option["workers"].append(change.worker)
        option["reasons"].append(change.reason)
    return list(options.values())


def first_wins(changes: list[HubChange]) -> dict:
    """The earliest commit that touched the key wins; workers with another value are dropped and named."""
    winner = changes[0]
    dropped = sorted({change.worker for change in changes if change.value != winner.value})
    return {"value": winner.value, "reason": winner.reason, "decided_by": "first-wins", "dropped_workers": dropped}


def supervisor_pick(backend: Backend, model: str, hub_text: str, key: str, base_value: str, options: list[dict], changes: list[HubChange], spend: Spend) -> dict:
    """Ask the reducer for one key; on a backend error or invalid reply fall back to first-wins and say so."""
    prompt = conflict_prompt(hub_text, key, base_value, options)
    try:
        outcome = call_and_validate(backend, model=model, system=SUPERVISOR_SYSTEM, schema=CONFLICT_SCHEMA, prompt=prompt)
    except Exception as error:
        return {**first_wins(changes), "decided_by": "first-wins (supervisor errored)", "error": f"{type(error).__name__}: {str(error)[:300]}"}
    spend.add(outcome.tokens, outcome.served_models)
    if isinstance(outcome, InvalidAfterRetry):
        return {**first_wins(changes), "decided_by": "first-wins (supervisor invalid)", "error": outcome.error}
    return {"value": outcome.payload["value"].strip(), "reason": outcome.payload["reason"], "decided_by": "supervisor"}


def hub_owner_decide(backend: Backend, model: str, hub_text: str, changes: list[HubChange], spend: Spend) -> tuple[dict[str, dict], str]:
    """One reducer call decides every requested key. Returns (decision per key, error text if it gave none)."""
    if not changes:
        return {}, ""
    try:
        outcome = call_and_validate(backend, model=model, system=HUB_OWNER_SYSTEM, schema=HUB_OWNER_SCHEMA, prompt=hub_owner_prompt(hub_text, changes))
    except Exception as error:
        return {}, f"{type(error).__name__}: {str(error)[:300]}"
    spend.add(outcome.tokens, outcome.served_models)
    if isinstance(outcome, InvalidAfterRetry):
        return {}, outcome.error
    return {item["key"]: item for item in outcome.payload["hub"]}, ""


def render_conflict(index: int, total: int, key: str, base_value: str, options: list[dict]) -> str:
    lines = [f"\nConflict {index}/{total}: {key} (now: {base_value})"]
    letters = "abc"
    for letter, option in zip(letters, options):
        who = ", ".join(f"{worker} ({', '.join(WORKERS[worker])})" for worker in option["workers"])
        lines.append(f"  [{letter}] {option['value']}  <- {who}: {' / '.join(option['reasons'])}")
    choices = "/".join(f"[{letter}]" for letter in letters[: len(options)])
    lines.append(f"{choices}/[e]dit value")
    return "\n".join(lines)


def human_pick(index: int, total: int, key: str, base_value: str, options: list[dict], ask: Callable[[str], str], show: Callable[[str], None]) -> dict:
    """Prompt for one conflict; the timer starts when the conflict is rendered and stops at the final answer."""
    letters = "abc"[: len(options)]
    show(render_conflict(index, total, key, base_value, options))
    started = time.monotonic()
    while True:
        choice = ask("choice: ").strip().lower()
        if choice in tuple(letters) + ("e",):
            break
    if choice == "e":
        while not (value := ask("value: ").strip()):
            pass
    else:
        value = options[letters.index(choice)]["value"]
    seconds = round(time.monotonic() - started, 2)
    return {"value": value, "reason": "", "decided_by": "human", "human_choice": "edit" if choice == "e" else choice, "human_seconds": seconds}
