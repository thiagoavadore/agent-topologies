"""Fan-out with a merge owner: three writer workers fix services in their own git worktrees and collide on platform.yaml.

The split is fixed and built to collide. Workers reply once with full file contents and hub changes; the harness
writes and commits them. How the hub conflicts are resolved is the arm (who owns the merge). The score is
`topologies.fixcheck.check()` on the merged repo and nothing else.
"""

import datetime as dt
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fanout_arms import HubChange, Spend, first_wins, group_by_key, hub_owner_decide, human_pick, options_of, supervisor_pick
from fanout_config import ARMS, OVERRIDES_ALLOWED_ARM, REDUCER_MODEL, WORKER_MODEL, WORKERS
from fanout_hubfile import parse_hub, set_hub_values
from fanout_overrides import effective_timeouts, grant_override, override_edits, service_overrides, strip_overrides
from fanout_prompts import REPORT_SYSTEM, report_prompt, service_paths, worker_prompt, worker_schema, worker_system
from fanout_repo import ScratchRepo
from topologies.fixcheck import CheckResult, check
from topologies.guards import InvalidAfterRetry, SkippedWork, call_and_validate, describe_skipped
from topologies.harbour import FIXTURE, HUB_FILE, SERVICES
from topologies.model import Backend

FAILED_STATUSES = ("invalid", "error", "injected-failure")


@dataclass
class WorkerRun:
    worker: str
    services: list[str]
    status: str  # committed, no-change, invalid, error or injected-failure
    attempts: int = 0
    tokens: int = 0
    served_models: list[str] = field(default_factory=list)
    served_primary: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # changed files only, full new text
    hub_changes: list[dict] = field(default_factory=list)
    notes: str = ""
    override_edits: list[dict] = field(default_factory=list)  # {service, key, value}: overrides the worker wrote
    overrides_stripped: bool = False  # True when the arm forbids worker overrides and the harness removed them
    error: str = ""
    commit_order: int | None = None
    wall_seconds: float = 0.0

    @property
    def failed(self) -> bool:
        return self.status in FAILED_STATUSES


def run_worker(
    worker: str,
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
    run = WorkerRun(worker=worker, services=WORKERS[worker], status="")
    if inject_failure:
        run.status, run.attempts, run.error = "injected-failure", 2, "harness injected an invalid payload after the retry"
        return run
    paths = service_paths(repo.main, run.services)
    base_files = {path: (repo.main / path).read_text(encoding="utf-8") for path in paths}
    hub_text = (repo.main / HUB_FILE).read_text(encoding="utf-8")
    try:
        outcome = call_and_validate(
            backend, model=model, system=worker_system(arm), prompt=worker_prompt(repo.main, worker), schema=worker_schema(paths)
        )
    except Exception as error:  # a backend failure takes this worker out, not the run
        run.status, run.error = "error", f"{type(error).__name__}: {str(error)[:300]}"
        run.wall_seconds = round(time.monotonic() - started, 1)
        return run
    run.tokens, run.attempts = outcome.tokens, outcome.attempts
    run.served_models = list(outcome.served_models)
    run.served_primary = list(outcome.served_models[:1])
    if isinstance(outcome, InvalidAfterRetry):
        run.status, run.error = "invalid", outcome.error
    else:
        payload = outcome.payload
        run.files = {item["path"]: item["content"] for item in payload["files"] if item["content"] != base_files[item["path"]]}
        run.overrides_stripped = arm != OVERRIDES_ALLOWED_ARM
        for path in list(run.files):
            if not path.endswith("/service.yaml"):
                continue
            edits = override_edits(base_files[path], run.files[path])
            run.override_edits += [{"service": path.split("/")[0], **edit} for edit in edits]
            if edits and run.overrides_stripped:
                run.files[path] = strip_overrides(base_files[path], run.files[path])
        run.files = {path: text for path, text in run.files.items() if text != base_files[path]}
        run.hub_changes = [{**item, "value": str(item["value"]).strip()} for item in payload["hub_changes"]]
        run.notes = payload.get("notes", "")
        files = dict(run.files)
        if arm != "hub-owner" and run.hub_changes:
            files[HUB_FILE] = set_hub_values(hub_text, {item["key"]: item["value"] for item in run.hub_changes})
        repo.write(worktree, files)
        with commit_lock:
            committed = repo.commit(worktree, f"fix: {worker} services")
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


def names_service(report: str, service: str) -> bool:
    """Plain text match on the service name, ignoring case and hyphen, underscore or space differences."""
    squash = lambda text: text.lower().replace("_", " ").replace("-", " ")
    return squash(service) in squash(report)


def score_record(result: CheckResult) -> dict:
    return {
        "risks_fixed": len(result.fixed),
        "risks_total": len(result.risks),
        "regressions": len(result.regressed),
        "outside_contract": len(result.outside_contract),
        "fixed": result.fixed,
        "regressed": result.regressed,
        "outside_contract_ids": result.outside_contract,
        "risks": {risk_id: asdict(item) for risk_id, item in result.risks.items()},
        "needs": {need_id: asdict(item) for need_id, item in result.needs.items()},
    }


def code_commit() -> str:
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, capture_output=True, text=True)
        return done.stdout.strip() if done.returncode == 0 else ""
    except OSError:
        return ""


