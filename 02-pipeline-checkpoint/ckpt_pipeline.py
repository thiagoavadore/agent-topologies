"""Four-stage risk-review pipeline (extract, classify, prioritise, plan) over the Harbour Bikes files, with optional checkpoints.

Each stage sees only the previous stage's output, like a handoff. A checkpoint is a contract check
(every service accounted for) followed by a model gate. A failed checkpoint stops the pipeline: there is no re-run loop.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ckpt_faults import Fault, Injection, inject
from topologies.guards import InvalidAfterRetry, SkippedWork, call_and_validate, may_start_group
from topologies.harbour import FIXTURE, SERVICES, world_files
from topologies.model import Backend

CATEGORIES = {
    "hardcoded_secret": "a credential, key or password written into code or config",
    "missing_timeout": "an outbound network call that can wait forever, or whose timeout is below what the call needs",
    "single_point_of_failure": "one instance, host or broker whose loss stops the service",
    "no_owner": "no team or person accountable for the service",
    "unpinned_dependency": "a dependency version that can change between builds",
    "missing_backup": "data that would be lost for good if its disk or host died",
    "public_exposure": "an internal surface reachable from the internet without strong auth",
    "no_rate_limit": "a public endpoint with no per-client request limit",
}

# Checkpoint arms: the stages after which a checkpoint runs.
ARMS = {
    "none": (),
    "end-only": (4,),
    "every-handoff": (1, 2, 3, 4),
}

STAGE_NAMES = {1: "extract", 2: "classify", 3: "prioritise", 4: "plan"}
STAGE_TASKS = {
    1: "Extract the operational facts of every file, each attributed to its service, file and locator.",
    2: "Classify each extracted fact that shows a risk into one risk category, keeping service, file and locator.",
    3: "Rank every classified risk once by urgency, keeping service, category, file and locator.",
    4: "Write one remediation action per ranked risk, keeping service, category, file and locator.",
}
# The key under which each stage lists its per-item results.
ITEMS_KEY = {1: "facts", 2: "risks", 3: "ranked", 4: "plan"}

LOCATOR_HELP = (
    "locator = where in the file the fact sits. In code: `<function>(): <call>(<keyword args>)`, e.g. `refresh(): session.get(url)`. "
    "In YAML, JSON, TOML and similar: the dotted key path, e.g. `data.store`. "
    "In a dependency list: `every requirement` or the package name."
)

STAGE_SYSTEMS = {
    1: f"""You are stage 1 (extract) of a platform risk review pipeline.
You get the files of a small platform repo: the shared platform files and eight services (one directory each).
Every file must appear in at least one fact. List the facts that matter for operational risk or its absence: deployment shape, instances and zones, dependencies and how they are pinned,
outbound calls and their timeouts, secrets and where they come from, network rules, public endpoints and their limits, where data lives and how it is backed up, who owns the service, and which platform defaults each service inherits.
Facts about the shared files (platform.yaml, teams.yaml, libs/) use service "platform". {LOCATOR_HELP}
Reply with JSON only, no prose, matching:
{{"facts": [{{"service": "<service name or platform>", "file": "<path as given>", "locator": "<locator>", "fact": "<one sentence>"}}]}}""",
    2: """You are stage 2 (classify) of a platform risk review pipeline.
You see only the extracted facts, not the files. Turn each fact that shows a risk into one risk, using one category from this list:
{categories}
Use only the facts you are given and keep each fact's file and locator.
The services are exactly: {services}. "platform" is not a service: a risk resting on a platform fact belongs to the service it affects, and "platform" never appears in "risks" or "no_risk_services".
List services with no risk in "no_risk_services". Every service must appear in "risks" or in "no_risk_services".
Reply with JSON only, no prose, matching:
{{"risks": [{{"service": "<service>", "category": "<category>", "file": "<path>", "locator": "<locator>", "fact": "<the fact it rests on>"}}], "no_risk_services": ["<service>"]}}""",
    3: """You are stage 3 (prioritise) of a platform risk review pipeline.
You see only the classified risks. Rank every risk once: priority 1 is the most urgent, with a one-sentence reason. Keep each risk's service, category, file and locator.
Carry "no_risk_services" over unchanged. Every service must appear in "ranked" or in "no_risk_services".
Reply with JSON only, no prose, matching:
{"ranked": [{"service": "<service>", "category": "<category>", "file": "<path>", "locator": "<locator>", "priority": 1, "reason": "<one sentence>"}], "no_risk_services": ["<service>"]}""",
    4: """You are stage 4 (remediation plan) of a platform risk review pipeline.
