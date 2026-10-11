"""Hierarchy of leads: 8 service workers, optional Opus sub-leads, one Opus platform lead that owns platform.yaml.

Workers and sub-leads run once per run index and are shared by the arms that use them; each arm only differs in the
management tiers above the workers. The harness applies every worker file and every platform-lead decision to a fresh
copy of the world and scores it with `topologies.fixcheck.check()` and nothing else.
"""

import datetime as dt
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from hier_config import ARMS, CUT_OF_ARM, CUTS, LEAD_MODEL, NEEDS_ARMS, REGISTRY_ARMS, WORKER_MODEL, owner_team
from hier_harm import classify, coupling_ladder
from hier_hub import effective_timeouts, grant_override, mandate_violation, override_edits, parse_hub, scratch_copy, service_overrides, set_hub_values, strip_overrides
from hier_prompts import SUB_LEAD_SCHEMA, SUB_LEAD_SYSTEM, TOP_SCHEMA, rules_block, service_paths, sub_lead_prompt, top_prompt, top_system, worker_prompt, worker_schema, worker_system
from topologies.fixcheck import CheckResult, NotFixed, check
from topologies.guards import InvalidAfterRetry, call_and_validate
from topologies.harbour import HUB_FILE, SERVICES, OutsideContract
from topologies.model import Backend


@dataclass
class WorkerResult:
    service: str
    status: str  # ok, no-change, invalid or error
    attempts: int = 0
    tokens: int = 0
    served_models: list[str] = field(default_factory=list)
    served_primary: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # changed files only, full new text
    hub_requests: list[dict] = field(default_factory=list)  # {key, value, reason}
    notes: str = ""
    mandate_rejections: list[dict] = field(default_factory=list)  # {path, reason}: file edits reverted for breaking the mandate
    override_edits: list[dict] = field(default_factory=list)  # {service, key, value}: overrides the worker wrote
    overrides_stripped: bool = False
    error: str = ""
    wall_seconds: float = 0.0


@dataclass
class SubLeadResult:
    group: str
    services: list[str]
    requests: list[dict] = field(default_factory=list)
    tokens: int = 0
    served_models: list[str] = field(default_factory=list)
    served_primary: list[str] = field(default_factory=list)
    fallback: str = ""  # "" or "forwarded-unchanged"
    error: str = ""
    prompt_chars: int = 0
    wall_seconds: float = 0.0


@dataclass
class TopResult:
    decisions: dict[str, dict] = field(default_factory=dict)  # hub key -> {value, reason}
    grants: list[dict] = field(default_factory=list)
    tokens: int = 0
    served_models: list[str] = field(default_factory=list)
    served_primary: list[str] = field(default_factory=list)
    called: bool = False
    error: str = ""
    prompt_chars: int = 0
    wall_seconds: float = 0.0


def run_worker(service: str, *, backend: Backend, model: str, base: Path) -> WorkerResult:
    started = time.monotonic()
    result = WorkerResult(service=service, status="")
    paths = service_paths(base, service)
    base_files = {path: (base / path).read_text(encoding="utf-8") for path in paths}
    try:
        outcome = call_and_validate(backend, model=model, system=worker_system(), prompt=worker_prompt(base, service), schema=worker_schema(paths))
    except Exception as error:  # a backend failure takes this worker out, not the run
        result.status, result.error = "error", f"{type(error).__name__}: {str(error)[:300]}"
        result.wall_seconds = round(time.monotonic() - started, 1)
        return result
    result.tokens, result.attempts = outcome.tokens, outcome.attempts
    result.served_models = list(outcome.served_models)
    result.served_primary = list(outcome.served_models[:1])
    if isinstance(outcome, InvalidAfterRetry):
        result.status, result.error = "invalid", outcome.error
    else:
        payload = outcome.payload
        result.files = {item["path"]: item["content"] for item in payload["files"] if item["content"] != base_files[item["path"]]}
        result.overrides_stripped = True
        for path in list(result.files):
            if not path.endswith("/service.yaml"):
                continue
            edits = override_edits(base_files[path], result.files[path])
            result.override_edits += [{"service": service, **edit} for edit in edits]
            if edits:
                result.files[path] = strip_overrides(base_files[path], result.files[path])
        for path in list(result.files):
            reason = mandate_violation(path, result.files[path])
            if reason:
                result.mandate_rejections.append({"path": path, "reason": reason})
                del result.files[path]
        result.files = {path: text for path, text in result.files.items() if text != base_files[path]}
        result.hub_requests = [{**item, "value": str(item["value"]).strip()} for item in payload["hub_changes"]]
        result.notes = payload.get("notes", "")
        result.status = "ok" if result.files or result.hub_requests else "no-change"
    result.wall_seconds = round(time.monotonic() - started, 1)
    return result