def run_fanout(
    *,
    arm: str,
    backend: Backend,
    worker_model: str = WORKER_MODEL,
    reducer_model: str = REDUCER_MODEL,
    fail_worker: str | None = None,
    source: Path = FIXTURE,
    workdir: Path | None = None,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> dict:
    """Run one fan-out under `arm` and return everything needed to re-read and re-score it."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}: use one of {ARMS}")
    if fail_worker is not None and fail_worker not in WORKERS:
        raise ValueError(f"unknown worker {fail_worker!r}: use one of {list(WORKERS)}")
    reducer, report_spend, shadow = Spend(), Spend(), Spend()
    with ExitStack() as stack:
        base = workdir or Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="fanout-"))) / "scratch"
        base.mkdir(parents=True, exist_ok=True)
        repo = ScratchRepo(base, source)
        hub_text = (repo.main / HUB_FILE).read_text(encoding="utf-8")
        base_hub = parse_hub(hub_text)
        worktrees = {worker: repo.add_worktree(worker) for worker in WORKERS}
        lock, counter = threading.Lock(), [0]
        with ThreadPoolExecutor(max_workers=len(WORKERS)) as pool:
            futures = [
                pool.submit(
                    run_worker, worker, repo=repo, worktree=worktrees[worker], arm=arm, backend=backend,
                    model=worker_model, inject_failure=worker == fail_worker, commit_lock=lock, commit_counter=counter,
                )
                for worker in WORKERS
            ]
            runs = [future.result() for future in futures]
        by_commit = sorted((run for run in runs if run.commit_order), key=lambda run: run.commit_order)
        textual = repo.merge_branches([run.worker for run in by_commit])
        merged = repo.merged_worktree()

        changes = [change for run in by_commit for change in hub_changes_of(arm, repo, run, base_hub)]
        by_key = group_by_key(changes)
        shared_keys = sorted(key for key, items in by_key.items() if len({item.worker for item in items}) >= 2)
        contested = {key: items for key, items in by_key.items() if len({item.value for item in items}) >= 2}
        resolutions: list[dict] = []
        final_hub = dict(base_hub)

        grants: list[dict] = []
        if arm == "hub-owner":
            decided, grants, owner_error = hub_owner_decide(backend, reducer_model, hub_text, changes, reducer)
            for key, items in by_key.items():
                options = options_of(items)
                if key in decided:
                    pick = {"value": decided[key]["value"].strip(), "reason": decided[key]["reason"], "decided_by": "hub-owner"}
                else:
                    pick = {**first_wins(items), "decided_by": "first-wins (hub owner gave no decision)", "error": owner_error}
                resolutions.append({"key": key, "base_value": base_hub[key], "options": options, "contested": key in contested, **pick})
                final_hub[key] = pick["value"]
        else:
            for key, items in by_key.items():
                if key in contested:
                    continue
                options = options_of(items)
                resolutions.append({"key": key, "base_value": base_hub[key], "options": options, "contested": False,
                                    "value": options[0]["value"], "reason": options[0]["reasons"][0], "decided_by": "uncontested", "dropped_workers": []})
                final_hub[key] = options[0]["value"]
            options_by_key = {key: options_of(items) for key, items in contested.items()}
            shadows: dict[str, dict] = {}
            if arm == "human-decides" and contested:
                # Blind to the human, and finished before the first prompt so the human never waits on a model.
                with ThreadPoolExecutor(max_workers=len(contested)) as pool:
                    picks = pool.map(
                        lambda key: supervisor_pick(backend, reducer_model, hub_text, key, base_hub[key], options_by_key[key], contested[key], shadow),
                        contested,
                    )
                    shadows = dict(zip(contested, picks))
            for index, (key, items) in enumerate(contested.items(), start=1):
                options = options_by_key[key]
                record = {"key": key, "base_value": base_hub[key], "options": options, "contested": True}
                if arm in ("first-wins", OVERRIDES_ALLOWED_ARM):
                    pick = first_wins(items)
                elif arm == "supervisor-merges":
                    pick = supervisor_pick(backend, reducer_model, hub_text, key, base_hub[key], options, items, reducer)
                else:
                    pick = {
                        **human_pick(index, len(contested), key, base_hub[key], options, ask, show),
                        "supervisor_value": shadows[key]["value"], "supervisor_reason": shadows[key]["reason"],
                        "supervisor_decided_by": shadows[key]["decided_by"], "supervisor_overrides": shadows[key]["overrides"],
                    }
                resolutions.append({**record, **pick})
                final_hub[key] = pick["value"]
                grants += pick["overrides"]

        granted_files = {}
        for grant in grants:
            path = f"{grant['service']}/service.yaml"
            granted_files[path] = grant_override(granted_files.get(path) or (merged / path).read_text(encoding="utf-8"), grant["key"], grant["value"])
        repo.write(merged, granted_files)

        repo.write(merged, {HUB_FILE: set_hub_values(hub_text, {key: value for key, value in final_hub.items() if value != base_hub[key]})})
        repo.commit(merged, "merge: hub resolution")
        final_hub_read = parse_hub((merged / HUB_FILE).read_text(encoding="utf-8"))
        merged_diff = repo.diff("merged")
        timeouts = effective_timeouts(merged, SERVICES, final_hub_read["http.default_timeout"])
        overrides_in_merged = service_overrides(merged, SERVICES)
        result = check(merged)

    failed_services = [service for run in runs if run.failed for service in run.services]
    skipped = [SkippedWork(run.worker, f"{run.status}: services {', '.join(run.services)} not fixed") for run in runs if run.failed]
    report, report_error = "", ""
    try:
        reply = backend.call(model=reducer_model, system=REPORT_SYSTEM, prompt=report_prompt(runs, base_hub, final_hub_read, resolutions, grants))
        report = reply.text
        report_spend.add(reply.total_tokens, reply.served_models)
    except Exception as error:
        report_error = f"{type(error).__name__}: {str(error)[:300]}"
    named_failed = [service for service in failed_services if names_service(report, service)]
    unfixed_services = sorted({risk_id.split(".")[0] for risk_id, item in result.risks.items() if not item.fixed})
    worker_tokens = sum(run.tokens for run in runs)
    return {
        "arm": arm,
        "fail_worker": fail_worker,
        "workers": [asdict(run) for run in runs],
        "commit_order": [run.worker for run in by_commit],
        "textual_conflicts": [{"branch": item.branch, "files": list(item.files)} for item in textual],
        "shared_keys": shared_keys,
        "conflicts": [item for item in resolutions if item["contested"]],
        "conflicted_keys": sorted(contested),
        "resolutions": resolutions,
        "final_hub": final_hub_read,
        "merged_diff": merged_diff,
        "overrides_granted": grants,
        "override_edits_by_worker": {run.worker: len(run.override_edits) for run in runs},
        "override_edits_stripped": sum(len(run.override_edits) for run in runs if run.overrides_stripped),
        "override_edits_written": sum(len(run.override_edits) for run in runs if not run.overrides_stripped),
        "overrides_in_merged": overrides_in_merged,
        "effective_timeouts": timeouts,
        "distinct_timeouts": len(set(timeouts.values())),
        "score": score_record(result),
        "failed_services": failed_services,
        "named_failed_services": named_failed,
        "silent_success": bool(failed_services) and not named_failed,
        "unfixed_services": unfixed_services,
        "named_unfixed_services": [service for service in unfixed_services if names_service(report, service)],
        "harness_skip_note": describe_skipped(skipped),
        "report": report,
        "report_error": report_error,
        "tokens_by_role": {"workers": worker_tokens, "reducer": reducer.tokens, "report": report_spend.tokens, "supervisor_shadow": shadow.tokens},
        "total_tokens": worker_tokens + reducer.tokens + report_spend.tokens + shadow.tokens,
        "worker_served_models": sorted({name for run in runs for name in run.served_models}),
        "worker_served_primary": sorted({name for run in runs for name in run.served_primary}),
        "reducer_served_models": sorted(reducer.served | report_spend.served | shadow.served),
        "reducer_served_primary": sorted(reducer.primary | report_spend.primary | shadow.primary),
        "human_seconds": round(sum(item.get("human_seconds", 0) for item in resolutions), 2),
    }


def run_row(*, run: int, backend_name: str, worker_model: str = WORKER_MODEL, reducer_model: str = REDUCER_MODEL, **kwargs) -> dict:
    """One JSONL row: run metadata, wall seconds and the full record of `run_fanout`."""
    started = time.monotonic()
    record = run_fanout(worker_model=worker_model, reducer_model=reducer_model, **kwargs)
    return {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "run": run,
        "backend": backend_name,
        "worker_model": worker_model,
        "reducer_model": reducer_model,
        "code_commit": code_commit(),
        "wall_seconds": round(time.monotonic() - started, 1),
        **record,
    }