You see only the ranked risks. Write one remediation action per ranked risk, in priority order. Keep each risk's service, category, file and locator.
Carry "no_risk_services" over unchanged. Every service must appear in "plan" or in "no_risk_services".
Reply with JSON only, no prose, matching:
{"plan": [{"service": "<service>", "category": "<category>", "file": "<path>", "locator": "<locator>", "priority": 1, "action": "<one or two sentences>"}], "no_risk_services": ["<service>"]}""",
}

GATE_SYSTEM = """You are a quality gate between two stages of a platform risk review pipeline.
You get the stage's task, its input and its output. Decide whether the output is faithful to the input:
every item attributed to the right service (the service that owns the cited file), nothing that bears on operational risk dropped, nothing invented or reworded into a different claim.
Reply with JSON only, no prose, matching:
{"verdict": "pass" or "fail", "reason": "<one sentence>"}"""

GATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "reason"],
    "properties": {"verdict": {"enum": ["pass", "fail"]}, "reason": {"type": "string"}},
}


def stage_schema(stage: int, files: list[str]) -> dict:
    """Strict JSON contract for one stage's output."""
    service = {"enum": [*SERVICES, "platform"] if stage == 1 else list(SERVICES)}
    where = {"file": {"enum": files}, "locator": {"type": "string"}}
    if stage == 1:
        item_props = {"service": service, **where, "fact": {"type": "string"}}
    else:
        item_props = {"service": service, "category": {"enum": list(CATEGORIES)}, **where}
        if stage == 2:
            item_props["fact"] = {"type": "string"}
        elif stage == 3:
            item_props |= {"priority": {"type": "integer", "minimum": 1}, "reason": {"type": "string"}}
        else:
            item_props |= {"priority": {"type": "integer", "minimum": 1}, "action": {"type": "string"}}
    items = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": list(item_props), "properties": item_props}}
    properties, required = {ITEMS_KEY[stage]: items}, [ITEMS_KEY[stage]]
    if stage > 1:
        properties["no_risk_services"] = {"type": "array", "items": {"enum": list(SERVICES)}}
        required.append("no_risk_services")
    return {"type": "object", "additionalProperties": False, "required": required, "properties": properties}


def stage_system(stage: int) -> str:
    system = STAGE_SYSTEMS[stage]
    if stage != 2:
        return system
    return system.format(categories="\n".join(f"- {name}: {meaning}" for name, meaning in CATEGORIES.items()), services=", ".join(SERVICES))


def unaccounted_services(stage: int, payload: dict) -> list[str]:
    """Services that appear neither in the stage's items nor in `no_risk_services`."""
    seen = {item["service"] for item in payload[ITEMS_KEY[stage]]} | set(payload.get("no_risk_services", []))
    return [service for service in SERVICES if service not in seen]


def uncovered_files(payload: dict, files: list[str]) -> list[str]:
    """Files no stage-1 fact cites."""
    cited = {fact["file"] for fact in payload["facts"]}
    return [path for path in files if path not in cited]


def load_world(root: Path = FIXTURE) -> dict[str, str]:
    """The fixture's files by relative path, sorted. Read-only: nothing in this folder writes to them."""
    return {path: (root / path).read_text() for path in sorted(world_files(root))}


def render_files(world: dict[str, str]) -> str:
    return "\n\n".join(f"=== FILE: {path} ===\n{text}" for path, text in world.items())


@dataclass
class StageResult:
    stage: int
    name: str
    status: str  # ok | invalid | error
    attempts: int = 0
    tokens: int = 0
    served_models: list[str] = field(default_factory=list)
    wall_seconds: float = 0.0
    output: dict | None = None
    error: str = ""


@dataclass
class CheckpointResult:
    after_stage: int
    check: str  # contract | gate
    verdict: str  # pass | fail | error
    reason: str
    tokens: int = 0
    served_models: list[str] = field(default_factory=list)
    wall_seconds: float = 0.0


@dataclass
class PipelineRecord:
    arm: str
    status: str  # completed | stopped_at_checkpoint | checkpoint_error | stage_failed | over_budget
    caught_at_stage: int | None  # the stage whose output the failing checkpoint judged: the stage that gets the blame
    injection: Injection | None
    stages: list[StageResult]
    checkpoints: list[CheckpointResult]
    plan: list[dict] | None
    skipped: list[SkippedWork]
    stage_tokens: int
    gate_tokens: int
    wall_seconds: float

    @property
    def total_tokens(self) -> int:
        return self.stage_tokens + self.gate_tokens


