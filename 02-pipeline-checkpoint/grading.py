"""Score a plan against the planted risks (matched on card + category) and decide whether an injected fault reached it."""

import json
from pathlib import Path

from injection import Fault, Injection
from pipeline import CATEGORIES, PipelineRecord

PLANTED_PATH = Path(__file__).parent / "fixtures" / "planted.json"


def load_planted(path: Path = PLANTED_PATH) -> set[tuple[str, str]]:
    planted = {(item["file"], item["category"]) for item in json.loads(path.read_text())["planted"]}
    if not planted:
        raise ValueError(f"{path.name} has no planted risks")
    unknown = {category for _, category in planted} - set(CATEGORIES)
    if unknown:
        raise ValueError(f"planted.json uses categories the stage contract does not know: {sorted(unknown)}")
    missing = {file for file, _ in planted if not (path.parent / "services" / file).exists()}
    if missing:
        raise ValueError(f"planted.json names cards that don't exist: {sorted(missing)}")
    return planted


def score_plan(plan: list[dict], planted: set[tuple[str, str]]) -> dict:
    found = {(item["card"], item["category"]) for item in plan}
    return {
        "planted": len(planted),
        "planted_found": len(found & planted),
        "recall": round(len(found & planted) / len(planted), 3),
        "extra_findings": len(found - planted),
        "missed": sorted(f"{card}:{category}" for card, category in planted - found),
    }


def fault_outcome(record: PipelineRecord, fault: Fault | None) -> str | None:
    """What became of the injected fault: caught-stage-N, reached-production, absorbed, not-injected or no-verdict.

    The fault reached production if the final plan lacks an item for the faulted (card, category):
    a dropped fact never becomes an action, and a misattributed one becomes an action for the wrong card.
    """
    if fault is None:
        return None
    injection: Injection | None = record.injection
    if injection is None or not injection.applied:
        return "not-injected"
    if record.caught_at_stage is not None:
        return f"caught-stage-{record.caught_at_stage}"
    if record.plan is None:
        return "no-verdict"
    planned = {(item["card"], item["category"]) for item in record.plan}
    return "absorbed" if (fault.card, fault.category) in planned else "reached-production"


def false_rejection(record: PipelineRecord, fault: Fault | None) -> bool:
    """A clean run that a checkpoint stopped."""
    return fault is None and record.caught_at_stage is not None
