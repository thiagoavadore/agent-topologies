"""Fan-out with a merge owner: three writer workers fix cards in their own git worktrees and collide on platform.yaml.

The split is fixed and built to collide. Workers return full file contents and hub changes; the harness
writes and commits them. How the hub conflicts are resolved is the arm (who owns the merge).
"""

import datetime as dt
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from fixcheck import check
from hubfile import parse_hub, set_hub_values
from scratch_repo import ScratchRepo
from topologies.guards import InvalidAfterRetry, SkippedWork, call_and_validate, describe_skipped
from topologies.model import Backend

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
HUB_FILE = "platform.yaml"

WORKERS = {
    "w1": ["bookings-api.md", "maintenance-scheduler.md", "admin-console.md"],
    "w2": ["notifications.md", "customer-profiles.md", "fleet-telemetry.md"],
    "w3": ["pricing-engine.md", "payments-gateway.md"],
}
ARMS = ("first-wins", "supervisor-merges", "hub-owner", "human-decides")
FAILED_STATUSES = ("invalid", "error", "injected-failure")

ONE_LINE = {"type": "string", "minLength": 1, "maxLength": 200, "pattern": "^[^\\n]+$"}


def hub_change_schema(hub_keys: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["key", "value", "reason"],
        "properties": {"key": {"enum": hub_keys}, "value": ONE_LINE, "reason": ONE_LINE},
    }


def worker_schema(cards: list[str], hub_keys: list[str]) -> dict:
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
                    "properties": {"path": {"enum": cards}, "content": {"type": "string", "minLength": 1}},
                },
            },
            "hub_changes": {"type": "array", "items": hub_change_schema(hub_keys)},
        },
    }


CONFLICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["value", "reason"],
    "properties": {"value": ONE_LINE, "reason": ONE_LINE},
}


def hub_owner_schema(hub_keys: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["hub"],
        "properties": {"hub": {"type": "array", "items": hub_change_schema(hub_keys)}},
    }


WORKER_SYSTEM = """You fix operational risks on service cards of a bike-rental platform. You see only your own cards and the shared file platform.yaml. Other workers fix other cards at the same time and you cannot see their work.
Find every operational risk on your cards (hardcoded secrets, outbound calls that can wait forever, single points of failure, a missing owner, unpinned dependencies, data with no backup, internal surfaces open to the internet, public endpoints with no rate limit) and fix each one by editing the card text.
Platform rule: a setting that applies across services and has a key in platform.yaml belongs in platform.yaml. Set the key through hub_changes, with a one-line reason, and make the card refer to it by key name instead of hardcoding the number. A card may differ from the hub for itself with a line `Override: <key> = <value>`.
{hub_rule}
Return the full new text of every card you change and leave unchanged cards out. Reply with JSON only, no prose, matching:
{{"files": [{{"path": "<card file>", "content": "<full new card text>"}}], "hub_changes": [{{"key": "<hub key>", "value": "<new value>", "reason": "<one line>"}}]}}"""

HUB_RULE_OWN_COPY = "Your hub_changes are written to platform.yaml in your own copy of the repo."
HUB_RULE_REQUESTS = "You cannot edit platform.yaml. Your hub_changes are requests to the hub owner, who decides the final values."

SUPERVISOR_SYSTEM = """You own the merge of a shared platform file after workers fixed service cards in parallel without seeing each other's work.
Two or more workers set the same hub key to different values. Pick the final value for that key so every service keeps working. You may pick one worker's value or write a different one.
Reply with JSON only, no prose, matching: {"value": "<final value>", "reason": "<one line>"}"""

HUB_OWNER_SYSTEM = """You own platform.yaml. Workers fixed service cards in parallel without seeing each other's work and could not edit platform.yaml: they sent change requests.
Decide the final value of every requested key so every service keeps working. You may accept a request, merge several, or keep the current value.
Reply with JSON only, no prose, matching: {"hub": [{"key": "<hub key>", "value": "<final value>", "reason": "<one line>"}]}"""