def run_checkpoint(backend: Backend, model: str, stage: int, stage_input: str, output: dict) -> CheckpointResult:
    """Contract check first (free), then the model gate. The first failure ends the checkpoint."""
    missing = unaccounted_services(stage, output)
    if missing:
        return CheckpointResult(stage, "contract", "fail", f"services not accounted for: {', '.join(missing)}")
    started = time.monotonic()
    prompt = (
        f"Stage {stage} ({STAGE_NAMES[stage]}) task: {STAGE_TASKS[stage]}\n\n"
        f"=== STAGE INPUT ===\n{stage_input}\n\n=== STAGE OUTPUT ===\n{json.dumps(output, indent=1)}"
    )
    try:
        gate = call_and_validate(backend, model=model, system=GATE_SYSTEM, prompt=prompt, schema=GATE_SCHEMA)
    except Exception as error:  # backend failure: a gate that did not run is neither a pass nor a catch
        return CheckpointResult(stage, "gate", "error", f"{type(error).__name__}: {error}"[:300])
    wall = round(time.monotonic() - started, 1)
    if isinstance(gate, InvalidAfterRetry):
        return CheckpointResult(stage, "gate", "error", f"gate reply broke the contract: {gate.error}", gate.tokens, list(gate.served_models), wall)
    return CheckpointResult(stage, "gate", gate.payload["verdict"], gate.payload["reason"], gate.tokens, list(gate.served_models), wall)


def run_stage_one(backend: Backend, model: str, prompt: str, files: list[str]):
    """Stage 1 with one more retry if some file is not cited by any fact: injection needs every file covered."""
    schema = stage_schema(1, files)
    result = call_and_validate(backend, model=model, system=stage_system(1), prompt=prompt, schema=schema)
    if isinstance(result, InvalidAfterRetry):
        return result
    gaps = uncovered_files(result.payload, files)
    if not gaps:
        return result
    again = call_and_validate(
        backend, model=model, system=stage_system(1),
        prompt=f"{prompt}\n\nYour previous reply left these files without a fact: {', '.join(gaps)}.\nReply again with facts for every file.",
        schema=schema,
    )
    tokens, served = result.tokens + again.tokens, tuple(dict.fromkeys(result.served_models + again.served_models))
    if isinstance(again, InvalidAfterRetry):
        return InvalidAfterRetry(again.error, result.attempts + again.attempts, tokens, served)
    gaps = uncovered_files(again.payload, files)
    if gaps:
        return InvalidAfterRetry(f"files without a fact after retry: {', '.join(gaps)}", result.attempts + again.attempts, tokens, served)
    return type(again)(again.payload, result.attempts + again.attempts, tokens, served)


def run_pipeline(
    world: dict[str, str],
    backend: Backend,
    *,
    arm: str,
    model: str,
    fault: Fault | None = None,
    token_ceiling: int | None = None,
) -> PipelineRecord:
    """Run the four stages; inject `fault` into the stage-1 output; stop at the first failed checkpoint."""
    checkpoint_after = ARMS[arm]
    files = list(world)
    started = time.monotonic()
    stages: list[StageResult] = []
    checkpoints: list[CheckpointResult] = []
    skipped: list[SkippedWork] = []
    injection: Injection | None = None
    stage_input = render_files(world)
    status, caught_at, plan = "completed", None, None

    for number in range(1, 5):
        spent = sum(s.tokens for s in stages) + sum(c.tokens for c in checkpoints)
        if not may_start_group(spent, token_ceiling):
            skipped = [SkippedWork(f"stage {n} ({STAGE_NAMES[n]})", f"token ceiling {token_ceiling} reached") for n in range(number, 5)]
            status = "over_budget"
            break

        stage_started = time.monotonic()
        try:
            if number == 1:
                result = run_stage_one(backend, model, stage_input, files)
            else:
                result = call_and_validate(backend, model=model, system=stage_system(number), prompt=stage_input, schema=stage_schema(number, files))
        except Exception as error:  # backend failure: stop, the stage did not produce anything
            stages.append(StageResult(number, STAGE_NAMES[number], "error", error=f"{type(error).__name__}: {error}"[:300]))
            status = "stage_failed"
            break
        wall = round(time.monotonic() - stage_started, 1)
        if isinstance(result, InvalidAfterRetry):
            stages.append(StageResult(number, STAGE_NAMES[number], "invalid", result.attempts, result.tokens, list(result.served_models), wall, error=result.error))
            status = "stage_failed"
            break
        output = result.payload
        if number == 1 and fault is not None:
            output, injection = inject(output, fault)
        stages.append(StageResult(number, STAGE_NAMES[number], "ok", result.attempts, result.tokens, list(result.served_models), wall, output))

        if number in checkpoint_after:
            checkpoint = run_checkpoint(backend, model, number, stage_input, output)
            checkpoints.append(checkpoint)
            if checkpoint.verdict != "pass":
                status = "stopped_at_checkpoint" if checkpoint.verdict == "fail" else "checkpoint_error"
                caught_at = number if checkpoint.verdict == "fail" else None
                break
        stage_input = json.dumps(output, indent=1)
        if number == 4:
            plan = output["plan"]

    return PipelineRecord(
        arm=arm,
        status=status,
        caught_at_stage=caught_at,
        injection=injection,
        stages=stages,
        checkpoints=checkpoints,
        plan=plan,
        skipped=skipped,
        stage_tokens=sum(s.tokens for s in stages),
        gate_tokens=sum(c.tokens for c in checkpoints),
        wall_seconds=round(time.monotonic() - started, 1),
    )
