"""The 02 experiment: the run schedule, one JSONL row per run, the summary table and the pre-registration guard."""

import dataclasses
import datetime as dt
import json
import os
import statistics
from pathlib import Path

from ckpt_faults import FAULT_KINDS, FAULT_TARGETS, Fault
from ckpt_pipeline import ARMS, PipelineRecord, load_world, run_pipeline
from ckpt_scoring import classify_rejection, false_rejection, fault_outcome, owner_mismatch, score_plan
from topologies.guards import describe_skipped
from topologies.model import backend_from_name

HERE = Path(__file__).parent
PREDICTIONS = HERE.parent / "PREDICTIONS.md"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_CEILING = 400_000


def assert_preregistered(predictions: Path = PREDICTIONS) -> None:
    """Refuse benchmark runs while the predictions are still a draft."""
    if "DRAFT" in predictions.read_text():
        raise SystemExit(f"{predictions} still says DRAFT: get the predictions approved and committed before any benchmark run.")


def fault_for_run(index: int) -> Fault | None:
    """The fault of 0-based run `index`: none on odd runs; kind and target rotate over the faulted ones."""
    if index % 2 == 1:
        return None
    ordinal = index // 2
    return Fault(FAULT_KINDS[ordinal % len(FAULT_KINDS)], FAULT_TARGETS[ordinal % len(FAULT_TARGETS)])


def arms_for_run(arms: list[str], index: int) -> list[str]:
    shift = index % len(arms)
    return arms[shift:] + arms[:shift]


def run_row(record: PipelineRecord, fault: Fault | None, *, run: int, backend: str, model: str) -> dict:
    served = sorted({name for stage in record.stages for name in stage.served_models} | {name for check in record.checkpoints for name in check.served_models})
    return {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "arm": record.arm,
        "run": run,
        "backend": backend,
        "model": model,
        "served_models": served,
        "fault": dataclasses.asdict(fault) if fault else None,
        "injection": dataclasses.asdict(record.injection) if record.injection else None,
        "fault_outcome": fault_outcome(record, fault),
        "owner_mismatch": owner_mismatch(record, fault),
        "rejection": classify_rejection(record, fault),
        "false_rejection": false_rejection(record, fault),
        "status": record.status,
        "caught_at_stage": record.caught_at_stage,
        "stage_tokens": record.stage_tokens,
        "gate_tokens": record.gate_tokens,
        "total_tokens": record.total_tokens,
        "wall_seconds": record.wall_seconds,
        "stages": [dataclasses.asdict(stage) for stage in record.stages],
        "checkpoints": [dataclasses.asdict(check) for check in record.checkpoints],
        "plan": record.plan,
        "skipped": describe_skipped(record.skipped),
        "score": score_plan(record.plan) if record.plan is not None else None,
    }


def run_arms(n: int, arms: list[str], out: Path, backend_name: str | None, model: str, ceiling: int | None) -> None:
    backend_name = backend_name or os.environ.get("TOPOLOGIES_BACKEND", "claude-cli")
    backend = backend_from_name(backend_name)
    world = load_world()
    out.parent.mkdir(parents=True, exist_ok=True)
    for index in range(n):
        fault = fault_for_run(index)
        for arm in arms_for_run(arms, index):
            record = run_pipeline(world, backend, arm=arm, model=model, fault=fault, token_ceiling=ceiling)
            row = run_row(record, fault, run=index + 1, backend=backend_name, model=model)
            with out.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            print(
                f"== {arm} run {index + 1} ({f'{fault.kind} {fault.risk_id}' if fault else 'clean'}): {row['status']}, "
                f"fault {row['fault_outcome']}, {row['total_tokens']} tokens",
                flush=True,
            )