SUMMARY_SYSTEM = """You own the merge of a parallel fix across service cards. Write the merge report for the platform lead in at most 150 words: what the merged repo now contains, which hub values were decided and why, and anything the lead should know."""


@dataclass
class WorkerRun:
    worker: str
    cards: list[str]
    status: str  # committed, no-change, invalid, error or injected-failure
    attempts: int = 0
    tokens: int = 0
    served_models: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # changed cards only, full new text
    hub_changes: list[dict] = field(default_factory=list)
    error: str = ""
    commit_order: int | None = None
    wall_seconds: float = 0.0


@dataclass(frozen=True)
class HubChange:
    worker: str
    key: str
    value: str
    reason: str


def worker_prompt(cards: dict[str, str], hub_text: str) -> str:
    parts = [f"=== {HUB_FILE} ===\n{hub_text}"]
    parts += [f"=== {name} ===\n{text}" for name, text in cards.items()]
    return "\n\n".join(parts)


def run_worker(
    worker: str,
    cards: list[str],
    base_cards: dict[str, str],
    hub_text: str,
    *,
    repo: ScratchRepo,
    worktree: Path,
    arm: str,
    backend: Backend,
    model: str,
    inject_failure: bool,
    commit_lock: threading.Lock,
    commit_counter: list[int],
) -> WorkerRun:
    started = time.monotonic()
    run = WorkerRun(worker=worker, cards=cards, status="")
    if inject_failure:
        run.status, run.attempts, run.error = "injected-failure", 2, "harness injected an invalid payload after the retry"
        return run
    system = WORKER_SYSTEM.format(hub_rule=HUB_RULE_REQUESTS if arm == "hub-owner" else HUB_RULE_OWN_COPY)
    schema = worker_schema(cards, list(parse_hub(hub_text)))
    try:
        outcome = call_and_validate(
            backend, model=model, system=system, prompt=worker_prompt({c: base_cards[c] for c in cards}, hub_text), schema=schema
        )
    except Exception as error:  # a backend failure takes this worker out, not the run
        run.status, run.error = "error", f"{type(error).__name__}: {str(error)[:300]}"
        run.wall_seconds = round(time.monotonic() - started, 1)
        return run
    run.tokens, run.attempts, run.served_models = outcome.tokens, outcome.attempts, list(outcome.served_models)
    if isinstance(outcome, InvalidAfterRetry):
        run.status, run.error = "invalid", outcome.error
    else:
        payload = outcome.payload
        run.files = {item["path"]: item["content"] for item in payload["files"] if item["content"] != base_cards[item["path"]]}
        run.hub_changes = payload["hub_changes"]
        files = dict(run.files)
        if arm != "hub-owner" and run.hub_changes:
            files[HUB_FILE] = set_hub_values(hub_text, {item["key"]: item["value"] for item in run.hub_changes})
        repo.write(worktree, files)
        with commit_lock:
            committed = repo.commit(worktree, f"fix: {worker} cards")
            if committed:
                commit_counter[0] += 1
                run.commit_order = commit_counter[0]
        run.status = "committed" if committed else "no-change"
    run.wall_seconds = round(time.monotonic() - started, 1)
    return run


def hub_changes_of(arm: str, repo: ScratchRepo, run: WorkerRun, base_hub: dict[str, str]) -> list[HubChange]:
    """Hub keys this worker moved off the base value, read from its committed platform.yaml (or its requests)."""
    if arm == "hub-owner":
        values = {**base_hub, **{item["key"]: item["value"] for item in run.hub_changes}}
    else:
        values = parse_hub(repo.show(run.worker, HUB_FILE))
    reasons = {item["key"]: item["reason"] for item in run.hub_changes}
    return [HubChange(run.worker, key, value, reasons.get(key, "")) for key, value in values.items() if value != base_hub[key]]


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


