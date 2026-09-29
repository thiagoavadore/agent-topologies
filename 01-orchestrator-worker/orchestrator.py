"""Orchestrator-worker risk review: the lead plans (router call), workers review, the lead summarises (synthesis call).

Safety checks: strict JSON format, worker cap (fan-out cap), token budget (token ceiling), fallback plan.
"""

import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import jsonschema

from topologies.model import Backend

# The worker contract. Scoring reads these same keys, so this dict is their only home.
CATEGORIES = {
    "hardcoded_secret": "a credential, key or password written into code or config",
    "missing_timeout": "an outbound network call that can wait forever",
    "single_point_of_failure": "one instance, host or broker whose loss stops the service",
    "no_owner": "no team or person accountable for the service",
    "unpinned_dependency": "a dependency version that can change between builds",
    "missing_backup": "data that would be lost for good if its disk or host died",
    "public_exposure": "an internal surface reachable from the internet without strong auth",
    "no_rate_limit": "a public endpoint with no per-client request limit",
}

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subtasks"],
    "properties": {
        "subtasks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "files", "focus"],
                "properties": {
                    "id": {"type": "string"},
                    "files": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "focus": {"type": "string"},
                },
            },
        }
    },
}

FINDINGS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["file", "category", "severity", "evidence"],
                "properties": {
                    "file": {"type": "string"},
                    "category": {"enum": list(CATEGORIES)},
                    "severity": {"enum": ["low", "medium", "high"]},
                    "evidence": {"type": "string", "maxLength": 300},
                },
            },
        }
    },
}

ROUTER_SYSTEM = """You are the lead of a platform risk review. You never review files yourself.
You split the review across workers: decide how many workers to use and which service cards each one reviews.
Every card must be assigned to exactly one worker.
Reply with JSON only, no prose, matching:
{"subtasks": [{"id": "w1", "files": ["<card file name>", ...], "focus": "<one line: what to look for>"}]}"""

WORKER_SYSTEM = """You review service cards for operational risk. Review only the cards you are given.
Report each risk you find with one category from this list:
{categories}
Quote the line that shows the risk as evidence (at most 300 characters).
Reply with JSON only, no prose, matching:
{{"findings": [{{"file": "<card file name>", "category": "<category>", "severity": "low|medium|high", "evidence": "<quote>"}}]}}"""

SYNTHESIS_SYSTEM = """You are the lead of a platform risk review, writing for the CTO.
Summarise the validated findings in at most 150 words: the highest-severity risks first, each with its service.
Name any service that was not reviewed, and say why. Do not add risks that are not in the findings."""

FALLBACK_WORKERS = 3  # used only when the router fails and no cap is set
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass
class Guards:
    fan_out_cap: int | None = 3  # max workers; None means no limit
    token_ceiling: int | None = 60_000  # token budget per review; None means no limit
    max_parallel: int = 4  # workers running at once, one "wave"

    def __post_init__(self) -> None:
        if self.fan_out_cap is not None and self.fan_out_cap < 1:
            raise ValueError(f"fan_out_cap must be at least 1 or None, got {self.fan_out_cap}")
        if self.max_parallel < 1:
            raise ValueError(f"max_parallel must be at least 1, got {self.max_parallel}")


@dataclass
class Subtask:
    id: str
    files: list[str]
    focus: str


@dataclass
class WorkerResult:
    subtask_id: str
    files: list[str]
    focus: str
    status: str  # ok | invalid | error | skipped_ceiling
    attempts: int = 0
    tokens: int = 0
    findings: list[dict] = field(default_factory=list)
    out_of_brief: int = 0  # findings about cards this worker wasn't given, dropped
    error: str = ""


@dataclass
class RunRecord:
    router_status: str  # ok | failed_fallback
    router_error: str
    planned_subtasks: int
    dispatched_workers: int
    cap_applied: bool
    ceiling_hit: bool
    tokens_by_role: dict
    total_tokens: int
    uncovered_files: list[str]
    findings: list[dict]
    workers: list[dict]
    summary: str


class RouterFailure(Exception):
    pass