def run_workers(*, backend: Backend, model: str, base: Path) -> list[WorkerResult]:
    with ThreadPoolExecutor(max_workers=len(SERVICES)) as pool:
        futures = [pool.submit(run_worker, service, backend=backend, model=model, base=base) for service in SERVICES]
        return [future.result() for future in futures]


def run_sub_lead(group: str, services: list[str], *, backend: Backend, model: str, hub_text: str, base: Path, workers: list[WorkerResult]) -> SubLeadResult:
    started = time.monotonic()
    own = [worker for worker in workers if worker.service in services]
    teams = sorted({owner_team(service) or "none" for service in services})
    prompt = sub_lead_prompt(hub_text, base, services, own)
    system = SUB_LEAD_SYSTEM.format(services=", ".join(services), teams=", ".join(teams), rules=rules_block())
    result = SubLeadResult(group=group, services=list(services), prompt_chars=len(prompt))
    forwarded = [dict(request) for worker in own for request in worker.hub_requests]
    try:
        outcome = call_and_validate(backend, model=model, system=system, prompt=prompt, schema=SUB_LEAD_SCHEMA)
    except Exception as error:
        result.requests, result.fallback = forwarded, "forwarded-unchanged"
        result.error = f"{type(error).__name__}: {str(error)[:300]}"
        result.wall_seconds = round(time.monotonic() - started, 1)
        return result
    result.tokens = outcome.tokens
    result.served_models = list(outcome.served_models)
    result.served_primary = list(outcome.served_models[:1])
    if isinstance(outcome, InvalidAfterRetry):
        result.requests, result.fallback, result.error = forwarded, "forwarded-unchanged", outcome.error
    else:
        result.requests = [{**item, "value": str(item["value"]).strip()} for item in outcome.payload["requests"]]
    result.wall_seconds = round(time.monotonic() - started, 1)
    return result


def run_sub_leads(cut: str, *, backend: Backend, model: str, hub_text: str, base: Path, workers: list[WorkerResult]) -> list[SubLeadResult]:
    groups = CUTS[cut]
    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        futures = [
            pool.submit(run_sub_lead, group, services, backend=backend, model=model, hub_text=hub_text, base=base, workers=workers)
            for group, services in groups.items()
        ]
        return [future.result() for future in futures]


def worker_requests_of(workers: list[WorkerResult]) -> list[dict]:
    """Every worker request with its harm list; inside means the harmed service is the worker's own."""
    return [
        {"from": worker.service, "services": [worker.service], **request, **classify(request["key"], request["value"], [worker.service])}
        for worker in workers
        for request in worker.hub_requests
    ]


def requests_up_of(arm: str, workers: list[WorkerResult], sub_leads: list[SubLeadResult]) -> list[dict]:
    """What reaches the platform lead: every worker request in flat, else every sub-lead request, each with its harm list."""
    if arm == "flat":
        return worker_requests_of(workers)
    return [
        {"from": lead.group, "services": lead.services, **request, **classify(request["key"], request["value"], lead.services)}
        for lead in sub_leads
        for request in lead.requests
    ]