def load_rows(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    setups = sorted({f"{row['model']} via {row['backend']}" for row in rows})
    if len(setups) > 1:
        raise ValueError(f"{path} mixes models or backends ({', '.join(setups)}); move the other runs aside first")
    return rows


def summarise(path: Path) -> str:
    """Markdown summary, one row per (arm, model, backend); a file that mixes models or backends is refused."""
    if not path.exists():
        return f"No runs at {path}."
    rows = load_rows(path)
    lines = [
        "| arm | model, backend | runs (faulted / clean) | fault outcomes | tokens, mean (min to max) | gate share of tokens | false rejections (clean runs) | justified rejections (clean runs) | other rejections (faulted runs: justified / false) | recall, mean (runs that reached stage 4) | recall, mean (clean runs that reached stage 4) | wall s, mean |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in ARMS:
        runs = [row for row in rows if row["arm"] == arm]
        if not runs:
            continue
        faulted = [row for row in runs if row["fault"]]
        clean = [row for row in runs if not row["fault"]]
        outcomes = {}
        for row in faulted:
            outcomes[row["fault_outcome"]] = outcomes.get(row["fault_outcome"], 0) + 1
        outcome_text = ", ".join(f"{name} {count}" for name, count in sorted(outcomes.items())) or "none"
        tokens = [row["total_tokens"] for row in runs]
        gate_share = sum(row["gate_tokens"] for row in runs) / sum(tokens) if sum(tokens) else 0
        scored = [row["score"]["recall"] for row in runs if row["score"]]
        recall_text = f"{statistics.mean(scored):.2f} ({len(scored)} of {len(runs)} runs)" if scored else f"n/a (0 of {len(runs)} runs)"
        clean_scored = [row["score"]["recall"] for row in clean if row["score"]]
        clean_recall_text = f"{statistics.mean(clean_scored):.2f} ({len(clean_scored)} of {len(clean)} runs)" if clean_scored else f"n/a (0 of {len(clean)} runs)"
        lines.append(
            f"| {arm} | {runs[0]['model']}, {runs[0]['backend']} | {len(runs)} ({len(faulted)} / {len(clean)}) | {outcome_text} "
            f"| {statistics.mean(tokens):,.0f} ({min(tokens):,} to {max(tokens):,}) "
            f"| {gate_share:.0%} "
            f"| {sum(row['false_rejection'] for row in clean)} of {len(clean)} "
            f"| {sum(row['rejection'] == 'justified' for row in clean)} of {len(clean)} "
            f"| {sum(row['rejection'] == 'justified' for row in faulted)} / {sum(row['rejection'] == 'false' for row in faulted)} "
            f"| {recall_text} "
            f"| {clean_recall_text} "
            f"| {statistics.mean(row['wall_seconds'] for row in runs):.0f} |"
        )
    lines += ["", "Per injected fault (blamed stage is the stage whose checkpoint failed; the owner is always stage 1):", "", "| run | arm | fault | outcome | blamed stage | stopped by |", "|---|---|---|---|---|---|"]
    for row in sorted((r for r in rows if r["fault"]), key=lambda r: (r["run"], list(ARMS).index(r["arm"]))):
        fault = row["fault"]
        failing = [c for c in row["checkpoints"] if c["verdict"] != "pass"]
        reason = f"{failing[0]['check']} ({row['rejection']}): {failing[0]['reason']}" if failing else ""
        blamed = row["caught_at_stage"] if row["caught_at_stage"] is not None else ""
        lines.append(f"| {row['run']} | {row['arm']} | {fault['kind']} {fault['risk_id']} | {row['fault_outcome']} | {blamed} | {reason} |")
    unusual = [f"{row['arm']} run {row['run']}: {row['status']}" for row in rows if row["status"] in ("stage_failed", "checkpoint_error", "over_budget")]
    if unusual:
        lines += ["", "Runs that ended without a verdict: " + "; ".join(unusual)]
    served = sorted({name for row in rows for name in row["served_models"]})
    dates = sorted({row["at"][:10] for row in rows})
    lines.append(f"\nServed by {', '.join(served) or 'not reported'}. Run dates: {', '.join(dates)}.")
    return "\n".join(lines)