def cards_block(cards: dict[str, str]) -> str:
    return "\n\n".join(f"=== {name} ===\n{text}" for name, text in cards.items())


def conflict_prompt(hub_text: str, merged_cards: dict[str, str], key: str, base_value: str, options: list[dict]) -> str:
    lines = [f"Hub key in conflict: {key} (current value: {base_value})"]
    for option in options:
        owners = "; ".join(f"{worker} (cards: {', '.join(WORKERS[worker])})" for worker in option["workers"])
        lines.append(f"- value {option['value']!r} from {owners}. Reason: {' / '.join(option['reasons'])}")
    return f"=== {HUB_FILE} (current) ===\n{hub_text}\n\n{cards_block(merged_cards)}\n\n" + "\n".join(lines)


def first_wins(key: str, changes: list[HubChange], options: list[dict]) -> dict:
    winner = changes[0]
    dropped = sorted({change.worker for change in changes if change.value != winner.value})
    return {"value": winner.value, "reason": winner.reason, "decided_by": "first-wins", "dropped_workers": dropped}


def supervisor_pick(backend, model, hub_text, merged_cards, key, base_value, options, changes) -> tuple[dict, int, list[str]]:
    """Ask the reducer for one key; on failure fall back to first-wins and say so. Returns (pick, tokens, served models)."""
    try:
        outcome = call_and_validate(
            backend, model=model, system=SUPERVISOR_SYSTEM, schema=CONFLICT_SCHEMA,
            prompt=conflict_prompt(hub_text, merged_cards, key, base_value, options),
        )
    except Exception as error:
        fallback = first_wins(key, changes, options)
        return {**fallback, "decided_by": "first-wins (supervisor errored)", "error": f"{type(error).__name__}: {str(error)[:300]}"}, 0, []
    if isinstance(outcome, InvalidAfterRetry):
        fallback = first_wins(key, changes, options)
        return {**fallback, "decided_by": "first-wins (supervisor invalid)", "error": outcome.error}, outcome.tokens, list(outcome.served_models)
    pick = {"value": outcome.payload["value"].strip(), "reason": outcome.payload["reason"], "decided_by": "supervisor"}
    return pick, outcome.tokens, list(outcome.served_models)


def render_conflict(index: int, total: int, key: str, base_value: str, options: list[dict]) -> str:
    lines = [f"\nConflict {index}/{total}: {key} (now: {base_value})"]
    letters = "abc"
    for letter, option in zip(letters, options):
        lines.append(f"  [{letter}] {option['value']}  <- {', '.join(option['workers'])}: {' / '.join(option['reasons'])}")
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
    seconds = round(time.monotonic() - started, 1)
    return {"value": value, "reason": "", "decided_by": "human", "human_choice": "edit" if choice == "e" else choice, "human_seconds": seconds}


def summary_prompt(runs: list[WorkerRun], base_hub: dict[str, str], final_hub: dict[str, str], resolutions: list[dict]) -> str:
    lines = ["Workers:"]
    for run in runs:
        outcome = "returned a fix" if run.status in ("committed", "no-change") else f"no usable output ({run.status})"
        lines.append(f"- {run.worker} ({', '.join(run.cards)}): {outcome}")
    lines.append("Hub values now different from the start:")
    changed = [f"- {key}: {base_hub[key]} -> {value}" for key, value in final_hub.items() if value != base_hub[key]]
    lines += changed or ["- none"]
    lines.append("Conflicts and how they were resolved:")
    for item in resolutions:
        if item["contested"]:
            wanted = "; ".join(f"{', '.join(option['workers'])} wanted {option['value']}" for option in item["options"])
            lines.append(f"- {item['key']}: {wanted}; decided {item['value']} ({item['decided_by']})")
    if not any(item["contested"] for item in resolutions):
        lines.append("- none")
    return "\n".join(lines)


def named_cards(summary: str, failed_cards: list[str]) -> list[str]:
    text = summary.lower()
    return [card for card in failed_cards if card.removesuffix(".md") in text]