def run_top(arm: str, *, backend: Backend, model: str, hub_text: str, requests_up: list[dict], cut: str | None) -> TopResult:
    """One platform-lead call; none when no request reaches it, and the hub then stays as it is."""
    if not requests_up:
        return TopResult()
    started = time.monotonic()
    groups = CUTS[cut] if cut else {}
    prompt = top_prompt(hub_text, requests_up, groups, needs=arm in NEEDS_ARMS)
    system = top_system(tiered=cut is not None, needs=arm in NEEDS_ARMS)
    result = TopResult(called=True, prompt_chars=len(prompt))
    try:
        outcome = call_and_validate(backend, model=model, system=system, prompt=prompt, schema=TOP_SCHEMA)
    except Exception as error:
        result.error = f"{type(error).__name__}: {str(error)[:300]}"
        result.wall_seconds = round(time.monotonic() - started, 1)
        return result
    result.tokens = outcome.tokens
    result.served_models = list(outcome.served_models)
    result.served_primary = list(outcome.served_models[:1])
    if isinstance(outcome, InvalidAfterRetry):
        result.error = outcome.error
    else:
        result.decisions = {item["key"]: {"value": item["value"].strip(), "reason": item["reason"]} for item in outcome.payload["hub"]}
        result.grants = [{**item, "by": "platform-lead"} for item in outcome.payload.get("overrides", [])]
    result.wall_seconds = round(time.monotonic() - started, 1)
    return result


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


def resolve(*, workers: list[WorkerResult], top: TopResult, hub_text: str, base_hub: dict[str, str]) -> dict:
    """Apply worker files, override grants and decided hub values to a fresh copy of the world, then score it."""
    with tempfile.TemporaryDirectory(prefix="hier-") as scratch:
        repo = scratch_copy(Path(scratch) / "repo")
        for worker in workers:
            for path, text in worker.files.items():
                (repo / path).write_text(text, encoding="utf-8")
        grant_errors = []
        for grant in top.grants:
            path = repo / grant["service"] / "service.yaml"
            try:
                path.write_text(grant_override(path.read_text(encoding="utf-8"), grant["key"], grant["value"]), encoding="utf-8")
            except Exception as error:  # a worker may have left a service.yaml that no longer parses
                grant_errors.append({**grant, "error": f"{type(error).__name__}: {str(error)[:200]}"})
        decided = {key: item["value"] for key, item in top.decisions.items() if item["value"] != base_hub[key]}
        (repo / HUB_FILE).write_text(set_hub_values(hub_text, decided), encoding="utf-8")
        final_hub = parse_hub((repo / HUB_FILE).read_text(encoding="utf-8"))
        timeouts_error = ""
        try:
            timeouts = effective_timeouts(repo, SERVICES, final_hub["http.default_timeout"])
        except (OutsideContract, NotFixed, ValueError) as error:  # a hub timeout that is not a duration
            timeouts, timeouts_error = {}, f"{type(error).__name__}: {str(error)[:200]}"
        overrides_in_merged = service_overrides(repo, SERVICES)
        result = check(repo)
    return {
        "final_hub": final_hub,
        "overrides_granted": top.grants,
        "grant_errors": grant_errors,
        "overrides_in_merged": overrides_in_merged,
        "effective_timeouts": timeouts,
        "timeouts_error": timeouts_error,
        "distinct_timeouts": len(set(timeouts.values())),
        "score": score_record(result),
    }


def rotated(arms: list[str], index: int) -> list[str]:
    shift = index % len(arms)
    return arms[shift:] + arms[:shift]


def primary_models(*results) -> list[str]:
    return sorted({model for item in results for model in item.served_primary})


