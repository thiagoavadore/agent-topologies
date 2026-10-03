"""Four-stage risk-review pipeline (extract, classify, prioritise, plan) with optional checkpoints between stages.

Each stage sees only the previous stage's output, like a handoff. A checkpoint is a contract check
(every card accounted for) followed by a model gate. A failed checkpoint stops the pipeline: there is no re-run loop.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from injection import Fault, Injection, inject
from topologies.guards import InvalidAfterRetry, SkippedWork, call_and_validate, may_start_group
from topologies.model import Backend

# Same eight categories as 01's worker contract (copied: 01 is a script folder, not importable).
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

# Checkpoint arms: the stages after which a checkpoint runs.
ARMS = {
    "none": (),
    "end-only": (4,),
    "every-handoff": (1, 2, 3, 4),
}

STAGE_SYSTEMS = {
    1: """You are stage 1 (extract) of a platform risk review pipeline.
For every service card, list the facts that matter for operational risk: deployment shape, dependencies and how they are pinned,
outbound calls and their timeouts, public endpoints and their limits, where data lives and how it is backed up, who owns the service.
Every card must appear. Quote the supporting line as evidence (at most 200 characters).
Reply with JSON only, no prose, matching:
{"facts": [{"card": "<card file name>", "fact": "<one sentence>", "evidence": "<quote>"}]}""",
    2: """You are stage 2 (classify) of a platform risk review pipeline.
You see only the extracted facts, not the cards. Turn each fact that shows a risk into one risk, using one category from this list:
{categories}
Use only the facts you are given. List cards with no risk in "no_risk_cards". Every card must appear in "risks" or in "no_risk_cards".
Reply with JSON only, no prose, matching:
{{"risks": [{{"card": "<card file name>", "category": "<category>", "fact": "<the fact it rests on>"}}], "no_risk_cards": ["<card file name>"]}}""",
    3: """You are stage 3 (prioritise) of a platform risk review pipeline.
You see only the classified risks. Rank every risk once: priority 1 is the most urgent, with a one-sentence reason.
Carry "no_risk_cards" over unchanged. Every card must appear in "ranked" or in "no_risk_cards".
Reply with JSON only, no prose, matching:
{"ranked": [{"card": "<card file name>", "category": "<category>", "priority": 1, "reason": "<one sentence>"}], "no_risk_cards": ["<card file name>"]}""",
    4: """You are stage 4 (remediation plan) of a platform risk review pipeline.