def run_fanout(
    *,
    arm: str,
    backend: Backend,
    worker_model: str,
    reducer_model: str,
    fail_worker: str | None = None,
    fixtures: Path = FIXTURES,
    workdir: Path | None = None,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> dict:
    """Run one fan-out under `arm` and return everything needed to re-read and re-score it."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}: use one of {ARMS}")
    base_cards = {path.name: path.read_text() for path in sorted((fixtures / "services").glob("*.md"))}
    hub_text = (fixtures / HUB_FILE).read_text()
    base_hub = parse_hub(hub_text)
    with ExitStack() as stack:
        base = workdir or Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="fanout-"))) / "scratch"
        repo = ScratchRepo(base, {**base_cards, HUB_FILE: hub_text})
        worktrees = {worker: repo.add_worktree(worker) for worker in WORKERS}
        lock, counter = threading.Lock(), [0]
        with ThreadPoolExecutor(max_workers=len(WORKERS)) as pool:
            futures = [
                pool.submit(
                    run_worker, worker, cards, base_cards, hub_text, repo=repo, worktree=worktrees[worker], arm=arm,
                    backend=backend, model=worker_model, inject_failure=worker == fail_worker, commit_lock=lock, commit_counter=counter,
                )
                for worker, cards in WORKERS.items()
            ]
            runs = [future.result() for future in futures]
        by_commit = sorted((run for run in runs if run.commit_order), key=lambda run: run.commit_order)
        textual = repo.merge_branches([run.worker for run in by_commit])
        merged = repo.merged_worktree()
        merged_cards = {path.name: path.read_text() for path in sorted(merged.glob("*.md"))}

        changes = [change for run in by_commit for change in hub_changes_of(arm, repo, run, base_hub)]
        by_key = group_by_key(changes)
        shared_keys = sorted(key for key, items in by_key.items() if len({item.worker for item in items}) >= 2)
        contested = {key: items for key, items in by_key.items() if len({item.value for item in items}) >= 2}
        reducer_tokens, shadow_tokens, served = 0, 0, set()
        resolutions: list[dict] = []
        final_hub = dict(base_hub)

        if arm == "hub-owner":
            decided, owner_error = {}, ""
            if by_key:
                requests = "\n".join(
                    f"- {change.worker} (cards: {', '.join(WORKERS[change.worker])}) asks {change.key} = {change.value!r}. Reason: {change.reason}"
                    for change in changes
                )
                prompt = f"=== {HUB_FILE} (current) ===\n{hub_text}\n\n{cards_block(merged_cards)}\n\nChange requests:\n{requests}"
                try:
                    outcome = call_and_validate(backend, model=reducer_model, system=HUB_OWNER_SYSTEM, prompt=prompt, schema=hub_owner_schema(list(base_hub)))
                    reducer_tokens += outcome.tokens
                    served.update(outcome.served_models)
                    if isinstance(outcome, InvalidAfterRetry):
                        owner_error = outcome.error
                    else:
                        decided = {item["key"]: item for item in outcome.payload["hub"]}
                except Exception as error:
                    owner_error = f"{type(error).__name__}: {str(error)[:300]}"
            for key, items in by_key.items():
                options = options_of(items)
                if key in decided:
                    pick = {"value": decided[key]["value"].strip(), "reason": decided[key]["reason"], "decided_by": "hub-owner"}
                else:
                    pick = {**first_wins(key, items, options), "decided_by": "first-wins (hub owner gave no decision)", "error": owner_error}
                resolutions.append({"key": key, "base_value": base_hub[key], "options": options, "contested": key in contested, **pick})
                final_hub[key] = pick["value"]
        else:
            for key, items in by_key.items():
                options = options_of(items)
                if key in contested:
                    continue
                resolutions.append({"key": key, "base_value": base_hub[key], "options": options, "contested": False,
                                    "value": options[0]["value"], "reason": options[0]["reasons"][0], "decided_by": "uncontested", "dropped_workers": []})
                final_hub[key] = options[0]["value"]
            for index, (key, items) in enumerate(contested.items(), start=1):
                options = options_of(items)
                record = {"key": key, "base_value": base_hub[key], "options": options, "contested": True}
                if arm == "first-wins":
                    pick = first_wins(key, items, options)
                elif arm == "supervisor-merges":
                    pick, tokens, models = supervisor_pick(backend, reducer_model, hub_text, merged_cards, key, base_hub[key], options, items)
                    reducer_tokens += tokens
                    served.update(models)
                else:
                    shadow, tokens, models = supervisor_pick(backend, reducer_model, hub_text, merged_cards, key, base_hub[key], options, items)
                    shadow_tokens += tokens
                    served.update(models)
                    pick = {
                        **human_pick(index, len(contested), key, base_hub[key], options, ask, show),
                        "supervisor_value": shadow["value"], "supervisor_reason": shadow["reason"], "supervisor_decided_by": shadow["decided_by"],
                    }
                resolutions.append({**record, **pick})
                final_hub[key] = pick["value"]

        repo.write(merged, {HUB_FILE: set_hub_values(hub_text, {key: value for key, value in final_hub.items() if value != base_hub[key]})})
        repo.commit(merged, "merge: hub resolution")
        final_cards = {path.name: path.read_text() for path in sorted(merged.glob("*.md"))}
        final_hub_read = parse_hub((merged / HUB_FILE).read_text())

    failed_cards = [card for run in runs if run.status in FAILED_STATUSES for card in run.cards]
    skipped = [SkippedWork(run.worker, f"{run.status}: cards {', '.join(run.cards)} not fixed") for run in runs if run.status in FAILED_STATUSES]
    summary, summary_error = "", ""
    try:
        reply = backend.call(model=reducer_model, system=SUMMARY_SYSTEM, prompt=summary_prompt(runs, base_hub, final_hub_read, resolutions))
        summary = reply.text
        reducer_tokens += reply.total_tokens
        served.update(reply.served_models)
    except Exception as error:
        summary_error = f"{type(error).__name__}: {str(error)[:300]}"
    named = named_cards(summary, failed_cards)
    worker_tokens = sum(run.tokens for run in runs)
    return {
        "arm": arm,
        "fail_worker": fail_worker,
        "workers": [asdict(run) for run in runs],
        "commit_order": [run.worker for run in by_commit],
        "textual_conflicts": [{"branch": item.branch, "files": list(item.files)} for item in textual],
        "shared_keys": shared_keys,
        "conflicts": [item for item in resolutions if item["contested"]],
        "resolutions": resolutions,
        "final_hub": final_hub_read,
        "final_cards": final_cards,
        "score": check(final_cards, final_hub_read),
        "failed_cards": failed_cards,
        "named_failed_cards": named,
        "silent_success": bool(failed_cards) and not named,
        "harness_skip_note": describe_skipped(skipped),
        "summary": summary,
        "summary_error": summary_error,
        "tokens_by_role": {"workers": worker_tokens, "reducer": reducer_tokens, "supervisor_shadow": shadow_tokens},
        "total_tokens": worker_tokens + reducer_tokens + shadow_tokens,
        "worker_served_models": sorted({name for run in runs for name in run.served_models}),
        "reducer_served_models": sorted(served),
        "human_seconds": round(sum(item.get("human_seconds", 0) for item in resolutions), 1),
    }


def run_row(*, run: int, backend_name: str, worker_model: str, reducer_model: str, **kwargs) -> dict:
    """One JSONL row: run metadata, wall seconds and the full record of `run_fanout`."""
    started = time.monotonic()
    record = run_fanout(worker_model=worker_model, reducer_model=reducer_model, **kwargs)
    return {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "run": run,
        "backend": backend_name,
        "worker_model": worker_model,
        "reducer_model": reducer_model,
        "wall_seconds": round(time.monotonic() - started, 1),
        **record,
    }