def run_arm(arm: str, *, backend: Backend, lead_model: str, hub_text: str, base_hub: dict[str, str], workers: list[WorkerResult], sub_leads: list[SubLeadResult]) -> dict:
    """The platform-lead call and the resolved, scored repo of one arm."""
    cut = CUT_OF_ARM.get(arm)
    requests_up = requests_up_of(arm, workers, sub_leads)
    top = run_top(arm, backend=backend, model=lead_model, hub_text=hub_text, requests_up=requests_up, cut=cut)
    measured = resolve(workers=workers, top=top, hub_text=hub_text, base_hub=base_hub)
    worker_requests = worker_requests_of(workers)
    ladder = coupling_ladder(worker_requests=worker_requests, requests_up=requests_up, regressed=measured["score"]["regressed"], tiered=cut is not None)
    harmful_up = [harm for request in requests_up for harm in request["harms"]]
    sub_lead_tokens, worker_tokens = sum(lead.tokens for lead in sub_leads), sum(worker.tokens for worker in workers)
    return {
        "arm": arm,
        "sub_leads": [asdict(lead) for lead in sub_leads],
        "sub_leads_shared_with": CUT_OF_ARM[arm] if arm in REGISTRY_ARMS else "",
        "worker_requests": worker_requests,
        "requests_up": requests_up,
        "top": asdict(top),
        **measured,
        "escalations": len(requests_up),
        "harmful_up_inside": sum(1 for harm in harmful_up if harm["inside"]),
        "harmful_up_outside": sum(1 for harm in harmful_up if not harm["inside"]),
        "harmful_worker_requests": sum(1 for request in worker_requests if request["harms"]),
        "ladder": ladder,
        "tokens_by_tier": {"workers": worker_tokens, "sub_leads": sub_lead_tokens, "top": top.tokens},
        "management_tokens": sub_lead_tokens + top.tokens,
        "total_tokens": worker_tokens + sub_lead_tokens + top.tokens,
        "top_prompt_chars": top.prompt_chars,
        "lead_served_primary": primary_models(*sub_leads, top),
        "wall_seconds_management": round(sum(lead.wall_seconds for lead in sub_leads) + top.wall_seconds, 1),
    }


def run_pair(*, run: int, backend: Backend, backend_name: str, worker_model: str = WORKER_MODEL, lead_model: str = LEAD_MODEL, arms: list[str] | tuple[str, ...] = ARMS, parallel_arms: bool = False) -> list[dict]:
    """One run index: workers once, sub-leads once per cut needed, then one platform-lead call and one scored repo per arm."""
    unknown = [arm for arm in arms if arm not in ARMS]
    if unknown:
        raise ValueError(f"unknown arms {unknown}: use {ARMS}")
    order = rotated(list(arms), run - 1)
    with tempfile.TemporaryDirectory(prefix="hier-base-") as scratch:
        base = scratch_copy(Path(scratch) / "base")
        hub_text = (base / HUB_FILE).read_text(encoding="utf-8")
        base_hub = parse_hub(hub_text)
        started = time.monotonic()
        workers = run_workers(backend=backend, model=worker_model, base=base)
        workers_seconds = round(time.monotonic() - started, 1)
        sub_leads = {}
        for cut in dict.fromkeys(CUT_OF_ARM[arm] for arm in order if arm in CUT_OF_ARM):
            sub_leads[cut] = run_sub_leads(cut, backend=backend, model=lead_model, hub_text=hub_text, base=base, workers=workers)

    def one(arm: str) -> dict:
        record = run_arm(arm, backend=backend, lead_model=lead_model, hub_text=hub_text, base_hub=base_hub, workers=workers, sub_leads=sub_leads.get(CUT_OF_ARM.get(arm), []))
        return {
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "run": run,
            "backend": backend_name,
            "worker_model": worker_model,
            "lead_model": lead_model,
            "code_commit": code_commit(),
            "workers": [asdict(worker) for worker in workers],
            "worker_served_primary": primary_models(*workers),
            "wall_seconds_workers": workers_seconds,
            **record,
        }

    if parallel_arms:
        with ThreadPoolExecutor(max_workers=len(order)) as pool:
            return list(pool.map(one, order))
    return [one(arm) for arm in order]