You see only the ranked risks. Write one remediation action per ranked risk, in priority order.
Carry "no_risk_cards" over unchanged. Every card must appear in "plan" or in "no_risk_cards".
Reply with JSON only, no prose, matching:
{"plan": [{"card": "<card file name>", "category": "<category>", "priority": 1, "action": "<one or two sentences>"}], "no_risk_cards": ["<card file name>"]}""",
}

STAGE_NAMES = {1: "extract", 2: "classify", 3: "prioritise", 4: "plan"}
STAGE_TASKS = {
    1: "Extract the operational facts of every service card, each attributed to its card.",
    2: "Classify each extracted fact that shows a risk into one risk category, keeping its card.",
    3: "Rank every classified risk once by urgency, keeping card and category.",
    4: "Write one remediation action per ranked risk, keeping card and category.",
}
# The key under which each stage lists its per-card items.
ITEMS_KEY = {1: "facts", 2: "risks", 3: "ranked", 4: "plan"}

GATE_SYSTEM = """You are a quality gate between two stages of a platform risk review pipeline.
You get the stage's task, its input and its output. Decide whether the output is faithful to the input:
every item attributed to the right card, nothing that bears on operational risk dropped, nothing invented or reworded into a different claim.
Reply with JSON only, no prose, matching:
{"verdict": "pass" or "fail", "reason": "<one sentence>"}"""

GATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "reason"],
    "properties": {"verdict": {"enum": ["pass", "fail"]}, "reason": {"type": "string"}},
}


def stage_schema(stage: int, cards: list[str]) -> dict:
    """Strict JSON contract for one stage's output."""
    card = {"enum": cards}
    category = {"enum": list(CATEGORIES)}
    if stage == 1:
        item_props = {"card": card, "fact": {"type": "string"}, "evidence": {"type": "string", "maxLength": 300}}
    elif stage == 2:
        item_props = {"card": card, "category": category, "fact": {"type": "string"}}
    elif stage == 3:
        item_props = {"card": card, "category": category, "priority": {"type": "integer", "minimum": 1}, "reason": {"type": "string"}}
    else:
        item_props = {"card": card, "category": category, "priority": {"type": "integer", "minimum": 1}, "action": {"type": "string"}}
    properties = {ITEMS_KEY[stage]: {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": list(item_props), "properties": item_props}}}
    required = [ITEMS_KEY[stage]]
    if stage > 1:
        properties["no_risk_cards"] = {"type": "array", "items": card}
        required.append("no_risk_cards")
    return {"type": "object", "additionalProperties": False, "required": required, "properties": properties}


def stage_system(stage: int) -> str:
    system = STAGE_SYSTEMS[stage]
    return system.format(categories="\n".join(f"- {name}: {meaning}" for name, meaning in CATEGORIES.items())) if stage == 2 else system


def unaccounted_cards(stage: int, payload: dict, cards: list[str]) -> list[str]:
    """Cards that appear neither in the stage's items nor in `no_risk_cards`."""
    seen = {item["card"] for item in payload[ITEMS_KEY[stage]]} | set(payload.get("no_risk_cards", []))
    return [card for card in cards if card not in seen]


def render_cards(cards: dict[str, str]) -> str:
    return "\n\n".join(f"=== FILE: {name} ===\n{text}" for name, text in cards.items())


def load_cards(services: Path) -> dict[str, str]:
    return {path.name: path.read_text() for path in sorted(services.glob("*.md"))}


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


@dataclass
class PipelineRecord:
    arm: str
    status: str  # completed | stopped_at_checkpoint | checkpoint_error | stage_failed | over_budget
    caught_at_stage: int | None  # the stage whose output the failing checkpoint judged
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


def run_checkpoint(backend: Backend, model: str, stage: int, stage_input: str, output: dict, cards: list[str]) -> CheckpointResult:
    """Contract check first (free), then the model gate. The first failure ends the checkpoint."""
    missing = unaccounted_cards(stage, output, cards)
    if missing:
        return CheckpointResult(stage, "contract", "fail", f"cards not accounted for: {', '.join(missing)}")
    prompt = (
        f"Stage {stage} ({STAGE_NAMES[stage]}) task: {STAGE_TASKS[stage]}\n\n"
        f"=== STAGE INPUT ===\n{stage_input}\n\n=== STAGE OUTPUT ===\n{json.dumps(output, indent=1)}"
    )
    try:
        gate = call_and_validate(backend, model=model, system=GATE_SYSTEM, prompt=prompt, schema=GATE_SCHEMA)
    except Exception as error:  # backend failure: a gate that did not run is neither a pass nor a catch
        return CheckpointResult(stage, "gate", "error", f"{type(error).__name__}: {error}"[:300])
    if isinstance(gate, InvalidAfterRetry):
        return CheckpointResult(stage, "gate", "error", f"gate reply broke the contract: {gate.error}", gate.tokens, list(gate.served_models))
    return CheckpointResult(stage, "gate", gate.payload["verdict"], gate.payload["reason"], gate.tokens, list(gate.served_models))


def run_pipeline(
    cards: dict[str, str],
    backend: Backend,
    *,
    arm: str,
    model: str,
    fault: Fault | None = None,
    token_ceiling: int | None = None,
) -> PipelineRecord:
    """Run the four stages; inject `fault` into the stage-1 output; stop at the first failed checkpoint."""
    checkpoint_after = ARMS[arm]
    card_names = list(cards)
    started = time.monotonic()
    stages: list[StageResult] = []
    checkpoints: list[CheckpointResult] = []
    skipped: list[SkippedWork] = []
    injection: Injection | None = None
    stage_input = render_cards(cards)
    status, caught_at, plan = "completed", None, None

    for number in range(1, 5):
        spent = sum(s.tokens for s in stages) + sum(c.tokens for c in checkpoints)
        if not may_start_group(spent, token_ceiling):
            skipped = [SkippedWork(f"stage {n} ({STAGE_NAMES[n]})", f"token ceiling {token_ceiling} reached") for n in range(number, 5)]
            status = "over_budget"
            break

        stage_started = time.monotonic()
        try:
            result = call_and_validate(
                backend, model=model, system=stage_system(number), prompt=stage_input, schema=stage_schema(number, card_names)
            )
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
            output, injection = inject(output, fault, card_names)
        stages.append(StageResult(number, STAGE_NAMES[number], "ok", result.attempts, result.tokens, list(result.served_models), wall, output))

        if number in checkpoint_after:
            checkpoint = run_checkpoint(backend, model, number, stage_input, output, card_names)
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