class TokenLedger:
    """Thread-safe running token total, because parallel workers finish at the same time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.by_role: dict[str, int] = {"router": 0, "worker": 0, "synthesis": 0}

    def add(self, role: str, tokens: int) -> None:
        with self._lock:
            self.by_role[role] += tokens

    @property
    def total(self) -> int:
        with self._lock:
            return sum(self.by_role.values())


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def load_cards(services_dir: Path) -> dict[str, str]:
    cards = {path.name: path.read_text() for path in sorted(services_dir.glob("*.md"))}
    if not cards:
        raise ValueError(f"no service cards (*.md) in {services_dir}")
    return cards


def extract_json(text: str) -> dict:
    """Parse a JSON object from a reply, tolerating a ```json fence around it."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(candidate)


def describe_card(text: str) -> str:
    """Return the card's 'What it does' paragraph, the only part the lead sees when planning."""
    match = re.search(r"## What it does\s*\n(.+?)(?:\n\n|\Z)", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.splitlines()[0] if text.strip() else "(empty card)"


def route(backend: Backend, model: str, cards: dict[str, str], ledger: TokenLedger) -> list[Subtask]:
    """Ask the lead for a plan; raise RouterFailure if the call fails or the plan is unusable."""
    listing = "\n".join(f"- {name}: {describe_card(text)}" for name, text in cards.items())
    try:
        reply = backend.call(model=model, system=ROUTER_SYSTEM, prompt=f"Service cards to review:\n{listing}")
    except Exception as error:
        raise RouterFailure(f"router call failed: {error}") from error
    ledger.add("router", reply.total_tokens)
    try:
        plan = extract_json(reply.text)
        jsonschema.validate(plan, PLAN_SCHEMA)
    except (ValueError, jsonschema.ValidationError) as error:
        raise RouterFailure(f"router plan rejected: {str(error)[:200]}") from error

    subtasks = [Subtask(**item) for item in plan["subtasks"]]
    assigned = [name for subtask in subtasks for name in subtask.files]
    unknown = sorted(set(assigned) - set(cards))
    duplicated = sorted({name for name in assigned if assigned.count(name) > 1})
    if unknown or duplicated:
        raise RouterFailure(f"router plan rejected: unknown={unknown} duplicated={duplicated}")
    missing = [name for name in cards if name not in assigned]
    if missing:
        log(f"[supervisor] router left {len(missing)} card(s) unassigned, adding a coverage subtask")
        subtasks.append(Subtask(id="coverage", files=missing, focus="all categories"))
    return subtasks


def fallback_plan(file_names: list[str], workers: int) -> list[Subtask]:
    """Deterministic split: sorted cards in contiguous chunks, so a failed router never blocks the review."""
    names = sorted(file_names)
    workers = max(1, min(workers, len(names)))
    size, remainder = divmod(len(names), workers)
    subtasks, start = [], 0
    for index in range(workers):
        end = start + size + (1 if index < remainder else 0)
        subtasks.append(Subtask(id=f"fallback-{index + 1}", files=names[start:end], focus="all categories"))
        start = end
    return subtasks


def apply_fan_out_cap(subtasks: list[Subtask], cap: int) -> list[Subtask]:
    """Merge the router's subtasks round-robin into `cap` workers; no card is dropped."""
    merged = [Subtask(id=f"capped-{index + 1}", files=[], focus="") for index in range(cap)]
    for index, subtask in enumerate(subtasks):
        target = merged[index % cap]
        target.files.extend(subtask.files)
        target.focus = "; ".join(part for part in (target.focus, subtask.focus) if part)
    return merged


def worker_prompt(subtask: Subtask, cards: dict[str, str]) -> str:
    bodies = "\n\n".join(f"=== FILE: {name} ===\n{cards[name]}" for name in subtask.files)
    return f"Focus: {subtask.focus}\n\n{bodies}"


def run_worker(
    backend: Backend,
    model: str,
    subtask: Subtask,
    cards: dict[str, str],
    ledger: TokenLedger,
) -> WorkerResult:
    """Review one subtask, retrying once if the reply breaks FINDINGS_SCHEMA."""
    system = WORKER_SYSTEM.format(categories="\n".join(f"- {key}: {text}" for key, text in CATEGORIES.items()))
    prompt = worker_prompt(subtask, cards)
    result = WorkerResult(subtask_id=subtask.id, files=subtask.files, focus=subtask.focus, status="invalid")
    for attempt in (1, 2):
        result.attempts = attempt
        try:
            reply = backend.call(model=model, system=system, prompt=prompt)
        except Exception as error:
            result.status, result.error = "error", str(error)[:300]
            return result
        ledger.add("worker", reply.total_tokens)
        result.tokens += reply.total_tokens
        try:
            payload = extract_json(reply.text)
            jsonschema.validate(payload, FINDINGS_SCHEMA)
        except (ValueError, jsonschema.ValidationError) as error:
            result.error = str(error)[:300]
            prompt = (
                f"{worker_prompt(subtask, cards)}\n\n"
                f"Your previous reply broke the contract: {result.error}\n"
                "Reply again with valid JSON only."
            )
            continue
        in_brief = [finding for finding in payload["findings"] if finding["file"] in subtask.files]
        result.out_of_brief = len(payload["findings"]) - len(in_brief)
        result.findings, result.status, result.error = in_brief, "ok", ""
        return result
    return result


def dispatch(
    backend: Backend,
    model: str,
    subtasks: list[Subtask],
    cards: dict[str, str],
    guards: Guards,
    ledger: TokenLedger,
) -> tuple[list[WorkerResult], bool]:
    """Run workers in waves of `max_parallel`, checking the token ceiling before each wave.

    The ceiling is soft: a wave that starts under it can finish over it.
    """
    results, ceiling_hit = [], False
    for start in range(0, len(subtasks), guards.max_parallel):
        wave = subtasks[start: start + guards.max_parallel]
        if guards.token_ceiling is not None and ledger.total >= guards.token_ceiling:
            ceiling_hit = True
            log(f"[ceiling] {ledger.total} >= {guards.token_ceiling} tokens, skipping {len(subtasks) - start} subtask(s)")
            results.extend(
                WorkerResult(subtask_id=subtask.id, files=subtask.files, focus=subtask.focus, status="skipped_ceiling")
                for subtask in subtasks[start:]
            )
            break
        with ThreadPoolExecutor(max_workers=len(wave)) as pool:
            wave_results = list(pool.map(lambda s: run_worker(backend, model, s, cards, ledger), wave))
        for result in wave_results:
            note = f", dropped {result.out_of_brief} out-of-brief finding(s)" if result.out_of_brief else ""
            log(
                f"[worker {result.subtask_id}] {result.status}: {len(result.findings)} finding(s) "
                f"on {len(result.files)} card(s), {result.attempts} attempt(s){note}"
            )
        results.extend(wave_results)
    return results, ceiling_hit


def merge_findings(results: list[WorkerResult]) -> list[dict]:
    """Keep one finding per (card, category), at its highest severity."""
    best: dict[tuple[str, str], dict] = {}
    for result in results:
        for finding in result.findings:
            key = (finding["file"], finding["category"])
            if key not in best or SEVERITY_RANK[finding["severity"]] > SEVERITY_RANK[best[key]["severity"]]:
                best[key] = finding
    return sorted(best.values(), key=lambda f: (-SEVERITY_RANK[f["severity"]], f["file"], f["category"]))


def synthesise(
    backend: Backend,
    model: str,
    findings: list[dict],
    uncovered: list[str],
    ceiling_hit: bool,
    guards: Guards,
    ledger: TokenLedger,
) -> str:
    """Ask the lead for the CTO summary, or write a plain one if over budget or the call fails."""
    counts = f"{len(findings)} validated finding(s); not reviewed: {', '.join(uncovered) or 'none'}."
    if ceiling_hit or (guards.token_ceiling is not None and ledger.total >= guards.token_ceiling):
        return f"Token ceiling reached: {counts}"
    prompt = json.dumps({"findings": findings, "not_reviewed": uncovered}, indent=1)
    try:
        reply = backend.call(model=model, system=SYNTHESIS_SYSTEM, prompt=prompt)
    except Exception as error:
        log(f"[supervisor] synthesis failed: {error}")
        return f"Synthesis failed ({str(error)[:200]}): {counts}"
    ledger.add("synthesis", reply.total_tokens)
    return reply.text.strip()


def run_review(
    services_dir: Path,
    backend: Backend,
    supervisor_model: str,
    worker_model: str,
    guards: Guards,
    break_router: bool = False,
) -> RunRecord:
    """Run one full review: plan, cap, dispatch workers, merge findings, summarise."""
    cards = load_cards(services_dir)
    ledger = TokenLedger()

    try:
        if break_router:
            raise RouterFailure("router killed (--break-router)")
        subtasks = route(backend, supervisor_model, cards, ledger)
        router_status, router_error = "ok", ""
        log(f"[supervisor] router planned {len(subtasks)} subtask(s) for {len(cards)} card(s)")
    except RouterFailure as error:
        router_status, router_error = "failed_fallback", str(error)
        subtasks = fallback_plan(list(cards), guards.fan_out_cap or FALLBACK_WORKERS)
        log(f"[supervisor] {error}; deterministic fallback into {len(subtasks)} subtask(s)")

    planned = len(subtasks)
    cap_applied = guards.fan_out_cap is not None and planned > guards.fan_out_cap
    if cap_applied:
        subtasks = apply_fan_out_cap(subtasks, guards.fan_out_cap)
        log(f"[cap] merged {planned} subtasks into {guards.fan_out_cap} workers")

    results, ceiling_hit = dispatch(backend, worker_model, subtasks, cards, guards, ledger)
    findings = merge_findings(results)
    uncovered = sorted(name for result in results if result.status != "ok" for name in result.files)
    summary = synthesise(backend, supervisor_model, findings, uncovered, ceiling_hit, guards, ledger)
    log(f"[supervisor] {len(findings)} validated finding(s), {len(uncovered)} card(s) not reviewed, {ledger.total} tokens")

    return RunRecord(
        router_status=router_status,
        router_error=router_error,
        planned_subtasks=planned,
        dispatched_workers=sum(1 for result in results if result.status != "skipped_ceiling"),
        cap_applied=cap_applied,
        ceiling_hit=ceiling_hit,
        tokens_by_role=dict(ledger.by_role),
        total_tokens=ledger.total,
        uncovered_files=uncovered,
        findings=findings,
        workers=[asdict(result) for result in results],
        summary=summary,
    )
