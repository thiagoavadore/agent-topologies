"""Score a final plan against the catalogue and decide what became of an injected fault."""

from ckpt_faults import Fault, locator_matches
from topologies.harbour import RISKS, Risk

ORIGIN_STAGE = 1  # faults are injected into stage 1, so stage 1 owns every one of them


def grounds(item: dict, risk: Risk) -> bool:
    """A finding counts for a risk only if it names the risk ID and cites its file and locator."""
    return (
        f"{item['service']}.{item['category']}" == risk.id
        and item["file"] == risk.file
        and locator_matches(item["locator"], risk.locator)
    )


def score_plan(plan: list[dict]) -> dict:
    found = sorted(risk.id for risk in RISKS if any(grounds(item, risk) for item in plan))
    missed = sorted(risk.id for risk in RISKS if risk.id not in found)
    return {
        "risks": len(RISKS),
        "grounded": len(found),
        "recall": round(len(found) / len(RISKS), 3),
        "missed": missed,
        "ungrounded_items": sum(1 for item in plan if not any(grounds(item, risk) for risk in RISKS)),
    }


def fault_outcome(record, fault: Fault | None) -> str | None:
    """caught-stage-N, reached-production, absorbed (plan still has the finding) or no-verdict.

    `not-injected` cannot happen: once stage 1 produced output, the injection exists and hit at least one fact.
    """
    if fault is None:
        return None
    stage_one_done = any(stage.stage == 1 and stage.status == "ok" for stage in record.stages)
    if stage_one_done:
        assert record.injection is not None, "stage 1 finished but no fault was injected"
    if record.caught_at_stage is not None:
        return f"caught-stage-{record.caught_at_stage}"
    if record.plan is None:
        return "no-verdict"
    risk = next(risk for risk in RISKS if risk.id == fault.risk_id)
    return "absorbed" if any(grounds(item, risk) for item in record.plan) else "reached-production"


def false_rejection(record, fault: Fault | None) -> bool:
    """A clean run that a checkpoint stopped."""
    return fault is None and record.caught_at_stage is not None


def owner_mismatch(record, fault: Fault | None) -> bool | None:
    """For a caught fault: whether the stage that got the blame is not the stage that owns the fault."""
    if fault is None or record.caught_at_stage is None:
        return None
    return record.caught_at_stage != ORIGIN_STAGE
